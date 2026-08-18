"""BM25 keyword index.

Vector search finds paraphrases; BM25 finds the exact terms a vector model tends
to blur — error codes (``insufficient_funds``), header names
(``Aurora-Signature``), amounts, and version strings. A domain-specific RAG system
without a keyword leg reliably fails on precisely the queries users type.

Backed by ``rank-bm25`` (Okapi BM25) over :func:`askmydocs.text.tokenize`. The
index is rebuilt from the chunk store at load time rather than persisted: it is
an in-memory structure, cheap to build, and rebuilding removes any chance of the
keyword and vector legs drifting out of sync.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from askmydocs.models import Chunk
from askmydocs.text import lexical_tokens

ScoredId = tuple[str, float]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._ids: list[str] = []
        self._corpus: list[list[str]] = []
        self._bm25 = None

    # -- build -------------------------------------------------------------

    def build(self, chunks: Iterable[Chunk]) -> BM25Index:
        """(Re)build the index from chunks. Indexes heading context + body."""
        self._ids = []
        self._corpus = []
        for chunk in chunks:
            # Headings are short and highly discriminative, so include them —
            # a query like "evidence window" should match the section title.
            tokens = lexical_tokens(f"{chunk.title} {chunk.section} {chunk.text}")
            if not tokens:
                continue
            self._ids.append(chunk.chunk_id)
            self._corpus.append(tokens)

        if self._corpus:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self._corpus, k1=self.k1, b=self.b)
        else:
            self._bm25 = None
        return self

    # -- search ------------------------------------------------------------

    def query(self, text: str, k: int) -> list[ScoredId]:
        """Return the top-``k`` (chunk_id, score) pairs. Raw BM25 scores."""
        if self._bm25 is None or k <= 0:
            return []
        tokens = lexical_tokens(text)
        if not tokens:
            return []

        scores: Sequence[float] = self._bm25.get_scores(tokens)
        ranked = sorted(
            ((self._ids[i], float(score)) for i, score in enumerate(scores) if score > 0.0),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:k]

    def idf_map(self) -> dict[str, float]:
        """Inverse document frequency per token, from the built index.

        Exposed because the lexical reranker and the evaluation harness both need
        to know which query terms are actually discriminative. Without IDF, a
        query like "what HTTP status is returned" is dominated by "status" and
        "returned", which appear across the whole corpus.
        """
        if self._bm25 is None:
            return {}
        return dict(getattr(self._bm25, "idf", {}) or {})

    def max_idf(self) -> float:
        """IDF to assign a query term that does not occur in the corpus at all.

        An out-of-vocabulary term is maximally rare, and it can never be matched —
        so weighting it highly is what makes an off-topic question score low
        instead of scoring well on its one incidental term.
        """
        values = self.idf_map().values()
        return max(values) if values else 1.0

    def count(self) -> int:
        return len(self._ids)

    def __len__(self) -> int:
        return len(self._ids)
