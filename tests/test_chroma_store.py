"""ChromaVectorStore: the store the model-backed profiles use.

Held to the same behaviour as NumpyVectorStore (tests/test_index.py), so the two
backends stay interchangeable: cosine similarity in [-1, 1], best first.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("chromadb")

from askmydocs.config import VectorStoreConfig  # noqa: E402
from askmydocs.index.vector_store import ChromaVectorStore, get_vector_store  # noqa: E402

X = np.array([1.0, 0.0, 0.0], dtype=np.float32)
Y = np.array([0.0, 1.0, 0.0], dtype=np.float32)
NEAR_X = np.array([0.9, 0.1, 0.0], dtype=np.float32)


def _store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(tmp_path / "chroma", collection="test")


class TestChromaVectorStore:
    def test_query_returns_cosine_similarity_best_first(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(["x", "y", "near-x"], np.vstack([X, Y, NEAR_X]))

        hits = store.query(X, k=3)

        assert [cid for cid, _ in hits] == ["x", "near-x", "y"]
        # Chroma returns cosine *distance*; the adapter converts to similarity.
        scores = dict(hits)
        assert scores["x"] == pytest.approx(1.0, abs=1e-5)
        assert scores["y"] == pytest.approx(0.0, abs=1e-5)

    def test_upsert_replaces_an_existing_vector(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(["a"], X[None, :])
        store.upsert(["a"], Y[None, :])

        assert store.count() == 1
        assert store.query(Y, k=1)[0][0] == "a"
        assert store.query(Y, k=1)[0][1] == pytest.approx(1.0, abs=1e-5)

    def test_delete_and_count(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(["a", "b"], np.vstack([X, Y]))
        store.delete(["a", "missing"])

        assert store.count() == 1
        assert [cid for cid, _ in store.query(X, k=5)] == ["b"]

    def test_empty_store_and_empty_upsert(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert([], np.zeros((0, 3), dtype=np.float32))
        store.delete([])

        assert store.count() == 0
        assert store.query(X, k=5) == []

    def test_k_larger_than_the_collection_is_clamped(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(["a", "b"], np.vstack([X, Y]))
        assert len(store.query(X, k=50)) == 2

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        _store(tmp_path).upsert(["a"], X[None, :])
        reopened = _store(tmp_path)
        reopened.persist()  # a no-op for Chroma, which writes through

        assert reopened.count() == 1
        assert reopened.query(X, k=1)[0][0] == "a"

    def test_reset_empties_the_collection(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(["a", "b"], np.vstack([X, Y]))
        store.reset()

        assert store.count() == 0
        store.upsert(["c"], Y[None, :])  # still usable after a reset
        assert store.count() == 1


def test_factory_builds_each_backend_and_rejects_unknown(tmp_path: Path) -> None:
    chroma = get_vector_store(VectorStoreConfig(provider="chroma"), tmp_path / "c")
    numpy_store = get_vector_store(VectorStoreConfig(provider="numpy"), tmp_path / "n")
    assert isinstance(chroma, ChromaVectorStore)
    assert type(numpy_store).__name__ == "NumpyVectorStore"

    bogus = VectorStoreConfig.model_construct(provider="faiss", collection="x", path=None)
    with pytest.raises(ValueError, match="faiss"):
        get_vector_store(bogus, tmp_path / "f")
