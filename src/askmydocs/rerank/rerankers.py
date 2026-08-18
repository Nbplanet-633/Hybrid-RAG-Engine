"""Rerankers.

First-stage retrieval optimises recall: cast a wide net, accept some noise. The
reranker optimises precision over that net. A cross-encoder reads the query and
the passage *together* in one forward pass, so it can judge relevance in a way no
independently-embedded bi-encoder can — at a cost that only makes sense on a
couple of dozen candidates, which is exactly what the fusion stage produces.

Three providers:

* ``CrossEncoderReranker`` — local ``cross-encoder/ms-marco-MiniLM-L-6-v2``.
  Default. Emits unbounded logits; higher is better, and roughly ``> 0`` means
  "relevant" for the ms-marco family.
* ``CohereReranker`` — hosted ``rerank-v3.5``. Returns scores in ``[0, 1]``.
* ``LexicalReranker`` — no model. A length-normalised term-overlap score with an
  exact-phrase bonus, in ``[0, 1]``. Deterministic, used by CI, and a reasonable
  degradation path when a model is unavailable.

Because score *scales* differ by provider, the abstention threshold
(``citations.min_top_score``) is configured per profile, not globally.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable

from askmydocs.models import RetrievedChunk
from askmydocs.text import lexical_tokens, normalize


@runtime_checkable
class Reranker(Protocol):
    name: str

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...


def _finalize(
    candidates: list[RetrievedChunk], scores: list[float], top_n: int
) -> list[RetrievedChunk]:
    """Attach rerank scores, sort, and truncate."""
    scored: list[RetrievedChunk] = []
    for candidate, score in zip(candidates, scores, strict=True):
        scored.append(candidate.model_copy(update={"rerank_score": score, "score": score}))
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:top_n]


class LexicalReranker:
    """Deterministic IDF-weighted term-overlap reranker. No model, no network.

    Coverage is weighted by inverse document frequency, so rare, discriminative
    terms ("429", "chargeback", "idempotency") dominate the score and corpus-wide
    filler ("status", "returned", "request") contributes almost nothing.

    Terms absent from the corpus entirely are weighted at the maximum observed IDF
    and can never be matched. That is what makes an out-of-scope question score
    near zero and trip the abstention gate, instead of scoring well on one
    incidental word.

    Supply IDF statistics with :meth:`set_idf_provider`; without them the scorer
    degrades gracefully to unweighted overlap.
    """

    name = "lexical"

    def __init__(self, phrase_bonus: float = 0.25) -> None:
        self.phrase_bonus = phrase_bonus
        self._idf_provider: Callable[[], dict[str, float]] | None = None
        self._max_idf_provider: Callable[[], float] | None = None

    def set_idf_provider(
        self,
        idf_provider: Callable[[], dict[str, float]],
        max_idf_provider: Callable[[], float] | None = None,
    ) -> None:
        """Attach corpus IDF statistics (wired up by the pipeline)."""
        self._idf_provider = idf_provider
        self._max_idf_provider = max_idf_provider

    def _weights(self, query_tokens: Iterable[str]) -> dict[str, float]:
        idf = self._idf_provider() if self._idf_provider else {}
        if not idf:
            return dict.fromkeys(query_tokens, 1.0)
        default = self._max_idf_provider() if self._max_idf_provider else max(idf.values())
        # Floor at a small positive value so a term present in every document
        # still counts for something rather than vanishing.
        return {token: max(idf.get(token, default), 0.01) for token in query_tokens}

    def _score(self, query: str, chunk_text: str, section: str) -> float:
        query_tokens = lexical_tokens(query)
        if not query_tokens:
            return 0.0
        query_set = set(query_tokens)
        weights = self._weights(query_set)
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return 0.0

        body_tokens = lexical_tokens(chunk_text)
        if not body_tokens:
            return 0.0
        body_set = set(body_tokens)
        section_set = set(lexical_tokens(section))

        # IDF-weighted coverage: how much of the query's *information* is present.
        coverage = sum(weights[t] for t in query_set & body_set) / total_weight
        # Density: matches per unit length, so a 700-token chunk that mentions the
        # term once does not beat a 60-token chunk that is entirely about it.
        matches = sum(weights.get(token, 0.0) for token in body_tokens if token in query_set)
        density = matches / (total_weight * math.sqrt(len(body_tokens)))
        # Heading hits are strong evidence the passage is on-topic.
        heading = (
            sum(weights[t] for t in query_set & section_set) / total_weight if section_set else 0.0
        )

        score = 0.60 * coverage + 0.25 * min(density, 1.0) + 0.15 * heading

        # Exact multi-word phrase match is the single best precision signal.
        normalized_query = normalize(query)
        if len(normalized_query.split()) > 1 and normalized_query in normalize(chunk_text):
            score += self.phrase_bonus

        return min(score, 1.0)

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        scores = [self._score(query, c.chunk.text, c.chunk.section) for c in candidates]
        return _finalize(candidates, scores, top_n)


class CrossEncoderReranker:
    """Local cross-encoder reranker via sentence-transformers."""

    name = "cross-encoder"

    def __init__(
        self,
        model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        batch_size: int = 16,
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The cross-encoder reranker requires an extra install:\n"
                "    pip install 'ask-my-docs[models]'\n"
                "Or set rerank.provider: lexical in config/app.yaml."
            ) from exc

        self.model = model
        self.batch_size = batch_size
        self._encoder = CrossEncoder(model, device=device)

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        pairs = [
            (query, f"{c.chunk.section}\n{c.chunk.text}" if c.chunk.section else c.chunk.text)
            for c in candidates
        ]
        raw = self._encoder.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return _finalize(candidates, [float(score) for score in raw], top_n)


class CohereReranker:
    """Hosted reranker via Cohere's rerank endpoint."""

    name = "cohere"

    def __init__(self, model: str = "rerank-v3.5", api_key: str | None = None) -> None:
        try:
            import cohere
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The Cohere reranker requires an extra install:\n"
                "    pip install 'ask-my-docs[cohere]'"
            ) from exc

        key = api_key or os.getenv("COHERE_API_KEY")
        if not key:
            raise RuntimeError(
                "COHERE_API_KEY is not set. Export it, or switch rerank.provider "
                "to cross-encoder or lexical."
            )
        self.model = model
        self._client = cohere.ClientV2(api_key=key)

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        documents = [c.chunk.text for c in candidates]
        response = self._client.rerank(
            model=self.model,
            query=query,
            documents=documents,
            top_n=min(top_n, len(documents)),
        )
        # Cohere returns only the top_n, already ordered, referencing input indices.
        out: list[RetrievedChunk] = []
        for result in response.results:
            candidate = candidates[result.index]
            score = float(result.relevance_score)
            out.append(candidate.model_copy(update={"rerank_score": score, "score": score}))
        return out


def get_reranker(config) -> Reranker:
    """Construct the reranker described by a :class:`RerankConfig`."""
    if config.provider == "lexical":
        return LexicalReranker()
    if config.provider == "cross-encoder":
        return CrossEncoderReranker(model=config.model, batch_size=config.batch_size)
    if config.provider == "cohere":
        return CohereReranker(model=config.model)
    raise ValueError(f"Unknown rerank provider: {config.provider!r}")
