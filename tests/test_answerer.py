"""Citation-enforcement tests — the trust boundary of the system.

These use a scripted LLM so each gate can be driven deliberately: a model that
invents a citation, one that cites nothing, one that answers fluently with no
basis in the sources. That is the only way to prove the enforcement layer works,
because a real model rarely produces those failures on demand — and when it does,
in production, this is the code that has to catch it.
"""

from __future__ import annotations

from pathlib import Path

from askmydocs.config import CitationConfig, GenerationConfig
from askmydocs.generation.answerer import (
    REASON_EMPTY_RESPONSE,
    REASON_LOW_GROUNDING,
    REASON_LOW_RELEVANCE,
    REASON_MODEL_REFUSAL,
    REASON_NO_CITATIONS,
    REASON_NO_RESULTS,
    REASON_QUESTION_NOT_COVERED,
    Answerer,
)
from askmydocs.generation.llm import GenerationRequest, LLMResponse
from askmydocs.generation.prompts import PromptLibrary
from askmydocs.models import Chunk, RetrievedChunk

REPO_PROMPTS = Path(__file__).resolve().parents[1] / "config" / "prompts"

QUESTION = "How much is the dispute fee?"
SOURCE_TEXT = (
    "Every formal dispute incurs a 15.00 EUR dispute fee, charged when the dispute "
    "is opened. The fee is refunded if you win."
)


class ScriptedLLM:
    """Returns a fixed string and records the request it was handed.

    Lets a test assert on what the model was *asked*, not just what came back.
    """

    name = "scripted"
    model = "scripted-v1"

    def __init__(self, text: str) -> None:
        self.text = text
        self.last_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(text=self.text, model=self.model, input_tokens=100, output_tokens=20)


def _retrieved(score: float = 0.8, text: str = SOURCE_TEXT, chunk_id: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            doc_id="disputes",
            source="disputes.md",
            title="Disputes",
            section="Disputes > Dispute fees",
            text=text,
        ),
        score=score,
        rerank_score=score,
        retriever="hybrid",
        dense_rank=1,
        lexical_rank=1,
    )


def build(
    llm_text: str,
    *,
    min_top_score: float = 0.1,
    min_grounding: float = 0.3,
    require_citations: bool = True,
    min_question_coverage: float = 0.0,
    idf: dict[str, float] | None = None,
) -> tuple[Answerer, ScriptedLLM]:
    llm = ScriptedLLM(llm_text)
    answerer = Answerer(
        llm=llm,
        prompts=PromptLibrary(REPO_PROMPTS),
        generation=GenerationConfig(provider="extractive", model="scripted-v1"),
        citations=CitationConfig(
            min_top_score=min_top_score,
            min_grounding=min_grounding,
            require_citations=require_citations,
            min_question_coverage=min_question_coverage,
        ),
    )
    if idf is not None:
        answerer.set_idf_provider(lambda: idf, lambda: max(idf.values()) if idf else 1.0)
    return answerer, llm


class TestHappyPath:
    def test_valid_cited_answer_is_returned(self) -> None:
        answerer, _ = build("The dispute fee is 15.00 EUR. [S1]")
        answer = answerer.answer(QUESTION, [_retrieved()])

        assert not answer.abstained
        assert answer.text == "The dispute fee is 15.00 EUR. [S1]"
        assert [c.marker for c in answer.citations] == ["S1"]
        assert answer.citations[0].source == "disputes.md"
        assert answer.citations[0].quote  # a supporting quote is attached
        assert answer.grounding_score > 0.9
        assert 0.0 < answer.confidence <= 1.0
        assert answer.prompt_version == "v2"
        assert answer.usage.input_tokens == 100

    def test_multiple_citations_are_all_resolved(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S1] and it is refunded on a win [S2].")
        answer = answerer.answer(
            QUESTION, [_retrieved(chunk_id="c1"), _retrieved(chunk_id="c2", score=0.7)]
        )
        assert [c.marker for c in answer.citations] == ["S1", "S2"]

    def test_context_is_numbered_and_labelled_for_the_model(self) -> None:
        answerer, llm = build("The dispute fee is 15.00 EUR. [S1]")
        answerer.answer(QUESTION, [_retrieved()])
        assert "[S1] source: disputes.md" in llm.last_request.user
        assert "section: Disputes > Dispute fees" in llm.last_request.user

    def test_confidence_rises_with_citation_breadth(self) -> None:
        one, _ = build("Fee is 15.00 EUR [S1].")
        two, _ = build("Fee is 15.00 EUR [S1] refunded on a win [S2].")
        single = one.answer(QUESTION, [_retrieved()])
        double = two.answer(QUESTION, [_retrieved(chunk_id="c1"), _retrieved(chunk_id="c2")])
        assert double.confidence > single.confidence


