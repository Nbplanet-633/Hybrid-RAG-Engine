"""The RAG pipeline: one object that owns ingestion and question answering.

    pipeline = RAGPipeline.from_config(load_config())
    pipeline.ingest(["data/corpus"])
    answer = pipeline.answer("How long do I have to respond to a dispute?")

Query path::

    question
      -> dense retrieval (embeddings -> vector store)   ─┐
      -> BM25 retrieval (keyword index)                 ─┴─> RRF fusion
      -> cross-encoder rerank (precision)
      -> citation-enforced generation (or abstention)

Ingestion is **incremental**. An index manifest records the content hash of every
ingested document, so re-running ingestion only re-embeds what actually changed.
The manifest also records the embedder's fingerprint: querying an index that was
built with a different embedding model returns nonsense, so that mismatch is
detected at load and raised loudly rather than silently degrading retrieval.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from askmydocs.config import AppConfig, load_config
from askmydocs.generation.answerer import Answerer
from askmydocs.generation.llm import get_llm
from askmydocs.generation.prompts import PromptLibrary
from askmydocs.index.bm25 import BM25Index
from askmydocs.index.chunk_store import ChunkStore
from askmydocs.index.embeddings import get_embedder
from askmydocs.index.hybrid import HybridRetriever
from askmydocs.index.vector_store import get_vector_store
from askmydocs.ingest.chunker import Chunker
from askmydocs.ingest.loaders import load_paths
from askmydocs.models import Answer, Chunk, Document, IngestReport, RetrievedChunk
from askmydocs.rerank import get_reranker


class IndexMismatchError(RuntimeError):
    """Raised when the on-disk index was built with a different embedder."""


class RAGPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

        self.chunk_store = ChunkStore(config.chunk_store_path).load()
        self.embedder = get_embedder(config.embedding)
        self.vector_store = get_vector_store(config.vector_store, config.vector_store_path)
        self.bm25 = BM25Index().build(self.chunk_store.all())

        self.chunker = Chunker(
            target_tokens=config.ingest.chunk_target_tokens,
            max_tokens=config.ingest.chunk_max_tokens,
            overlap_tokens=config.ingest.chunk_overlap_tokens,
            min_tokens=config.ingest.min_chunk_tokens,
            section_break_ratio=config.ingest.section_break_ratio,
        )
        self.retriever = HybridRetriever(
            chunk_store=self.chunk_store,
            vector_store=self.vector_store,
            bm25=self.bm25,
            embedder=self.embedder,
            dense_k=config.retrieval.dense_k,
            lexical_k=config.retrieval.lexical_k,
            fusion=config.retrieval.fusion,
            rrf_k=config.retrieval.rrf_k,
            candidates=config.retrieval.candidates,
        )
        self.reranker = get_reranker(config.rerank) if config.rerank.enabled else None
        # The lexical reranker needs corpus IDF statistics. Bind lazily through
        # `self.bm25` rather than the current object, because the index is
        # replaced (not mutated) on every ingest.
        if hasattr(self.reranker, "set_idf_provider"):
            self.reranker.set_idf_provider(lambda: self.bm25.idf_map(), lambda: self.bm25.max_idf())
        self.prompts = PromptLibrary(config.generation.prompts_dir)
        self.answerer = Answerer(
            llm=get_llm(config.generation),
            prompts=self.prompts,
            generation=config.generation,
            citations=config.citations,
        )

        if hasattr(self.answerer, "set_idf_provider"):
            self.answerer.set_idf_provider(lambda: self.bm25.idf_map(), lambda: self.bm25.max_idf())

        self._manifest = self._load_manifest()
        self._verify_manifest()

    @classmethod
    def from_config(cls, config: AppConfig | None = None, **kwargs: Any) -> RAGPipeline:
        return cls(config or load_config(**kwargs))

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        path = self.config.manifest_path
        if not path.exists():
            return {"documents": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"documents": {}}
        data.setdefault("documents", {})
        return data

    def _verify_manifest(self) -> None:
        """Fail loudly when the index does not match the configured embedder."""
        recorded = self._manifest.get("embedder")
        if not recorded or self.vector_store.count() == 0:
            return
        if recorded != self.embedder.fingerprint:
            raise IndexMismatchError(
                f"The index at {self.config.storage_path} was built with embedder "
                f"{recorded!r} but the current configuration uses "
                f"{self.embedder.fingerprint!r}. Vectors from different models are not "
                "comparable. Re-ingest with --reset, or switch back to the original "
                "embedding configuration."
            )

    def _save_manifest(self) -> None:
        self.config.storage_path.mkdir(parents=True, exist_ok=True)
        self._manifest.update(
            {
                "embedder": self.embedder.fingerprint,
                "vector_store": f"{self.config.vector_store.provider}:{self.config.vector_store.collection}",
                "chunk_count": len(self.chunk_store),
                "profile": self.config.profile,
                "chunker": {
                    "target_tokens": self.config.ingest.chunk_target_tokens,
                    "max_tokens": self.config.ingest.chunk_max_tokens,
                    "overlap_tokens": self.config.ingest.chunk_overlap_tokens,
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.config.manifest_path.write_text(
            json.dumps(self._manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def reset_index(self) -> None:
        """Drop every index artifact. Used by ``ingest --reset``."""
        self.chunk_store.reset()
        self.vector_store.reset()
        self.bm25 = BM25Index().build([])
        self.retriever.bm25 = self.bm25
        self._manifest = {"documents": {}}
        self.config.manifest_path.unlink(missing_ok=True)

    def _embed_and_store(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        batch_size = max(1, self.config.embedding.batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embedder.embed_documents([c.embedding_text for c in batch])
            self.vector_store.upsert([c.chunk_id for c in batch], vectors)

    def ingest_documents(self, documents: Iterable[Document], reset: bool = False) -> IngestReport:
        """Chunk, embed, and index already-loaded documents."""
        started = time.perf_counter()
        if reset:
            self.reset_index()

        docs = list(documents)
        known: dict[str, Any] = self._manifest.setdefault("documents", {})

        changed: list[Document] = []
        skipped = 0
        for document in docs:
            record = known.get(document.doc_id)
            if record and record.get("content_hash") == document.content_hash:
                skipped += 1
                continue
            changed.append(document)

        total_chunks = 0
        for document in changed:
            # Replace, don't append: a re-ingested document must not leave the
            # chunks of its previous revision behind in either index.
            stale = self.chunk_store.delete_document(document.doc_id)
            if stale:
                self.vector_store.delete(stale)

            chunks = self.chunker.chunk_document(document)
            self.chunk_store.add(chunks)
            self._embed_and_store(chunks)
            total_chunks += len(chunks)

            known[document.doc_id] = {
                "source": document.source,
                "title": document.title,
                "content_hash": document.content_hash,
                "chunks": len(chunks),
                "content_type": document.content_type,
            }

        self.chunk_store.save()
        self.vector_store.persist()
        # BM25 is an in-memory structure rebuilt from the chunk store, which makes
        # keyword/vector divergence structurally impossible.
        self.bm25 = BM25Index().build(self.chunk_store.all())
        self.retriever.bm25 = self.bm25
        self._save_manifest()

        return IngestReport(
            documents=len(changed),
            chunks=total_chunks,
            skipped_unchanged=skipped,
            sources=[d.source for d in changed],
            duration_s=round(time.perf_counter() - started, 3),
        )

    def ingest(
        self, targets: Iterable[str | Path] | None = None, reset: bool = False
    ) -> IngestReport:
        """Load files, directories, or URLs and index them."""
        paths = list(targets) if targets else [self.config.corpus_dir]
        documents = load_paths(
            paths,
            include_globs=self.config.ingest.include_globs,
            exclude_globs=self.config.ingest.exclude_globs,
        )
        if not documents:
            raise ValueError(
                f"No supported documents found in {paths}. "
                f"Include patterns: {self.config.ingest.include_globs}"
            )
        return self.ingest_documents(documents, reset=reset)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def retrieve(
        self,
        question: str,
        top_n: int | None = None,
        doc_ids: Iterable[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Run the retrieval half of the pipeline: hybrid search, then rerank.

        ``doc_ids`` limits the search to those documents; ``None`` searches all.
        """
        candidates = self.retriever.retrieve(question, doc_ids=doc_ids)
        if not candidates:
            return []
        limit = top_n or self.config.rerank.top_n
        if self.reranker is None:
            return candidates[:limit]
        return self.reranker.rerank(question, candidates, limit)

    def answer(
        self,
        question: str,
        top_n: int | None = None,
        doc_ids: Iterable[str] | None = None,
    ) -> Answer:
        """Answer a question, or abstain. Never returns an uncited claim."""
        if not question or not question.strip():
            raise ValueError("question must be a non-empty string")
        retrieved = self.retrieve(question, top_n=top_n, doc_ids=doc_ids)
        return self.answerer.answer(question.strip(), retrieved)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def documents(self) -> dict[str, dict[str, Any]]:
        """Indexed documents keyed by doc_id, as recorded in the manifest."""
        return {doc_id: dict(record) for doc_id, record in self._manifest["documents"].items()}

    def delete_document(self, doc_id: str) -> bool:
        """Remove one document from every index. Returns False if it was not indexed."""
        if doc_id not in self._manifest["documents"]:
            return False
        stale = self.chunk_store.delete_document(doc_id)
        if stale:
            self.vector_store.delete(stale)
        del self._manifest["documents"][doc_id]

        self.chunk_store.save()
        self.vector_store.persist()
        self.bm25 = BM25Index().build(self.chunk_store.all())
        self.retriever.bm25 = self.bm25
        self._save_manifest()
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "profile": self.config.profile,
            "documents": len(self._manifest.get("documents", {})),
            "chunks": len(self.chunk_store),
            "vectors": self.vector_store.count(),
            "bm25_documents": len(self.bm25),
            "embedder": self.embedder.fingerprint,
            "vector_store": self.config.vector_store.provider,
            "reranker": getattr(self.reranker, "name", "disabled"),
            "generator": f"{self.config.generation.provider}:{self.config.generation.model}",
            "prompt": f"{self.answerer.prompt.name}.{self.answerer.prompt.version}",
            "prompt_hash": self.answerer.prompt.content_hash,
            "storage_dir": str(self.config.storage_path),
            "updated_at": self._manifest.get("updated_at"),
            "sources": sorted(self.chunk_store.sources()),
        }

    def is_empty(self) -> bool:
        return len(self.chunk_store) == 0
