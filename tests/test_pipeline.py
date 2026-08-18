"""End-to-end pipeline tests: ingestion, incremental re-ingestion, and querying."""

from __future__ import annotations

from pathlib import Path

import pytest

from askmydocs.config import AppConfig, load_config
from askmydocs.pipeline import IndexMismatchError, RAGPipeline


class TestIngestion:
    def test_ingest_populates_every_index(self, pipeline: RAGPipeline) -> None:
        stats = pipeline.stats()
        assert stats["documents"] == 2
        assert stats["chunks"] > 0
        # All three structures must agree: chunk store, vectors, keyword index.
        assert stats["chunks"] == stats["vectors"] == stats["bm25_documents"]

    def test_index_survives_a_process_restart(
        self, config: AppConfig, pipeline: RAGPipeline
    ) -> None:
        before = len(pipeline.chunk_store)
        reopened = RAGPipeline.from_config(config)
        assert len(reopened.chunk_store) == before
        assert reopened.vector_store.count() == before
        assert not reopened.is_empty()

    def test_reingestion_skips_unchanged_documents(
        self, pipeline: RAGPipeline, config: AppConfig
    ) -> None:
        report = pipeline.ingest([config.corpus_dir])
        assert report.documents == 0
        assert report.skipped_unchanged == 2
        assert report.chunks == 0

    def test_changed_document_is_reindexed_and_stale_chunks_removed(
        self, pipeline: RAGPipeline, config: AppConfig
    ) -> None:
        before = len(pipeline.chunk_store)
        path = Path(config.corpus_dir) / "glossary.md"
        path.write_text(
            "# Glossary\n\n## Widget\n\nA widget is a billable unit priced at 4.00 EUR.\n",
            encoding="utf-8",
        )

        report = pipeline.ingest([config.corpus_dir])
        assert report.documents == 1
        assert report.skipped_unchanged == 1
        # The previous revision's chunks must not linger in either index.
        assert len(pipeline.chunk_store) != before or report.chunks > 0
        assert len(pipeline.chunk_store) == pipeline.vector_store.count()
        assert any("4.00 EUR" in c.text for c in pipeline.chunk_store.all())

    def test_reset_clears_everything(self, pipeline: RAGPipeline) -> None:
        pipeline.reset_index()
        assert pipeline.is_empty()
        assert pipeline.vector_store.count() == 0
        assert len(pipeline.bm25) == 0

    def test_ingesting_a_single_file(self, config: AppConfig) -> None:
        pipeline = RAGPipeline.from_config(config)
        report = pipeline.ingest([Path(config.corpus_dir) / "handbook.md"], reset=True)
        assert report.documents == 1
        assert pipeline.stats()["documents"] == 1

    def test_empty_target_directory_is_an_actionable_error(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        pipeline = RAGPipeline.from_config(config)
        with pytest.raises(ValueError, match="No supported documents"):
            pipeline.ingest([empty])

    def test_manifest_records_provenance(self, pipeline: RAGPipeline, config: AppConfig) -> None:
        import json

        manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
        assert manifest["embedder"] == pipeline.embedder.fingerprint
        assert manifest["chunker"]["target_tokens"] == config.ingest.chunk_target_tokens
        assert manifest["profile"] == "offline"
        assert len(manifest["documents"]) == 2


class TestIndexConsistency:
    def test_changing_the_embedder_is_refused_loudly(
        self, config: AppConfig, pipeline: RAGPipeline
    ) -> None:
        # Vectors from different models are not comparable. Silently serving
        # nonsense is far worse than refusing to start.
        mutated = config.model_copy(deep=True)
        mutated.embedding.dimension = 256  # changes the fingerprint

        with pytest.raises(IndexMismatchError, match="--reset"):
            RAGPipeline.from_config(mutated)

    def test_mismatch_is_not_raised_for_an_empty_index(self, config: AppConfig) -> None:
        mutated = config.model_copy(deep=True)
        mutated.embedding.dimension = 256
        # Nothing indexed yet, so there is nothing to be inconsistent with.
        assert RAGPipeline.from_config(mutated).is_empty()


class TestQuerying:
    def test_answers_a_question_from_the_corpus(self, pipeline: RAGPipeline) -> None:
        answer = pipeline.answer("How long do I have to request a refund?")
        assert not answer.abstained
        assert "14" in answer.text
        assert answer.citations
        assert answer.citations[0].source.endswith("handbook.md")

    def test_abstains_on_an_out_of_scope_question(self, pipeline: RAGPipeline) -> None:
        answer = pipeline.answer("What is the capital of Portugal?")
        assert answer.abstained
        assert answer.abstain_reason
        assert not answer.citations

    def test_retrieval_returns_at_most_top_n(self, pipeline: RAGPipeline) -> None:
        assert len(pipeline.retrieve("refund window", top_n=2)) <= 2

    def test_retrieval_trace_is_populated(self, pipeline: RAGPipeline) -> None:
        results = pipeline.retrieve("rate limits")
        assert results
        assert all(r.rerank_score is not None for r in results)
        assert all(r.fusion_score is not None for r in results)

    def test_blank_question_is_rejected(self, pipeline: RAGPipeline) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            pipeline.answer("   ")

    def test_answers_are_deterministic_on_the_offline_profile(self, pipeline: RAGPipeline) -> None:
        # Determinism is what makes the CI eval gate trustworthy.
        first = pipeline.answer("How long do I have to request a refund?")
        second = pipeline.answer("How long do I have to request a refund?")
        assert first.text == second.text
        assert first.cited_chunk_ids() == second.cited_chunk_ids()

    def test_querying_an_empty_index_abstains_rather_than_crashing(self, config: AppConfig) -> None:
        pipeline = RAGPipeline.from_config(config)
        answer = pipeline.answer("anything at all")
        assert answer.abstained


class TestConfigProfiles:
    def test_offline_profile_selects_the_deterministic_stack(self) -> None:
        config = load_config(
            path=Path(__file__).resolve().parents[1] / "config" / "app.yaml", profile="offline"
        )
        assert config.embedding.provider == "hashing"
        assert config.vector_store.provider == "numpy"
        assert config.rerank.provider == "lexical"
        assert config.generation.provider == "extractive"

    def test_full_profile_selects_the_model_backed_stack(self) -> None:
        config = load_config(
            path=Path(__file__).resolve().parents[1] / "config" / "app.yaml", profile="full"
        )
        assert config.embedding.provider == "sentence-transformers"
        assert config.vector_store.provider == "chroma"
        assert config.rerank.provider == "cross-encoder"
        assert config.generation.provider == "anthropic"
        assert config.generation.model == "claude-opus-5"

    def test_full_retrieval_profile_pairs_real_retrieval_with_a_local_generator(self) -> None:
        # Exists so retrieval can be benchmarked against real models without an
        # API key, isolating retrieval quality from generation quality.
        config = load_config(
            path=Path(__file__).resolve().parents[1] / "config" / "app.yaml",
            profile="full-retrieval",
        )
        assert config.embedding.provider == "sentence-transformers"
        assert config.rerank.provider == "cross-encoder"
        assert config.generation.provider == "extractive"
        # Cross-encoder logits are unbounded, so the floor must not be the
        # lexical profile's 0-1 threshold.
        assert config.citations.min_top_score < 0

    def test_unknown_profile_lists_the_valid_ones(self) -> None:
        with pytest.raises(ValueError, match="Available profiles"):
            load_config(
                path=Path(__file__).resolve().parents[1] / "config" / "app.yaml", profile="nope"
            )

    def test_default_profile_works_without_optional_extras(self) -> None:
        # A fresh clone must run with no model downloads and no API key, so the
        # shipped default is the deterministic profile. `full` is opt-in.
        config = load_config(path=Path(__file__).resolve().parents[1] / "config" / "app.yaml")
        assert config.profile == "offline"
        assert config.generation.provider == "extractive"

    def test_chunking_defaults_match_the_brief(self) -> None:
        config = load_config(path=Path(__file__).resolve().parents[1] / "config" / "app.yaml")
        assert 500 <= config.ingest.chunk_target_tokens <= 800
        assert config.ingest.chunk_max_tokens <= 800
        assert config.ingest.chunk_overlap_tokens == 100

    def test_overrides_win_over_the_profile(self, tmp_path: Path) -> None:
        config = load_config(
            path=Path(__file__).resolve().parents[1] / "config" / "app.yaml",
            profile="offline",
            overrides={"storage_dir": str(tmp_path), "retrieval": {"dense_k": 7}},
        )
        assert config.storage_dir == str(tmp_path)
        assert config.retrieval.dense_k == 7
        # Untouched keys keep their profile values.
        assert config.embedding.provider == "hashing"

    def test_invalid_chunk_bounds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="chunk_overlap_tokens"):
            load_config(
                overrides={"ingest": {"chunk_target_tokens": 100, "chunk_overlap_tokens": 100}}
            )