class TestPreGenerationGates:
    def test_no_retrieval_results_abstains_without_calling_the_model(self) -> None:
        answerer, llm = build("should never be used")
        answer = answerer.answer(QUESTION, [])
        assert answer.abstained
        assert answer.abstain_reason == REASON_NO_RESULTS
        assert llm.last_request is None

    def test_low_relevance_abstains_without_calling_the_model(self) -> None:
        # Asking a model to answer from off-topic passages is the most common
        # hallucination trigger; skipping the call removes it entirely, and saves
        # the latency and token cost of a request that could only mislead.
        answerer, llm = build("A confident but baseless answer. [S1]")
        answer = answerer.answer(QUESTION, [_retrieved(score=0.01)])
        assert answer.abstained
        assert answer.abstain_reason == REASON_LOW_RELEVANCE
        assert llm.last_request is None

    def test_min_sources_is_respected(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S1].")
        answerer.citations.min_sources = 2
        answer = answerer.answer(QUESTION, [_retrieved(score=0.9)])
        assert answer.abstained
        assert answer.abstain_reason == REASON_LOW_RELEVANCE

    def test_out_of_scope_question_abstains_on_corpus_vocabulary(self) -> None:
        idf = {"disput": 1.5, "fee": 1.2, "refund": 1.4}
        answerer, llm = build(
            "Aurora partners with several airlines. [S1]",
            min_question_coverage=0.6,
            idf=idf,
        )
        answer = answerer.answer("Which airlines does Aurora partner with?", [_retrieved()])
        assert answer.abstained
        assert answer.abstain_reason == REASON_QUESTION_NOT_COVERED
        assert llm.last_request is None

    def test_in_scope_question_passes_the_coverage_gate(self) -> None:
        idf = {"disput": 1.5, "fee": 1.2}
        answerer, _ = build("Fee is 15.00 EUR [S1].", min_question_coverage=0.6, idf=idf)
        assert not answerer.answer(QUESTION, [_retrieved()]).abstained

    def test_coverage_gate_is_inert_without_idf_statistics(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S1].", min_question_coverage=0.9)
        # No IDF provider attached: the gate must degrade to inert rather than
        # silently rejecting every question.
        assert answerer.question_coverage(QUESTION) == 1.0
        assert not answerer.answer(QUESTION, [_retrieved()]).abstained

    def test_coverage_is_measured_against_the_corpus_not_the_passages(self) -> None:
        # A term the corpus has but retrieval missed is a recall problem, not an
        # out-of-scope question, and must not be reported as one.
        idf = {"disput": 1.5, "fee": 1.2, "payout": 1.1}
        answerer, _ = build("x [S1]", min_question_coverage=0.6, idf=idf)
        assert answerer.question_coverage("What is the payout fee?") == 1.0


class TestRefusalSentinel:
    def test_sentinel_produces_a_structured_abstention(self) -> None:
        answerer, _ = build("INSUFFICIENT_EVIDENCE")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert answer.abstained
        assert answer.abstain_reason == REASON_MODEL_REFUSAL
        assert answer.text == answerer.citations.refusal_message
        assert answer.citations == []

    def test_sentinel_anywhere_in_the_response_still_counts(self) -> None:
        # Detecting a token is reliable; pattern-matching hedged prose is not.
        answerer, _ = build("I looked but INSUFFICIENT_EVIDENCE to answer.")
        assert answerer.answer(QUESTION, [_retrieved()]).abstain_reason == REASON_MODEL_REFUSAL

    def test_empty_response_abstains(self) -> None:
        answerer, _ = build("   ")
        assert answerer.answer(QUESTION, [_retrieved()]).abstain_reason == REASON_EMPTY_RESPONSE


