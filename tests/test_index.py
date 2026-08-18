"""Tests for chunk storage, vector search, BM25, and RRF fusion."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from askmydocs.index.bm25 import BM25Index
from askmydocs.index.chunk_store import ChunkStore
from askmydocs.index.embeddings import HashingEmbedder
from askmydocs.index.hybrid import HybridRetriever
from askmydocs.index.vector_store import NumpyVectorStore
from askmydocs.models import Chunk


def _chunk(chunk_id: str, text: str, source: str = "a.md", section: str = "") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=source.split(".")[0],
        source=source,
        title="T",
        section=section,
        text=text,
        token_count=len(text.split()),
    )


CHUNKS = [
    _chunk("c1", "The dispute fee is 15.00 EUR and is refunded if you win.", "disputes.md", "Fees"),
    _chunk(
        "c2",
        "Payouts are daily and the minimum payout amount is 10.00 EUR.",
        "payouts.md",
        "Payouts",
    ),
    _chunk("c3", "Rate limits return HTTP 429 with a Retry-After header.", "limits.md", "Limits"),
    _chunk("c4", "Widgets are blue and sprockets are green.", "misc.md", "Colours"),
]


class TestChunkStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        store = ChunkStore(tmp_path / "chunks.jsonl")
        store.add(CHUNKS)
        store.save()

        reloaded = ChunkStore(tmp_path / "chunks.jsonl").load()
        assert len(reloaded) == 4
        assert reloaded.get("c1").text == CHUNKS[0].text
        assert reloaded.get("c1").section == "Fees"

    def test_add_replaces_by_id(self, tmp_path: Path) -> None:
        store = ChunkStore(tmp_path / "chunks.jsonl")
        store.add([_chunk("c1", "original")])
        store.add([_chunk("c1", "updated")])
        assert len(store) == 1
        assert store.get("c1").text == "updated"

    def test_delete_document_removes_all_its_chunks(self, tmp_path: Path) -> None:
        store = ChunkStore(tmp_path / "chunks.jsonl")
        store.add(CHUNKS)
        removed = store.delete_document("disputes")
        assert removed == ["c1"]
        assert store.get("c1") is None
        assert len(store) == 3

    def test_save_is_atomic_and_leaves_no_temp_files(self, tmp_path: Path) -> None:
        store = ChunkStore(tmp_path / "chunks.jsonl")
        store.add(CHUNKS)
        store.save()
        store.save()
        assert not list(tmp_path.glob("*.tmp"))

    def test_corrupt_file_raises_with_the_line_number(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.jsonl"
        path.write_text(
            '{"chunk_id": "ok", "doc_id": "d", "source": "s", "text": "t"}\nNOT JSON\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="chunks.jsonl:2"):
            ChunkStore(path).load()

    def test_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        assert len(ChunkStore(tmp_path / "absent.jsonl").load()) == 0

    def test_membership_and_iteration(self, tmp_path: Path) -> None:
        store = ChunkStore(tmp_path / "c.jsonl")
        store.add(CHUNKS)
        assert "c1" in store
        assert "nope" not in store
        assert {c.chunk_id for c in store} == {"c1", "c2", "c3", "c4"}
        assert store.sources() == {"disputes.md", "payouts.md", "limits.md", "misc.md"}


class TestHashingEmbedder:
    def test_is_deterministic_across_instances(self) -> None:
        a = HashingEmbedder(dimension=128).embed_query("the dispute fee")
        b = HashingEmbedder(dimension=128).embed_query("the dispute fee")
        assert np.allclose(a, b)

    def test_vectors_are_unit_length_when_normalised(self) -> None:
        matrix = HashingEmbedder(dimension=128).embed_documents(["one text", "another text"])
        assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)

    def test_similar_text_scores_higher_than_unrelated(self) -> None:
        embedder = HashingEmbedder(dimension=512)
        query = embedder.embed_query("dispute fee refunded")
        near = embedder.embed_query("the dispute fee is refunded if you win")
        far = embedder.embed_query("widgets are blue and sprockets are green")
        assert float(query @ near) > float(query @ far)

    def test_empty_input_returns_an_empty_matrix(self) -> None:
        assert HashingEmbedder(dimension=64).embed_documents([]).shape == (0, 64)

    def test_fingerprint_encodes_provider_and_dimension(self) -> None:
        assert HashingEmbedder(dimension=64).fingerprint == "hashing:hashing-64:64"

    def test_rejects_a_tiny_dimension(self) -> None:
        with pytest.raises(ValueError, match="dimension"):
            HashingEmbedder(dimension=8)


class TestNumpyVectorStore:
    def test_upsert_query_and_ordering(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a", "b"], np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
        results = store.query(np.array([1.0, 0.0], dtype=np.float32), k=2)
        assert results[0][0] == "a"
        assert results[0][1] == pytest.approx(1.0)
        assert results[1][1] == pytest.approx(0.0)

    def test_upsert_updates_in_place(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a"], np.array([[1.0, 0.0]], dtype=np.float32))
        store.upsert(["a"], np.array([[0.0, 1.0]], dtype=np.float32))
        assert store.count() == 1
        assert store.query(np.array([0.0, 1.0], dtype=np.float32), k=1)[0][1] == pytest.approx(1.0)

    def test_delete(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a", "b"], np.eye(2, dtype=np.float32))
        store.delete(["a"])
        assert store.count() == 1
        assert store.query(np.array([1.0, 0.0], dtype=np.float32), k=5)[0][0] == "b"

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a"], np.array([[1.0, 0.0]], dtype=np.float32))
        store.persist()
        assert NumpyVectorStore(tmp_path, "test").count() == 1

    def test_empty_store_returns_no_results(self, tmp_path: Path) -> None:
        assert NumpyVectorStore(tmp_path, "test").query(np.array([1.0]), k=5) == []

    def test_k_larger_than_the_corpus_is_clamped(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a"], np.array([[1.0, 0.0]], dtype=np.float32))
        assert len(store.query(np.array([1.0, 0.0], dtype=np.float32), k=100)) == 1

    def test_dimension_change_is_rejected_with_actionable_advice(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a"], np.zeros((1, 4), dtype=np.float32))
        with pytest.raises(ValueError, match="--reset"):
            store.upsert(["b"], np.zeros((1, 8), dtype=np.float32))

    def test_mismatched_ids_and_vectors_are_rejected(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        with pytest.raises(ValueError, match="same length"):
            store.upsert(["a", "b"], np.zeros((1, 4), dtype=np.float32))

    def test_reset_clears_disk_state(self, tmp_path: Path) -> None:
        store = NumpyVectorStore(tmp_path, "test")
        store.upsert(["a"], np.zeros((1, 4), dtype=np.float32))
        store.persist()
        store.reset()
        assert store.count() == 0
        assert NumpyVectorStore(tmp_path, "test").count() == 0


class TestBM25:
    def test_exact_term_wins(self) -> None:
        index = BM25Index().build(CHUNKS)
        assert index.query("429 Retry-After", k=1)[0][0] == "c3"

    def test_stemmed_query_matches_inflected_text(self) -> None:
        # "refunded" in the query must match "refunded"/"refunds" in the corpus.
        index = BM25Index().build(CHUNKS)
        assert index.query("refunding disputes", k=1)[0][0] == "c1"

    def test_headings_are_indexed(self) -> None:
        index = BM25Index().build(CHUNKS)
        assert any(cid == "c4" for cid, _ in index.query("colours", k=4))

    def test_empty_index_and_empty_query(self) -> None:
        assert BM25Index().build([]).query("anything", k=5) == []
        assert BM25Index().build(CHUNKS).query("", k=5) == []

    def test_zero_scoring_documents_are_excluded(self) -> None:
        results = BM25Index().build(CHUNKS).query("sprockets", k=10)
        assert all(score > 0 for _, score in results)

    def test_idf_map_marks_out_of_vocabulary_terms(self) -> None:
        index = BM25Index().build(CHUNKS)
        idf = index.idf_map()
        assert "disput" in idf
        assert "salesforc" not in idf
        assert index.max_idf() >= max(idf.values())


class TestHybridFusion:
    def _retriever(self, tmp_path: Path, fusion: str = "rrf") -> HybridRetriever:
        store = ChunkStore(tmp_path / "c.jsonl")
        store.add(CHUNKS)
        embedder = HashingEmbedder(dimension=256)
        vectors = NumpyVectorStore(tmp_path, "v")
        vectors.upsert(
            [c.chunk_id for c in CHUNKS],
            embedder.embed_documents([c.embedding_text for c in CHUNKS]),
        )
        return HybridRetriever(
            chunk_store=store,
            vector_store=vectors,
            bm25=BM25Index().build(CHUNKS),
            embedder=embedder,
            dense_k=4,
            lexical_k=4,
            fusion=fusion,
            rrf_k=60,
            candidates=4,
        )

    def test_rrf_ranks_the_relevant_chunk_first(self, tmp_path: Path) -> None:
        results = self._retriever(tmp_path).retrieve("How much is the dispute fee?")
        assert results[0].chunk.chunk_id == "c1"

    def test_rrf_records_both_legs_for_audit(self, tmp_path: Path) -> None:
        top = self._retriever(tmp_path).retrieve("dispute fee refunded")[0]
        assert top.fusion_score is not None
        assert top.retriever in {"hybrid", "dense", "lexical"}
        if top.retriever == "hybrid":
            assert top.dense_rank is not None and top.lexical_rank is not None

    def test_rrf_score_matches_the_formula(self, tmp_path: Path) -> None:
        top = self._retriever(tmp_path).retrieve("429 Retry-After")[0]
        expected = sum(
            1.0 / (60 + rank) for rank in (top.dense_rank, top.lexical_rank) if rank is not None
        )
        assert top.fusion_score == pytest.approx(expected)

    def test_single_leg_modes(self, tmp_path: Path) -> None:
        dense_only = self._retriever(tmp_path, "dense_only").retrieve("dispute fee")
        assert all(r.lexical_rank is None for r in dense_only)
        lexical_only = self._retriever(tmp_path, "lexical_only").retrieve("dispute fee")
        assert all(r.dense_rank is None for r in lexical_only)

    def test_unknown_fusion_strategy_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="fusion"):
            self._retriever(tmp_path, "magic").retrieve("dispute fee")

    def test_blank_query_returns_nothing(self, tmp_path: Path) -> None:
        assert self._retriever(tmp_path).retrieve("   ") == []

    def test_orphaned_vector_ids_are_skipped(self, tmp_path: Path) -> None:
        # A vector whose chunk was deleted must not crash retrieval; the next
        # ingest reconciles it.
        retriever = self._retriever(tmp_path)
        retriever.chunk_store.delete_document("disputes")
        results = retriever.retrieve("dispute fee")
        assert all(r.chunk.chunk_id != "c1" for r in results)
