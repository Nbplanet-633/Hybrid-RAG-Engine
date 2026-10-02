"""Core domain models.

Everything that crosses a module boundary in this project is one of these types.
They are Pydantic models so that the FastAPI layer, the JSONL stores, and the
evaluation harness all share one schema definition.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    """A source document, before chunking."""

    doc_id: str = Field(..., description="Stable identifier, derived from the source path.")
    source: str = Field(..., description="Original path or URL.")
    title: str = ""
    text: str
    content_type: str = Field("markdown", description="markdown | pdf | html | text")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """Hash of the raw text — used to skip re-ingesting unchanged documents."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


class Chunk(BaseModel):
    """A retrievable unit of text."""

    chunk_id: str
    doc_id: str
    source: str
    title: str = ""
    # Heading breadcrumb ("Refunds > Evidence window"). Kept out of the embedded
    # text body but prepended at embedding time so headings contribute to recall.
    section: str = ""
    text: str
    ordinal: int = Field(0, description="Position of this chunk within its document.")
    token_count: int = 0
    # Character offsets into the source document, so a citation can be traced
    # back to the exact span of the original file.
    start_char: int = 0
    end_char: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def embedding_text(self) -> str:
        """Text actually handed to the embedder: heading context + body."""
        if self.section:
            return (
                f"{self.title} — {self.section}\n\n{self.text}"
                if self.title
                else f"{self.section}\n\n{self.text}"
            )
        return f"{self.title}\n\n{self.text}" if self.title else self.text

    @property
    def locator(self) -> str:
        """Human-readable pointer used in citations."""
        return f"{self.source}#{self.section}" if self.section else self.source


class RetrievedChunk(BaseModel):
    """A chunk plus the scores that got it here."""

    chunk: Chunk
    score: float = Field(0.0, description="Final score after the last scoring stage.")
    dense_score: float | None = None
    lexical_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    retriever: str = Field("hybrid", description="Which retriever surfaced this chunk.")


class Citation(BaseModel):
    """A source referenced by the generated answer."""

    marker: str = Field(..., description='The inline marker, e.g. "S1".')
    chunk_id: str
    source: str
    title: str = ""
    section: str = ""
    quote: str = Field("", description="The most relevant sentence from the chunk.")
    score: float = 0.0


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class Answer(BaseModel):
    """The full result of answering one question, including its audit trail."""

    question: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    # Grounding score in [0,1]: how much of the answer's content is supported by
    # the cited chunks. Used both at request time (to gate) and in evaluation.
    grounding_score: float = 0.0
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    prompt_name: str = ""
    prompt_version: str = ""
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    # Generation only: the answerer's own time, which is what the eval gates on.
    latency_ms: float = 0.0
    # The whole question, retrieval and reranking included. Set by the pipeline;
    # on a CPU the cross-encoder can make this ~40x latency_ms.
    total_ms: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)

    def cited_chunk_ids(self) -> list[str]:
        return [c.chunk_id for c in self.citations]


class DocumentSummary(BaseModel):
    """One document in the upload library, as listed to a user."""

    doc_id: str
    filename: str
    title: str = ""
    content_type: str = ""
    chunks: int = 0
    size_bytes: int = 0


class IngestReport(BaseModel):
    """Summary returned by an ingestion run."""

    documents: int = 0
    chunks: int = 0
    skipped_unchanged: int = 0
    sources: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
