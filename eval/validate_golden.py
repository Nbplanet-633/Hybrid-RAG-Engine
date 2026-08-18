"""Validate the golden dataset before trusting it.

A golden set with wrong labels silently poisons every downstream number: a
mislabelled ``expected_sources`` looks like a retrieval regression, and an
"unanswerable" question that the corpus actually answers looks like a
hallucination. This script is cheap, runs in CI ahead of the evaluation itself,
and catches the mistakes that are otherwise invisible.

Checks
------
1. Schema and unique ids.
2. Answerable rows have a reference answer and at least one expected source.
3. Every expected source path exists on disk.
4. The reference answer's content words actually appear in the *union* of its
   expected sources — the check that catches a copy-paste error in the source
   list. Union, not each source individually, because ``expected_sources`` uses
   any-of semantics: each listed document is a sufficient citation, and a fact
   restated across two documents legitimately lists both.
5. Unanswerable rows carry no reference answer and no sources.
6. Unanswerable rows are *plausibly* unanswerable: warn when every topical term
   in the question appears in the corpus, since that row may be mislabelled.

Usage::

    python -m eval.validate_golden [--golden data/golden/golden_set.jsonl] [--strict]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askmydocs.text import lexical_tokens, topical_tokens

# Below this, the reference answer shares so little vocabulary with its declared
# sources that the row is almost certainly pointing at the wrong file.
MIN_ANSWER_SOURCE_OVERLAP = 0.55
# The overlap ratio is meaningless on a two-word answer ("Node 18."): one
# incidental word swings it by 50 points. Short answers skip the check rather
# than generating false alarms that train people to ignore the validator.
MIN_TERMS_FOR_OVERLAP_CHECK = 4


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{number}: invalid JSON — {exc}") from exc
    return rows


def validate(golden_path: Path, corpus_dir: Path) -> tuple[list[str], list[str]]:
    rows = load_rows(golden_path)
    errors: list[str] = []
    warnings: list[str] = []

    if not rows:
        return ["golden set is empty"], []

    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    corpus_vocab: set[str] = set()
    for document in sorted(corpus_dir.rglob("*")):
        if document.is_file() and document.suffix.lower() in {".md", ".markdown", ".txt"}:
            corpus_vocab |= set(lexical_tokens(document.read_text(encoding="utf-8")))

    for row in rows:
        rid = row.get("id", "<missing id>")

        for field in ("id", "question"):
            if not row.get(field):
                errors.append(f"{rid}: missing required field '{field}'")

        if rid in seen_ids:
            errors.append(f"{rid}: duplicate id")
        seen_ids.add(rid)

        question = (row.get("question") or "").strip().lower()
        if question in seen_questions:
            warnings.append(f"{rid}: duplicate question text")
        seen_questions.add(question)

        answerable = bool(row.get("answerable", True))
        reference = (row.get("reference_answer") or "").strip()
        sources = list(row.get("expected_sources") or [])

        if answerable:
            if not reference:
                errors.append(f"{rid}: answerable row has no reference_answer")
            if not sources:
                errors.append(f"{rid}: answerable row has no expected_sources")

            union_vocab: set[str] = set()
            for source in sources:
                path = Path(source)
                if not path.exists():
                    errors.append(f"{rid}: expected source does not exist: {source}")
                    continue
                union_vocab |= set(lexical_tokens(path.read_text(encoding="utf-8")))

            # Check 4: do the declared sources actually support the answer?
            answer_terms = lexical_tokens(reference)
            if union_vocab and len(answer_terms) >= MIN_TERMS_FOR_OVERLAP_CHECK:
                matched = sum(1 for term in answer_terms if term in union_vocab)
                overlap = matched / len(answer_terms)
                if overlap < MIN_ANSWER_SOURCE_OVERLAP:
                    missing = sorted({t for t in answer_terms if t not in union_vocab})
                    errors.append(
                        f"{rid}: reference answer shares only {overlap:.0%} of its vocabulary "
                        f"with {sources} — wrong source? Missing terms: {missing[:8]}"
                    )
        else:
            if reference:
                errors.append(f"{rid}: unanswerable row must have an empty reference_answer")
            if sources:
                errors.append(f"{rid}: unanswerable row must have no expected_sources")
            # Check 6: is it really unanswerable?
            terms = set(topical_tokens(row.get("question", "")))
            if terms and terms <= corpus_vocab:
                warnings.append(
                    f"{rid}: every topical term appears in the corpus "
                    f"({sorted(terms)}) — verify this is genuinely unanswerable"
                )

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the golden dataset.")
    parser.add_argument("--golden", default="data/golden/golden_set.jsonl", type=Path)
    parser.add_argument("--corpus", default="data/corpus", type=Path)
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    args = parser.parse_args(argv)

    errors, warnings = validate(args.golden, args.corpus)
    rows = load_rows(args.golden)

    print(f"Golden set: {args.golden}  ({len(rows)} rows)")
    print(f"  answerable  : {sum(1 for r in rows if r.get('answerable', True))}")
    print(f"  unanswerable: {sum(1 for r in rows if not r.get('answerable', True))}")

    for warning in warnings:
        print(f"  WARN  {warning}")
    for error in errors:
        print(f"  ERROR {error}")

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    if warnings and args.strict:
        print(f"\nFAILED (strict): {len(warnings)} warning(s)")
        return 1
    print(f"\nOK: {len(warnings)} warning(s), no errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
