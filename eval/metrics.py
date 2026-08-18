"""Metrics for the golden-set evaluation.

Grouped into four families, because they fail for different reasons and a single
aggregate score would hide which stage regressed:

* **Retrieval** — did the right document reach the top-k? Isolates the
  embedding / BM25 / fusion / rerank stack from generation.
* **Answer quality** — is the generated text correct and attributable?
  ``expected_sources`` uses *any-of* semantics: each listed document is a
  sufficient citation, so ``correct_source_cited_rate`` asks whether the answer
  cited an acceptable source, while ``citation_precision`` penalises citing
  documents that are not on the list.
* **Abstention** — does the system refuse when it should, and *only* when it
  should? Both directions matter: a system that abstains on everything scores a
  perfect faithfulness and is useless.
* **Operational** — latency and token cost, so a quality win that triples the
  bill is visible.

``faithfulness`` here is the lexical grounding proxy from
:func:`askmydocs.text.overlap_ratio`: the fraction of the answer's content words
present in the passages it cites. It needs no API key, so it runs on every commit.
It cannot detect a claim that reuses source vocabulary to state something the
source does not — for that, ``--llm-judge`` runs the ``judge`` prompt, and
``eval/ragas_eval.py`` offers a third-party cross-check.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from askmydocs.models import Answer
from askmydocs.text import overlap_ratio, token_f1


@dataclass
class GoldenItem:
    """One row of the golden set."""

    id: str
    question: str
    reference_answer: str = ""
    expected_sources: list[str] = field(default_factory=list)
    answerable: bool = True
    category: str = ""
    difficulty: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenItem:
        missing = {"id", "question"} - set(data)
        if missing:
            raise ValueError(f"Golden item missing required fields: {sorted(missing)}")
        return cls(
            id=str(data["id"]),
            question=str(data["question"]),
            reference_answer=str(data.get("reference_answer", "")),
            expected_sources=list(data.get("expected_sources", [])),
            answerable=bool(data.get("answerable", True)),
            category=str(data.get("category", "")),
            difficulty=str(data.get("difficulty", "")),
        )


@dataclass
class ItemResult:
    """Per-item outcome, kept in the report so failures are debuggable."""

    id: str
    question: str
    answerable: bool
    category: str
    difficulty: str
    abstained: bool
    abstain_reason: str
    answer: str
    citations: list[str]
    retrieved_sources: list[str]
    expected_sources: list[str]
    # Retrieval
    hit_at_1: bool | None = None
    recall_at_k: bool | None = None
    reciprocal_rank: float | None = None
    # Answer quality
    faithfulness: float | None = None
    answer_f1: float | None = None
    citation_precision: float | None = None
    correct_source_cited: bool | None = None
    judge_score: float | None = None
    # Operational
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _unique(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def score_item(item: GoldenItem, answer: Answer) -> ItemResult:
    """Compute every per-item metric for one golden row."""
    retrieved_sources = _unique(chunk.chunk.source for chunk in answer.retrieved)
    cited_sources = _unique(citation.source for citation in answer.citations)

    result = ItemResult(
        id=item.id,
        question=item.question,
        answerable=item.answerable,
        category=item.category,
        difficulty=item.difficulty,
        abstained=answer.abstained,
        abstain_reason=answer.abstain_reason,
        answer=answer.text,
        citations=cited_sources,
        retrieved_sources=retrieved_sources,
        expected_sources=list(item.expected_sources),
        latency_ms=round(answer.latency_ms, 2),
        input_tokens=answer.usage.input_tokens,
        output_tokens=answer.usage.output_tokens,
    )

    # --- retrieval: only meaningful when a correct document exists ---------
    if item.answerable and item.expected_sources:
        expected = set(item.expected_sources)
        result.hit_at_1 = bool(retrieved_sources) and retrieved_sources[0] in expected
        result.recall_at_k = any(source in expected for source in retrieved_sources)
        rank = next(
            (i for i, source in enumerate(retrieved_sources, start=1) if source in expected),
            None,
        )
        result.reciprocal_rank = 1.0 / rank if rank else 0.0

    # --- answer quality: only for answered, answerable items --------------
    if item.answerable and not answer.abstained:
        supporting = "\n".join(
            chunk.chunk.text
            for chunk in answer.retrieved
            if chunk.chunk.chunk_id in set(answer.cited_chunk_ids())
        )
        # Prefer the pipeline's own grounding score; recompute if absent.
        result.faithfulness = (
            answer.grounding_score
            if answer.grounding_score
            else round(overlap_ratio(answer.text, supporting), 4)
        )
        if item.reference_answer:
            result.answer_f1 = round(token_f1(answer.text, item.reference_answer), 4)
        if item.expected_sources:
            expected = set(item.expected_sources)
            if cited_sources:
                hits = sum(1 for source in cited_sources if source in expected)
                result.citation_precision = round(hits / len(cited_sources), 4)
            else:
                result.citation_precision = 0.0
            # any-of semantics: citing any one expected source is correct, so
            # this is "did the answer cite an acceptable source at all", not a
            # demand that every listed document be cited.
            result.correct_source_cited = any(s in expected for s in cited_sources)

    return result


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((pct / 100.0) * (len(ordered) - 1))), len(ordered) - 1)
    return round(ordered[index], 2)


def aggregate(results: Sequence[ItemResult]) -> dict[str, Any]:
    """Roll per-item results into the report's headline metrics."""
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    answered = [r for r in answerable if not r.abstained]

    # Retrieval
    hits1 = [float(r.hit_at_1) for r in answerable if r.hit_at_1 is not None]
    recalls = [float(r.recall_at_k) for r in answerable if r.recall_at_k is not None]
    rrs = [r.reciprocal_rank for r in answerable if r.reciprocal_rank is not None]

    # Answer quality
    faith = [r.faithfulness for r in answered if r.faithfulness is not None]
    f1s = [r.answer_f1 for r in answered if r.answer_f1 is not None]
    cprec = [r.citation_precision for r in answered if r.citation_precision is not None]
    csrc = [float(r.correct_source_cited) for r in answered if r.correct_source_cited is not None]
    judged = [r.judge_score for r in answered if r.judge_score is not None]

    # Abstention. Recall: of the questions we should refuse, how many did we?
    # Precision: of the refusals we made, how many were correct?
    abstained_unanswerable = sum(1 for r in unanswerable if r.abstained)
    abstained_answerable = sum(1 for r in answerable if r.abstained)
    total_abstained = abstained_unanswerable + abstained_answerable

    latencies = [r.latency_ms for r in results]

    metrics: dict[str, Any] = {
        "counts": {
            "total": len(results),
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "answered": len(answered),
            "abstained": total_abstained,
        },
        "retrieval": {
            "hit_at_1": _mean(hits1),
            "recall_at_k": _mean(recalls),
            "mrr": _mean(rrs),
        },
        "answer_quality": {
            "faithfulness": _mean(faith),
            "answer_f1": _mean(f1s),
            "citation_precision": _mean(cprec),
            "correct_source_cited_rate": _mean(csrc),
            # Every returned answer carries at least one resolvable citation, or
            # the pipeline abstains. Measured rather than assumed.
            "answer_citation_rate": _mean([float(bool(r.citations)) for r in answered]),
        },
        "abstention": {
            "abstention_recall": round(
                abstention_ratio(abstained_unanswerable, len(unanswerable)), 4
            ),
            "abstention_precision": round(
                abstention_ratio(abstained_unanswerable, total_abstained), 4
            ),
            "false_abstention_rate": round(
                abstention_ratio(abstained_answerable, len(answerable)), 4
            ),
        },
        "operational": {
            "latency_p50_ms": _percentile(latencies, 50),
            "latency_p95_ms": _percentile(latencies, 95),
            "mean_input_tokens": int(_mean([float(r.input_tokens) for r in results])),
            "mean_output_tokens": int(_mean([float(r.output_tokens) for r in results])),
        },
    }
    if judged:
        metrics["answer_quality"]["llm_judge_faithfulness"] = _mean(judged)

    metrics["by_difficulty"] = _breakdown(results, key=lambda r: r.difficulty)
    metrics["by_category"] = _breakdown(results, key=lambda r: r.category)
    metrics["abstain_reasons"] = _reason_counts(results)
    return metrics


