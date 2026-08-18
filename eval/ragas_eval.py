"""Optional third-party evaluation via Ragas.

The built-in metrics in :mod:`eval.metrics` are the gate: they run on every
commit with no API key and no network. Ragas is a *cross-check* — an independent
implementation of faithfulness, answer relevancy, and context precision/recall,
useful when you want a second opinion before changing a threshold or shipping a
prompt rewrite.

It is deliberately not in the CI path: it calls a hosted model per question, so
it is slow and costs money, and a metric you cannot run on every commit cannot
gate a build.

    pip install 'ask-my-docs[ragas]'
    python -m eval.ragas_eval --profile full --limit 25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askmydocs.config import load_config
from askmydocs.pipeline import RAGPipeline
from eval.run_eval import load_golden


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-check the pipeline with Ragas.")
    parser.add_argument("--golden", type=Path, default=Path("data/golden/golden_set.jsonl"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--limit", type=int, default=25, help="Ragas is slow; keep this small.")
    parser.add_argument("--out", type=Path, default=Path("eval_reports/ragas.json"))
    args = parser.parse_args(argv)

    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError:
        print(
            "Ragas is not installed. Install the extra:\n    pip install 'ask-my-docs[ragas]'",
            file=sys.stderr,
        )
        return 2

    config = load_config(profile=args.profile)
    pipeline = RAGPipeline.from_config(config)
    if pipeline.is_empty():
        print("Index is empty — run ingestion first.", file=sys.stderr)
        return 2

    # Ragas measures generation quality, so abstentions and unanswerable rows are
    # excluded: there is no answer to score, and including them would drag every
    # metric down for the wrong reason. Abstention behaviour is measured by the
    # built-in harness instead.
    records: list[dict] = []
    skipped = 0
    for item in load_golden(args.golden, args.limit):
        if not item.answerable:
            continue
        answer = pipeline.answer(item.question)
        if answer.abstained:
            skipped += 1
            continue
        records.append(
            {
                "question": item.question,
                "answer": answer.text,
                "contexts": [c.chunk.text for c in answer.retrieved],
                "ground_truth": item.reference_answer,
            }
        )

    if not records:
        print("No answered questions to evaluate.", file=sys.stderr)
        return 2

    print(f"Scoring {len(records)} answers with Ragas ({skipped} abstentions excluded)...")
    result = evaluate(
        Dataset.from_list(records),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
    )

    scores = {key: float(value) for key, value in dict(result).items()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "profile": config.profile,
                "n": len(records),
                "skipped_abstentions": skipped,
                "scores": scores,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\nRagas scores:")
    for key, value in scores.items():
        print(f"  {key:<24} {value:.4f}")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
