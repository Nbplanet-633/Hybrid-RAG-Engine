"""Embedding providers.

Two implementations behind one protocol:

* ``SentenceTransformerEmbedder`` — the real thing. Local model, no API calls,
  good semantic quality. Requires ``pip install 'ask-my-docs[models]'``.
* ``HashingEmbedder`` — a deterministic hashed character-n-gram + word projection.
  No downloads, no network, identical vectors on every machine. This is what CI
  and the test suite use, and it is a genuinely useful fallback: paired with BM25
  in the hybrid retriever it still produces respectable recall on a small corpus.

The important property for a RAG system is that *the same* embedder is used at
index time and at query time. :func:`get_embedder` is the only construction path,
and the pipeline records the embedder's ``fingerprint`` in the index manifest so a
mismatch is caught loudly at load rather than silently returning noise.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

import numpy as np

from askmydocs.text import normalize, tokenize


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into fixed-width float vectors."""

    dimension: int

    @property
    def fingerprint(self) -> str:
        """Stable identity of this embedder — provider, model, and dimension."""
        ...

    def embed_documents(self, texts: Iterable[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashingEmbedder:
    """Deterministic hashing embedder — the offline / CI provider.

    Builds each vector from three signal families, all hashed into the same
    ``dimension``-wide space:

    1. whole words (weighted by sublinear term frequency and a token-length prior),
    2. word bigrams, which capture short phrases like "dispute fee",
    3. character 4-grams, which give partial robustness to morphology and typos.

    It is a bag-of-features projection, not a learned semantic space, so it will
    not match paraphrases the way a trained model does. Within the hybrid
    retriever that is acceptable: BM25 carries exact terms, and this carries
    fuzzy lexical similarity.
    """

    provider = "hashing"

    def __init__(self, dimension: int = 512, normalize_vectors: bool = True) -> None:
        if dimension < 32:
            raise ValueError("dimension must be >= 32")
        self.dimension = dimension
        self.normalize_vectors = normalize_vectors
        self.model = f"hashing-{dimension}"

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimension}"

    # -- feature hashing ---------------------------------------------------

    def _bucket(self, feature: str) -> tuple[int, float]:
        """Map a feature to (index, sign). The signed hash reduces collision bias."""
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dimension, 1.0 if (value >> 63) & 1 else -1.0

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        words = tokenize(text)
        if not words:
            return vector

        counts: dict[str, int] = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1

        # 1. words — sublinear tf, longer tokens carry more information
        for word, count in counts.items():
            index, sign = self._bucket(f"w:{word}")
            weight = (1.0 + math.log(count)) * (1.0 + 0.1 * min(len(word), 12))
            vector[index] += sign * weight

        # 2. word bigrams — short phrases
        for left, right in zip(words, words[1:], strict=False):
            index, sign = self._bucket(f"b:{left}_{right}")
            vector[index] += sign * 1.4

        # 3. character 4-grams over the normalised string
        flat = re.sub(r"\s+", " ", normalize(text))
        for position in range(len(flat) - 3):
            gram = flat[position : position + 4]
            if gram.isspace():
                continue
            index, sign = self._bucket(f"c:{gram}")
            vector[index] += sign * 0.35

        return vector

    # -- Embedder protocol -------------------------------------------------

    def embed_documents(self, texts: Iterable[str]) -> np.ndarray:
        rows = [self._vector(text) for text in texts]
        if not rows:
            return np.zeros((0, self.dimension), dtype=np.float32)
        matrix = np.vstack(rows)
        return _l2_normalize(matrix) if self.normalize_vectors else matrix

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class SentenceTransformerEmbedder:
    """Local transformer embeddings via sentence-transformers."""

    provider = "sentence-transformers"

    def __init__(
        self,
        model: str = "sentence-transformers/all-MiniLM-L6-v2",
        batch_size: int = 32,
        normalize_vectors: bool = True,
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The sentence-transformers embedder requires an extra install:\n"
                "    pip install 'ask-my-docs[models]'\n"
                "Or switch to the offline profile: ASKMYDOCS_PROFILE=offline"
            ) from exc

        self.model = model
        self.batch_size = batch_size
        self.normalize_vectors = normalize_vectors
        self._encoder = SentenceTransformer(model, device=device)
        # sentence-transformers 6.0 renamed `get_sentence_embedding_dimension` to
        # `get_embedding_dimension`; the old name still works but warns. Support
        # both so the pin can move without an edit here.
        read_dimension = (
            getattr(self._encoder, "get_embedding_dimension", None)
            or self._encoder.get_sentence_embedding_dimension
        )
        self.dimension = int(read_dimension())

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimension}"

    def embed_documents(self, texts: Iterable[str]) -> np.ndarray:
        items = list(texts)
        if not items:
            return np.zeros((0, self.dimension), dtype=np.float32)
        vectors = self._encoder.encode(
            items,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize_vectors,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


def get_embedder(config) -> Embedder:
    """Construct the embedder described by an :class:`EmbeddingConfig`."""
    if config.provider == "hashing":
        return HashingEmbedder(dimension=config.dimension, normalize_vectors=config.normalize)
    if config.provider == "sentence-transformers":
        return SentenceTransformerEmbedder(
            model=config.model,
            batch_size=config.batch_size,
            normalize_vectors=config.normalize,
        )
    raise ValueError(f"Unknown embedding provider: {config.provider!r}")