def abstention_ratio(numerator: int, denominator: int) -> float:
    """Ratio with an explicit convention for the empty denominator.

    With no unanswerable items, abstention recall is *vacuously* perfect (1.0);
    with no abstentions at all, precision is 1.0 for the same reason. Returning
    0.0 would make an empty slice look like a failure and fail the gate for the
    wrong reason.
    """
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _breakdown(results: Sequence[ItemResult], key) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ItemResult]] = {}
    for result in results:
        groups.setdefault(key(result) or "unknown", []).append(result)

    out: dict[str, dict[str, Any]] = {}
    for name, group in sorted(groups.items()):
        answerable = [r for r in group if r.answerable]
        answered = [r for r in answerable if not r.abstained]
        out[name] = {
            "n": len(group),
            "recall_at_k": _mean(
                [float(r.recall_at_k) for r in answerable if r.recall_at_k is not None]
            ),
            "faithfulness": _mean([r.faithfulness for r in answered if r.faithfulness is not None]),
            "answer_f1": _mean([r.answer_f1 for r in answered if r.answer_f1 is not None]),
            "abstained": sum(1 for r in group if r.abstained),
        }
    return out


def _reason_counts(results: Sequence[ItemResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        if result.abstained and result.abstain_reason:
            counts[result.abstain_reason] = counts.get(result.abstain_reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
