"""Run the golden-set evaluation and gate the build on it.

This is the script CI runs on every pull request. It answers every question in
the golden set, computes the metrics in :mod:`eval.metrics`, compares them with
``eval/thresholds.yaml``, writes a JSON report and a Markdown summary, and exits
non-zero when any threshold is breached.

    python -m eval.run_eval --profile offline
    python -m eval.run_eval --profile full --llm-judge
    python -m eval.run_eval --baseline eval_reports/main.json   # show deltas

Exit codes: ``0`` all thresholds met, ``1`` one or more breached, ``2`` the run
itself failed (bad config, empty index).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from askmydocs.config import load_config
from askmydocs.pipeline import RAGPipeline
from eval.metrics import GoldenItem, ItemResult, aggregate, score_item


def load_golden(path: Path, limit: int | None = None) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(GoldenItem.from_dict(json.loads(line)))
    return items[:limit] if limit else items


# ---------------------------------------------------------------------------
# Optional LLM-as-judge faithfulness
# ---------------------------------------------------------------------------


def judge_faithfulness(pipeline: RAGPipeline, item: GoldenItem, answer) -> float | None:
    """Score grounding with the ``judge`` prompt. Returns None if unavailable.

    A judge failure must never fail the build — it is a supplementary signal, and
    the lexical faithfulness metric is the one under the gate.
    """
    if answer.abstained or not answer.citations:
        return None
    try:
        from askmydocs.generation.llm import AnthropicLLM, GenerationRequest

        prompt = pipeline.prompts.get("judge", "latest")
        cited = {c.chunk_id for c in answer.citations}
        context = "\n\n".join(
            f"[{c.chunk.chunk_id}] {c.chunk.text}"
            for c in answer.retrieved
            if c.chunk.chunk_id in cited
        )
        system, user = prompt.render(question=item.question, answer=answer.text, context=context)
        judge = AnthropicLLM(model="claude-opus-5", effort="low")
        response = judge.generate(
            GenerationRequest(question=item.question, system=system, user=user, sources=[])
        )
        text = response.text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        return float(json.loads(text[start : end + 1]).get("score", 0.0))
    except Exception as exc:  # noqa: BLE001 - judging is best-effort by design
        print(f"    (judge unavailable for {item.id}: {type(exc).__name__}: {exc})")
        return None


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def check_thresholds(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare measured metrics with the configured bounds."""
    checks: list[dict[str, Any]] = []
    for group, group_thresholds in (thresholds or {}).items():
        measured_group = metrics.get(group, {})
        for name, bounds in (group_thresholds or {}).items():
            if name not in measured_group:
                checks.append(
                    {
                        "metric": f"{group}.{name}",
                        "value": None,
                        "bound": bounds,
                        "passed": False,
                        "detail": "metric not produced by this run",
                    }
                )
                continue
            value = measured_group[name]
            passed, detail = True, ""
            if "min" in bounds and value < bounds["min"]:
                passed, detail = False, f"{value} < min {bounds['min']}"
            if "max" in bounds and value > bounds["max"]:
                passed, detail = False, f"{value} > max {bounds['max']}"
            checks.append(
                {
                    "metric": f"{group}.{name}",
                    "value": value,
                    "bound": bounds,
                    "passed": passed,
                    "detail": detail or "ok",
                }
            )
    return checks


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# RAG Evaluation Report",
        "",
        f"- **Profile**: `{report['profile']}`",
        f"- **Generator**: `{report['generator']}`",
        f"- **Embedder**: `{report['embedder']}`",
        f"- **Reranker**: `{report['reranker']}`",
        f"- **Prompt**: `{report['prompt']}` (hash `{report['prompt_hash']}`)",
        f"- **Golden set**: {report['golden_set']} ({metrics['counts']['total']} questions)",
        f"- **Run at**: {report['created_at']}  ·  **Duration**: {report['duration_s']}s",
        "",
        f"## Gate: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        "| Metric | Value | Bound | Result |",
        "|---|---|---|---|",
    ]
    for check in report["checks"]:
        bound = ", ".join(f"{k}={v}" for k, v in check["bound"].items())
        mark = "pass" if check["passed"] else "**FAIL**"
        lines.append(f"| `{check['metric']}` | {_fmt(check['value'])} | {bound} | {mark} |")

    for group in ("retrieval", "answer_quality", "abstention", "operational"):
        lines += [
            "",
            f"## {group.replace('_', ' ').title()}",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for name, value in metrics.get(group, {}).items():
            lines.append(f"| `{name}` | {_fmt(value)} |")

    if metrics.get("abstain_reasons"):
        lines += ["", "## Abstention reasons", "", "| Reason | Count |", "|---|---|"]
        for reason, count in metrics["abstain_reasons"].items():
            lines.append(f"| `{reason}` | {count} |")

    lines += [
        "",
        "## By difficulty",
        "",
        "| Difficulty | n | recall@k | faithfulness | answer F1 | abstained |",
        "|---|---|---|---|---|---|",
    ]
    for name, stats in metrics.get("by_difficulty", {}).items():
        lines.append(
            f"| {name} | {stats['n']} | {stats['recall_at_k']:.3f} | "
            f"{stats['faithfulness']:.3f} | {stats['answer_f1']:.3f} | {stats['abstained']} |"
        )

    failures = [
        r
        for r in report["items"]
        if (r["answerable"] and (r["abstained"] or (r.get("recall_at_k") is False)))
        or (not r["answerable"] and not r["abstained"])
    ]
    if failures:
        lines += [
            "",
            f"## Notable failures ({len(failures)})",
            "",
            "| id | question | issue |",
            "|---|---|---|",
        ]
        for row in failures[:25]:
            if not row["answerable"]:
                issue = "answered an unanswerable question"
            elif row["abstained"]:
                issue = f"false abstention ({row['abstain_reason']})"
            else:
                issue = "correct document not retrieved"
            question = row["question"].replace("|", "\\|")
            lines.append(f"| `{row['id']}` | {question} | {issue} |")

    if report.get("deltas"):
        lines += [
            "",
            "## Change vs baseline",
            "",
            "| Metric | Baseline | Current | Delta |",
            "|---|---|---|---|",
        ]
        for name, delta in report["deltas"].items():
            arrow = "+" if delta["delta"] > 0 else ""
            lines.append(
                f"| `{name}` | {_fmt(delta['baseline'])} | {_fmt(delta['current'])} | "
                f"{arrow}{delta['delta']:.4f} |"
            )

    return "\n".join(lines) + "\n"


def compute_deltas(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in ("retrieval", "answer_quality", "abstention"):
        for name, value in current.get(group, {}).items():
            before = baseline.get(group, {}).get(name)
            if isinstance(value, (int, float)) and isinstance(before, (int, float)):
                out[f"{group}.{name}"] = {
                    "baseline": before,
                    "current": value,
                    "delta": round(value - before, 4),
                }
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set RAG evaluation.")
    parser.add_argument("--golden", type=Path, default=Path("data/golden/golden_set.jsonl"))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--profile", default=None, help="offline | full")
    parser.add_argument("--thresholds", type=Path, default=Path("eval/thresholds.yaml"))
    parser.add_argument("--out", type=Path, default=Path("eval_reports"))
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N rows.")
    parser.add_argument("--llm-judge", action="store_true", help="Add LLM-as-judge faithfulness.")
    parser.add_argument("--baseline", type=Path, default=None, help="Previous report.json to diff.")
    parser.add_argument("--no-gate", action="store_true", help="Report but always exit 0.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(path=args.config, profile=args.profile)
    try:
        pipeline = RAGPipeline.from_config(config)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean failure
        print(f"Failed to build the pipeline: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if pipeline.is_empty():
        print(
            "The index is empty. Run ingestion first:\n"
            f"    askmydocs ingest --profile {config.profile}",
            file=sys.stderr,
        )
        return 2

    items = load_golden(args.golden, args.limit)
    if not items:
        print(f"No golden items found in {args.golden}", file=sys.stderr)
        return 2

    print(
        f"Evaluating {len(items)} questions  ·  profile={config.profile}  ·  "
        f"generator={config.generation.provider}:{config.generation.model}"
    )

    started = time.perf_counter()
    results: list[ItemResult] = []
    for index, item in enumerate(items, start=1):
        answer = pipeline.answer(item.question)
        result = score_item(item, answer)
        if args.llm_judge:
            result.judge_score = judge_faithfulness(pipeline, item, answer)
        results.append(result)
        if not args.quiet:
            state = f"abstain:{result.abstain_reason}" if result.abstained else "answered"
            flag = " "
            if item.answerable and result.abstained or not item.answerable and not result.abstained:
                flag = "!"
            print(f"  {flag} [{index:>3}/{len(items)}] {item.id}  {state:<32} {item.question[:56]}")

    duration = round(time.perf_counter() - started, 2)
    metrics = aggregate(results)

    all_thresholds = yaml.safe_load(args.thresholds.read_text(encoding="utf-8")) or {}
    thresholds = all_thresholds.get(config.profile, {})
    if not thresholds:
        print(f"  (no thresholds configured for profile {config.profile!r}; gate is advisory)")
    checks = check_thresholds(metrics, thresholds)
    passed = all(check["passed"] for check in checks)

    stats = pipeline.stats()
    report: dict[str, Any] = {
        "profile": config.profile,
        "golden_set": str(args.golden),
        "generator": stats["generator"],
        "embedder": stats["embedder"],
        "reranker": stats["reranker"],
        "prompt": stats["prompt"],
        "prompt_hash": stats["prompt_hash"],
        "chunks": stats["chunks"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": duration,
        "metrics": metrics,
        "checks": checks,
        "passed": passed,
        "items": [r.to_dict() for r in results],
    }

    if args.baseline and args.baseline.exists():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["deltas"] = compute_deltas(metrics, baseline.get("metrics", {}))

    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"report-{config.profile}.json"
    md_path = args.out / f"report-{config.profile}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"  {'PASS' if passed else 'FAIL'}  ·  profile={config.profile}  ·  {duration}s")
    print("=" * 72)
    for check in checks:
        mark = "ok  " if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['metric']:<44} {check['detail']}")
    print(f"\n  Reports: {json_path}  {md_path}")

    if args.no_gate:
        return 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
