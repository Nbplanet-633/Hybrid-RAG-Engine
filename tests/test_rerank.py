"""Reranker tests.

The lexical reranker is the offline profile's precision stage and the source of
the score that the abstention gate thresholds on, so its ordering behaviour and
its IDF weighting are both load-bearing.
"""

from __future__ import annotations

import pytest

from askmydocs.config import RerankConfig
from askmydocs.models import Chunk, RetrievedChunk
from askmydocs.rerank import LexicalReranker, get_reranker


def _candidate(chunk_id: str, text: str, section: str = "", score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            doc_id="d",
            source=f"{chunk_id}.md",
            title="T",
            section=section,
            text=text,
        ),
        score=score,
        fusion_score=score,
        retriever="hybrid",
    )


CANDIDATES = [
    _candidate("filler", "Widgets are blue. Sprockets are green. Colours are configurable."),
    _candidate(
        "target", "The dispute fee is 15.00 EUR and is refunded if you win.", "Dispute fees"
    ),
    _candidate("partial", "A refund returns captured funds to the cardholder."),
]


class TestLexicalReranker:
    def test_puts_the_relevant_candidate_first(self) -> None:
        ranked = LexicalReranker().rerank("How much is the dispute fee?", CANDIDATES, top_n=3)
        assert ranked[0].chunk.chunk_id == "target"

    def test_truncates_to_top_n(self) -> None:
        assert len(LexicalReranker().rerank("dispute fee", CANDIDATES, top_n=2)) == 2

    def test_scores_are_bounded_and_descending(self) -> None:
        ranked = LexicalReranker().rerank("dispute fee refunded", CANDIDATES, top_n=3)
        scores = [r.score for r in ranked]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_sets_both_score_and_rerank_score(self) -> None:
        ranked = LexicalReranker().rerank("dispute fee", CANDIDATES, top_n=1)
        assert ranked[0].rerank_score == ranked[0].score

    def test_preserves_the_first_stage_signals(self) -> None:
        # The fusion trace must survive reranking, or the retrieval path becomes
        # unauditable from the returned Answer.
        ranked = LexicalReranker().rerank("dispute fee", CANDIDATES, top_n=1)
        assert ranked[0].fusion_score is not None
        assert ranked[0].retriever == "hybrid"

    def test_exact_phrase_match_earns_a_bonus(self) -> None:
        plain = _candidate("plain", "Fees for disputes are documented elsewhere in this guide.")
        exact = _candidate("exact", "The dispute fee is charged when the dispute is opened.")
        ranked = LexicalReranker().rerank("dispute fee", [plain, exact], top_n=2)
        assert ranked[0].chunk.chunk_id == "exact"

    def test_heading_match_contributes(self) -> None:
        with_heading = _candidate("h", "Charged when opened. Refunded on a win.", "Dispute fees")
        without = _candidate("n", "Charged when opened. Refunded on a win.", "Other")
        ranked = LexicalReranker().rerank("dispute fees", [without, with_heading], top_n=2)
        assert ranked[0].chunk.chunk_id == "h"

    def test_empty_candidates_and_empty_query(self) -> None:
        assert LexicalReranker().rerank("anything", [], top_n=5) == []
        assert all(r.score == 0.0 for r in LexicalReranker().rerank("", CANDIDATES, top_n=3))

    def test_off_topic_query_scores_near_zero(self) -> None:
        # This is what the abstention gate depends on.
        ranked = LexicalReranker().rerank("capital of Portugal", CANDIDATES, top_n=1)
        assert ranked[0].score < 0.15

    def test_density_prevents_a_long_chunk_from_winning_on_length(self) -> None:
        short = _candidate("short", "The dispute fee is 15.00 EUR.")
        padded = _candidate(
            "padded",
            "The dispute fee is 15.00 EUR. " + "Unrelated filler about widgets and colours. " * 30,
        )
        ranked = LexicalReranker().rerank("dispute fee", [padded, short], top_n=2)
        assert ranked[0].chunk.chunk_id == "short"


class TestIdfWeighting:
    def test_idf_downweights_corpus_wide_filler(self) -> None:
        """A query term present in every document must not drive the ranking."""
        common = _candidate("common", "Aurora returns a response for every request you send.")
        rare = _candidate("rare", "Exceeding the quota returns HTTP 429 with a Retry-After header.")
        candidates = [common, rare]

        # "request"/"returns" are everywhere; "429" is rare and discriminative.
        idf = {
            "request": 0.05,
            "return": 0.05,
            "aurora": 0.01,
            "429": 3.2,
            "quota": 2.8,
            "http": 1.1,
        }
        reranker = LexicalReranker()
        reranker.set_idf_provider(lambda: idf, lambda: 3.2)

        ranked = reranker.rerank("what request returns 429", candidates, top_n=2)
        assert ranked[0].chunk.chunk_id == "rare"

    def test_degrades_gracefully_without_idf_statistics(self) -> None:
        # No provider attached: unweighted overlap, still functional.
        ranked = LexicalReranker().rerank("dispute fee", CANDIDATES, top_n=1)
        assert ranked[0].chunk.chunk_id == "target"

    def test_out_of_vocabulary_terms_suppress_the_score(self) -> None:
        reranker = LexicalReranker()
        idf = {"disput": 1.5, "fee": 1.2}
        reranker.set_idf_provider(lambda: idf, lambda: 4.0)

        in_scope = reranker.rerank("dispute fee", CANDIDATES, top_n=1)[0].score
        # "salesforce" and "webhook" are absent from the idf map, so they are
        # weighted at max_idf and can never be matched.
        out_of_scope = reranker.rerank("salesforce integration", CANDIDATES, top_n=1)[0].score
        assert out_of_scope < in_scope


class TestFactory:
    def test_builds_the_lexical_reranker(self) -> None:
        reranker = get_reranker(RerankConfig(provider="lexical"))
        assert reranker.name == "lexical"

    def test_unknown_provider_is_rejected(self) -> None:
        config = RerankConfig(provider="lexical")
        object.__setattr__(config, "provider", "nonsense")  # bypass literal validation
        with pytest.raises(ValueError, match="Unknown rerank provider"):
            get_reranker(config)
