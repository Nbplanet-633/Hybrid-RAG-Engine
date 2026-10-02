"""Evaluation-harness tests.

The eval harness is what gates the build, so its arithmetic and its empty-slice
conventions need to be pinned. A metric that silently reports 0.0 instead of
"not applicable" fails a build for the wrong reason, and people quickly learn to
ignore a gate that cries wolf.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from eval.metrics import GoldenItem, abstention_ratio, aggregate, score_item
from eval.run_eval import check_thresholds, compute_deltas, load_golden
from eval.validate_golden import validate

from askmydocs.models import Answer, Chunk, Citation, RetrievedChunk, TokenUsage

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "data" / "golden" / "golden_set.jsonl"


def _retrieved(
    source: str, chunk_id: str = "c1", text: str = "The window is 14 days."
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(chunk_id=chunk_id, doc_id="d", source=source, text=text),
        score=0.9,
        retriever="hybrid",
    )


def _answer(
    *,
    text: str = "The refund window is 14 days. [S1]",
    sources: list[str] | None = None,
    cited: list[str] | None = None,
    abstained: bool = False,
    reason: str = "",
    grounding: float = 1.0,
) -> Answer:
    sources = sources or ["handbook.md"]
    cited = cited if cited is not None else ["handbook.md"]
    return Answer(
        question="How long is the refund window?",
        text=text,
        abstained=abstained,
        abstain_reason=reason,
        grounding_score=grounding,
        retrieved=[_retrieved(s, chunk_id=f"c{i}") for i, s in enumerate(sources)],
        citations=[
            Citation(marker=f"S{i + 1}", chunk_id=f"c{sources.index(s)}", source=s)
            for i, s in enumerate(cited)
        ],
        usage=TokenUsage(input_tokens=100, output_tokens=20),
        latency_ms=12.5,
    )


ITEM = GoldenItem(
    id="q1",
    question="How long is the refund window?",
    reference_answer="The refund window is 14 calendar days.",
    expected_sources=["handbook.md"],
    answerable=True,
    category="refunds",
    difficulty="easy",
)
UNANSWERABLE = GoldenItem(
    id="q2", question="Capital of Portugal?", answerable=False, difficulty="abstention"
)


class TestGoldenItem:
    def test_requires_id_and_question(self) -> None:
        with pytest.raises(ValueError, match="missing required fields"):
            GoldenItem.from_dict({"question": "no id"})

    def test_defaults_are_sensible(self) -> None:
        item = GoldenItem.from_dict({"id": "x", "question": "q"})
        assert item.answerable is True
        assert item.expected_sources == []


class TestScoreItem:
    def test_perfect_answer_scores_across_every_family(self) -> None:
        result = score_item(ITEM, _answer())
        assert result.hit_at_1 is True
        assert result.recall_at_k is True
        assert result.reciprocal_rank == 1.0
        assert result.faithfulness == 1.0
        assert result.citation_precision == 1.0
        assert result.correct_source_cited is True
        assert result.answer_f1 is not None and result.answer_f1 > 0.5

    def test_reciprocal_rank_reflects_position(self) -> None:
        answer = _answer(sources=["other.md", "another.md", "handbook.md"], cited=["handbook.md"])
        result = score_item(ITEM, answer)
        assert result.hit_at_1 is False
        assert result.recall_at_k is True
        assert result.reciprocal_rank == pytest.approx(1 / 3)

    def test_missed_retrieval_scores_zero(self) -> None:
        result = score_item(ITEM, _answer(sources=["wrong.md"], cited=["wrong.md"]))
        assert result.hit_at_1 is False
        assert result.recall_at_k is False
        assert result.reciprocal_rank == 0.0
        assert result.correct_source_cited is False
        assert result.citation_precision == 0.0

    def test_citation_precision_penalises_extra_sources(self) -> None:
        answer = _answer(
            sources=["handbook.md", "unrelated.md"], cited=["handbook.md", "unrelated.md"]
        )
        assert score_item(ITEM, answer).citation_precision == 0.5

    def test_any_of_semantics_for_multi_source_rows(self) -> None:
        # Each listed document is a *sufficient* citation, so citing one is correct.
        item = GoldenItem(
            id="q3",
            question="q",
            reference_answer="a",
            expected_sources=["a.md", "b.md"],
            answerable=True,
        )
        result = score_item(item, _answer(sources=["a.md"], cited=["a.md"]))
        assert result.correct_source_cited is True
        assert result.citation_precision == 1.0

    def test_abstention_skips_answer_quality_metrics(self) -> None:
        result = score_item(ITEM, _answer(abstained=True, reason="low_relevance", cited=[]))
        assert result.abstained is True
        assert result.faithfulness is None
        assert result.answer_f1 is None
        # Retrieval is still scored: an abstention caused by bad retrieval must
        # remain visible as a retrieval failure.
        assert result.recall_at_k is True

    def test_unanswerable_rows_have_no_retrieval_metrics(self) -> None:
        result = score_item(UNANSWERABLE, _answer(abstained=True, reason="low_relevance", cited=[]))
        assert result.hit_at_1 is None
        assert result.recall_at_k is None


class TestAbstentionRatio:
    def test_empty_denominator_is_vacuously_perfect(self) -> None:
        # With no unanswerable items, abstention recall is not a failure. Returning
        # 0.0 would fail the gate for the wrong reason.
        assert abstention_ratio(0, 0) == 1.0

    def test_normal_ratio(self) -> None:
        assert abstention_ratio(3, 4) == 0.75


class TestAggregate:
    def test_counts_and_headline_metrics(self) -> None:
        results = [
            score_item(ITEM, _answer()),
            score_item(ITEM, _answer(sources=["wrong.md"], cited=["wrong.md"])),
            score_item(UNANSWERABLE, _answer(abstained=True, reason="low_relevance", cited=[])),
        ]
        metrics = aggregate(results)

        assert metrics["counts"] == {
            "total": 3,
            "answerable": 2,
            "unanswerable": 1,
            "answered": 2,
            "abstained": 1,
        }
        assert metrics["retrieval"]["hit_at_1"] == 0.5
        assert metrics["abstention"]["abstention_recall"] == 1.0
        assert metrics["abstention"]["abstention_precision"] == 1.0
        assert metrics["abstention"]["false_abstention_rate"] == 0.0

    def test_false_abstention_is_counted_and_hurts_precision(self) -> None:
        results = [
            score_item(ITEM, _answer(abstained=True, reason="low_relevance", cited=[])),
            score_item(UNANSWERABLE, _answer(abstained=True, reason="low_relevance", cited=[])),
        ]
        metrics = aggregate(results)
        assert metrics["abstention"]["false_abstention_rate"] == 1.0
        assert metrics["abstention"]["abstention_precision"] == 0.5

    def test_breakdowns_and_reason_counts(self) -> None:
        results = [
            score_item(ITEM, _answer()),
            score_item(
                UNANSWERABLE, _answer(abstained=True, reason="question_not_covered", cited=[])
            ),
        ]
        metrics = aggregate(results)
        assert metrics["by_difficulty"]["easy"]["n"] == 1
        assert metrics["by_category"]["refunds"]["n"] == 1
        assert metrics["abstain_reasons"]["question_not_covered"] == 1

    def test_operational_metrics_are_present(self) -> None:
        metrics = aggregate([score_item(ITEM, _answer())])
        assert metrics["operational"]["latency_p50_ms"] == 12.5
        assert metrics["operational"]["mean_input_tokens"] == 100

    def test_empty_results_do_not_crash(self) -> None:
        metrics = aggregate([])
        assert metrics["counts"]["total"] == 0
        assert metrics["retrieval"]["hit_at_1"] == 0.0


class TestThresholdGate:
    METRICS = {"retrieval": {"hit_at_1": 0.9}, "abstention": {"false_abstention_rate": 0.05}}

    def test_min_bound_passes_and_fails(self) -> None:
        assert check_thresholds(self.METRICS, {"retrieval": {"hit_at_1": {"min": 0.8}}})[0][
            "passed"
        ]
        failed = check_thresholds(self.METRICS, {"retrieval": {"hit_at_1": {"min": 0.95}}})[0]
        assert not failed["passed"]
        assert "min 0.95" in failed["detail"]

    def test_max_bound_passes_and_fails(self) -> None:
        thresholds = {"abstention": {"false_abstention_rate": {"max": 0.1}}}
        assert check_thresholds(self.METRICS, thresholds)[0]["passed"]
        thresholds = {"abstention": {"false_abstention_rate": {"max": 0.01}}}
        assert not check_thresholds(self.METRICS, thresholds)[0]["passed"]

    def test_a_missing_metric_fails_rather_than_passing_silently(self) -> None:
        # A renamed metric must break the build, not quietly stop being checked.
        check = check_thresholds(self.METRICS, {"retrieval": {"nonexistent": {"min": 0.5}}})[0]
        assert not check["passed"]
        assert "not produced" in check["detail"]

    def test_empty_thresholds_produce_no_checks(self) -> None:
        assert check_thresholds(self.METRICS, {}) == []

    def test_deltas_against_a_baseline(self) -> None:
        current = {"retrieval": {"hit_at_1": 0.9}}
        baseline = {"retrieval": {"hit_at_1": 0.8}}
        deltas = compute_deltas(current, baseline)
        assert deltas["retrieval.hit_at_1"]["delta"] == pytest.approx(0.1)


class TestShippedGoldenSet:
    def test_loads_and_has_both_classes(self) -> None:
        items = load_golden(GOLDEN)
        assert len(items) >= 50  # the brief asks for 50-200 pairs
        assert len(items) <= 200
        assert sum(1 for i in items if not i.answerable) >= 10

    def test_ids_are_unique(self) -> None:
        items = load_golden(GOLDEN)
        assert len({i.id for i in items}) == len(items)

    def test_limit_is_applied(self) -> None:
        assert len(load_golden(GOLDEN, limit=5)) == 5

    def test_the_shipped_set_passes_its_own_validator(self) -> None:
        errors, _ = validate(GOLDEN, REPO_ROOT / "data" / "corpus")
        assert errors == []

    def test_validator_catches_a_wrong_source(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text(
            "# A\n\nWidgets are blue and cost four euros.\n", encoding="utf-8"
        )

        golden = tmp_path / "g.jsonl"
        golden.write_text(
            '{"id": "q1", "question": "What is the dispute fee?", '
            '"reference_answer": "The dispute fee is fifteen euros refunded on winning.", '
            f'"expected_sources": ["{(corpus / "a.md").as_posix()}"], "answerable": true}}\n',
            encoding="utf-8",
        )
        errors, _ = validate(golden, corpus)
        assert any("wrong source" in e for e in errors)

    def test_validator_rejects_a_labelled_contradiction(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("# A\n\nContent.\n", encoding="utf-8")
        golden = tmp_path / "g.jsonl"
        golden.write_text(
            '{"id": "q1", "question": "q", "reference_answer": "an answer", '
            '"expected_sources": [], "answerable": false}\n',
            encoding="utf-8",
        )
        errors, _ = validate(golden, corpus)
        assert any("empty reference_answer" in e for e in errors)