class TestCitationValidation:
    def test_invented_marker_is_stripped_from_the_answer(self) -> None:
        # The model cited a passage that was never supplied. Returning it would
        # give the reader a citation they cannot follow.
        answerer, _ = build("Fee is 15.00 EUR [S1] and payouts are daily [S7].")
        answer = answerer.answer(QUESTION, [_retrieved()])

        assert not answer.abstained
        assert "[S7]" not in answer.text
        assert "[S1]" in answer.text
        assert [c.marker for c in answer.citations] == ["S1"]

    def test_answer_with_only_invented_markers_abstains(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S9].")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert answer.abstained
        assert answer.abstain_reason == REASON_NO_CITATIONS

    def test_uncited_answer_abstains(self) -> None:
        answerer, _ = build("The dispute fee is 15.00 EUR.")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert answer.abstained
        assert answer.abstain_reason == REASON_NO_CITATIONS

    def test_citation_requirement_can_be_disabled(self) -> None:
        answerer, _ = build("The dispute fee is 15.00 EUR.", require_citations=False)
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert not answer.abstained
        assert answer.citations == []

    def test_marker_matching_tolerates_whitespace_and_case(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [ s1 ].")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert [c.marker for c in answer.citations] == ["S1"]

    def test_duplicate_markers_are_deduplicated(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S1]. It is refunded on a win [S1].")
        assert len(answerer.answer(QUESTION, [_retrieved()]).citations) == 1


class TestGroundingGate:
    def test_fluent_but_unsupported_answer_abstains(self) -> None:
        # The signature of fabrication: confident prose with no lexical footprint
        # in the passage it claims to cite.
        answerer, _ = build(
            "Aurora waives all chargeback penalties for merchants enrolled in the "
            "platinum loyalty programme during promotional quarters. [S1]"
        )
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert answer.abstained
        assert answer.abstain_reason == REASON_LOW_GROUNDING
        assert answer.grounding_score < 0.3

    def test_grounding_is_computed_against_the_cited_passages_only(self) -> None:
        cited = _retrieved(chunk_id="c1", text=SOURCE_TEXT, score=0.9)
        other = _retrieved(
            chunk_id="c2", text="Payouts are daily and settle in two business days.", score=0.8
        )
        answerer, _ = build("Payouts are daily and settle in two business days. [S2]")
        answer = answerer.answer(QUESTION, [cited, other])
        assert not answer.abstained
        assert answer.grounding_score > 0.9

    def test_threshold_of_zero_disables_the_gate(self) -> None:
        answerer, _ = build(
            "Entirely invented claim about loyalty programmes. [S1]", min_grounding=0.0
        )
        assert not answerer.answer(QUESTION, [_retrieved()]).abstained

    def test_markers_are_excluded_from_the_grounding_calculation(self) -> None:
        # "[S1]" is not a content word and must not skew the score.
        answerer, _ = build("The dispute fee is 15.00 EUR. [S1]")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert answer.grounding_score == 1.0


class TestAbstentionShape:
    def test_every_abstention_has_a_machine_readable_reason(self) -> None:
        cases = [
            (build("x")[0], []),
            (build("x", min_top_score=0.99)[0], [_retrieved(score=0.1)]),
            (build("INSUFFICIENT_EVIDENCE")[0], [_retrieved()]),
            (build("uncited text")[0], [_retrieved()]),
        ]
        for answerer, retrieved in cases:
            answer = answerer.answer(QUESTION, retrieved)
            assert answer.abstained
            assert answer.abstain_reason
            assert answer.confidence == 0.0
            assert answer.citations == []
            assert answer.text == answerer.citations.refusal_message

    def test_abstention_still_reports_what_was_retrieved(self) -> None:
        # The retrieval trace is what makes an abstention debuggable.
        answerer, _ = build("INSUFFICIENT_EVIDENCE")
        answer = answerer.answer(QUESTION, [_retrieved()])
        assert len(answer.retrieved) == 1

    def test_latency_is_always_recorded(self) -> None:
        answerer, _ = build("Fee is 15.00 EUR [S1].")
        assert answerer.answer(QUESTION, [_retrieved()]).latency_ms >= 0.0


class TestContextBudget:
    def test_per_source_truncation_is_enforced(self) -> None:
        answerer, llm = build("Fee is 15.00 EUR [S1].")
        answerer.generation.max_chars_per_source = 200
        answerer.answer(QUESTION, [_retrieved(text="word " * 500)])
        assert len(llm.last_request.sources[0].text) <= 220  # 200 plus the ellipsis

    def test_total_context_budget_limits_the_number_of_passages(self) -> None:
        # Prompt size is bounded by configuration, not by whatever the model's
        # context window happens to allow.
        answerer, llm = build("Fee is 15.00 EUR [S1].")
        answerer.generation.max_context_chars = 600
        answerer.generation.max_chars_per_source = 500
        passages = [_retrieved(chunk_id=f"c{i}", text="x" * 500, score=0.9) for i in range(6)]
        answerer.answer(QUESTION, passages)
        assert len(llm.last_request.sources) < 6
