"""Hybrid retrieval: dense + BM25, fused with Reciprocal Rank Fusion.

Why RRF rather than a weighted score sum
----------------------------------------
Cosine similarity lives in ``[-1, 1]``; BM25 scores are unbounded and scale with
corpus statistics and query length. Adding them requires a normalisation that has
to be re-tuned whenever the corpus changes. RRF sidesteps this by discarding the
scores and fusing *ranks*:

    score(d) = sum over retrievers of  1 / (k + rank_r(d))

with ``k = 60`` from Cormack et al. (2009). It is parameter-light, robust, and it
rewards documents that both retrievers agree on — which is exactly the signal we
want before spending a cross-encoder pass on the candidates.

The original per-retriever scores and ranks are preserved on every
:class:`~askmydocs.models.RetrievedChunk` so the fusion is auditable rather than
a black box.
"""

from __future__ import annotations

from askmydocs.index.bm25 import BM25Index
from askmydocs.index.chunk_store import ChunkStore
from askmydocs.index.embeddings import Embedder
from askmydocs.index.vector_store import VectorStore
from askmydocs.models import RetrievedChunk


class HybridRetriever:
    def __init__(
        self,
        chunk_store: ChunkStore,
        vector_store: VectorStore,
        bm25: BM25Index,
        embedder: Embedder,
        dense_k: int = 20,
        lexical_k: int = 20,
        fusion: str = "rrf",
        rrf_k: int = 60,
        candidates: int = 24,
    ) -> None:
        self.chunk_store = chunk_store
        self.vector_store = vector_store
        self.bm25 = bm25
        self.embedder = embedder
        self.dense_k = dense_k
        self.lexical_k = lexical_k
        self.fusion = fusion
        self.rrf_k = rrf_k
        self.candidates = candidates

    # -- legs --------------------------------------------------------------

    def _dense(self, query: str, k: int) -> list[tuple[str, float]]:
        vector = self.embedder.embed_query(query)
        return self.vector_store.query(vector, k)

    def _lexical(self, query: str, k: int) -> list[tuple[str, float]]:
        return self.bm25.query(query, k)

    # -- fusion ------------------------------------------------------------

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """Retrieve fused candidates for ``query``, best first."""
        limit = top_k or self.candidates
        if not query.strip():
            return []

        dense = self._dense(query, self.dense_k) if self.fusion != "lexical_only" else []
        lexical = self._lexical(query, self.lexical_k) if self.fusion != "dense_only" else []

        dense_scores = dict(dense)
        lexical_scores = dict(lexical)
        dense_ranks = {cid: rank for rank, (cid, _) in enumerate(dense, start=1)}
        lexical_ranks = {cid: rank for rank, (cid, _) in enumerate(lexical, start=1)}

        fused: dict[str, float] = {}
        if self.fusion == "rrf":
            for ranks in (dense_ranks, lexical_ranks):
                for chunk_id, rank in ranks.items():
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
        elif self.fusion == "dense_only":
            fused = dict(dense_scores)
        elif self.fusion == "lexical_only":
            fused = dict(lexical_scores)
        else:
            raise ValueError(f"Unknown fusion strategy: {self.fusion!r}")

        if not fused:
            return []

        ordered = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:limit]

        results: list[RetrievedChunk] = []
        for chunk_id, score in ordered:
            chunk = self.chunk_store.get(chunk_id)
            if chunk is None:
                # The vector store references a chunk the store no longer has.
                # Skip rather than fail: the next ingest run will reconcile it.
                continue
            in_dense = chunk_id in dense_ranks
            in_lexical = chunk_id in lexical_ranks
            results.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    fusion_score=score,
                    dense_score=dense_scores.get(chunk_id),
                    lexical_score=lexical_scores.get(chunk_id),
                    dense_rank=dense_ranks.get(chunk_id),
                    lexical_rank=lexical_ranks.get(chunk_id),
                    retriever=(
                        "hybrid" if in_dense and in_lexical else "dense" if in_dense else "lexical"
                    ),
                )
            )
        return results
