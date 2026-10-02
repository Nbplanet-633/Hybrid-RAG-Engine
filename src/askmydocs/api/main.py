"""FastAPI application.

    uvicorn askmydocs.api.main:app       # or: askmydocs serve

Endpoints
---------
``GET  /healthz``   liveness — never touches the index
``GET  /readyz``    readiness — fails while the index is empty, so an orchestrator
                    does not route traffic to a node that can only abstain
``POST /ask``       answer a question, with citations or an explicit abstention
``POST /ingest``    index files, directories, or URLs
``GET  /stats``     index and configuration state
``GET  /prompts``   the versioned prompt registry

Upload library — user documents, in an index separate from the evaluated corpus:

``GET    /library/documents``          list uploaded documents
``POST   /library/documents``          upload one file (multipart ``file``) and index it
``DELETE /library/documents/{doc_id}`` remove a document and its file
``POST   /library/ask``                answer from the library, optionally from chosen documents

The pipeline is built once at startup and shared. It loads local models and opens
a vector store, so building it per request would dominate latency.
"""

from __future__ import annotations

import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from askmydocs import __version__
from askmydocs.config import AppConfig, load_config
from askmydocs.library import (
    DocumentLibrary,
    DocumentNotFound,
    DuplicateDocument,
    FileTooLarge,
    UnsupportedFileType,
    UploadError,
)
from askmydocs.models import Answer, DocumentSummary
from askmydocs.pipeline import RAGPipeline

logger = logging.getLogger("askmydocs.api")

# Module-level state, populated by the lifespan handler.
_state: dict[str, Any] = {
    "pipeline": None,
    "config": None,
    "error": None,
    "library": None,
    "library_error": None,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    top_n: int | None = Field(
        None, ge=1, le=20, description="Override how many reranked passages to use."
    )
    include_retrieval: bool = Field(
        False, description="Include the full retrieval trace in the response."
    )


class CitationOut(BaseModel):
    marker: str
    source: str
    section: str = ""
    title: str = ""
    quote: str = ""
    score: float = 0.0
    chunk_id: str = ""


class RetrievedOut(BaseModel):
    rank: int
    source: str
    section: str = ""
    chunk_id: str
    score: float
    retriever: str
    dense_rank: int | None = None
    lexical_rank: int | None = None


class AskResponse(BaseModel):
    question: str
    answer: str
    abstained: bool
    abstain_reason: str = ""
    citations: list[CitationOut] = Field(default_factory=list)
    confidence: float = 0.0
    grounding_score: float = 0.0
    model: str = ""
    prompt: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    retrieval: list[RetrievedOut] | None = None


class LibraryAskRequest(AskRequest):
    doc_ids: list[str] | None = Field(
        None,
        min_length=1,
        description="Answer only from these documents. Omit to search the whole library.",
    )


class IngestRequest(BaseModel):
    targets: list[str] = Field(
        default_factory=list, description="Paths or URLs. Empty means the configured corpus_dir."
    )
    reset: bool = Field(False, description="Drop the existing index first.")


class IngestResponse(BaseModel):
    documents: int
    chunks: int
    skipped_unchanged: int
    total_chunks: int
    duration_s: float
    sources: list[str]


def _to_response(answer: Answer, include_retrieval: bool) -> AskResponse:
    return AskResponse(
        question=answer.question,
        answer=answer.text,
        abstained=answer.abstained,
        abstain_reason=answer.abstain_reason,
        citations=[
            CitationOut(
                marker=c.marker,
                source=c.source,
                section=c.section,
                title=c.title,
                quote=c.quote,
                score=c.score,
                chunk_id=c.chunk_id,
            )
            for c in answer.citations
        ],
        confidence=answer.confidence,
        grounding_score=answer.grounding_score,
        model=answer.model,
        prompt=f"{answer.prompt_name}.{answer.prompt_version}",
        latency_ms=round(answer.latency_ms, 2),
        input_tokens=answer.usage.input_tokens,
        output_tokens=answer.usage.output_tokens,
        retrieval=(
            [
                RetrievedOut(
                    rank=index,
                    source=item.chunk.source,
                    section=item.chunk.section,
                    chunk_id=item.chunk.chunk_id,
                    score=round(item.score, 4),
                    retriever=item.retriever,
                    dense_rank=item.dense_rank,
                    lexical_rank=item.lexical_rank,
                )
                for index, item in enumerate(answer.retrieved, start=1)
            ]
            if include_retrieval
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Lifespan and dependencies
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the pipeline once, at startup."""
    try:
        config = load_config()
        _state["config"] = config
        _state["pipeline"] = RAGPipeline.from_config(config)
        logger.info(
            "pipeline ready: profile=%s chunks=%d generator=%s",
            config.profile,
            len(_state["pipeline"].chunk_store),
            config.generation.provider,
        )
    except Exception as exc:  # noqa: BLE001
        # Start anyway so /healthz can report the failure. Crash-looping a
        # container hides the reason from anyone reading the logs.
        _state["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("pipeline failed to initialise")
    # Built separately so a broken upload library cannot take /ask down, and
    # vice versa.
    try:
        _state["library"] = DocumentLibrary(_state.get("config") or load_config())
    except Exception as exc:  # noqa: BLE001
        _state["library_error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("upload library failed to initialise")
    yield
    _state.clear()


app = FastAPI(
    title="Ask My Docs",
    description=(
        "Domain-specific RAG with hybrid retrieval, cross-encoder reranking, and "
        "enforced citations. Answers are returned only when the retrieved evidence "
        "supports them; otherwise the API abstains and says why."
    ),
    version=__version__,
    lifespan=lifespan,
)


def get_pipeline() -> RAGPipeline:
    if _state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Pipeline unavailable: {_state['error']}",
        )
    pipeline = _state.get("pipeline")
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Pipeline not initialised"
        )
    return pipeline


def get_library() -> DocumentLibrary:
    if _state.get("library_error"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Upload library unavailable: {_state['library_error']}",
        )
    library = _state.get("library")
    if library is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Upload library not initialised"
        )
    return library


def get_config() -> AppConfig:
    config = _state.get("config")
    if config is None:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    return config


def require_api_key(x_api_key: str | None = Header(None)) -> None:
    """Enforce the shared secret when one is configured."""
    config = _state.get("config")
    expected = getattr(getattr(config, "api", None), "api_key", None) or os.getenv(
        "ASKMYDOCS_API_KEY"
    )
    if expected and x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-API-Key"
        )


# CORS must be configured before the app serves traffic, so this reads the
# config at import time. A failure here is not fatal: the lifespan handler
# reports the real error, and permissive defaults keep local dev working.
_cors_origins = ["*"]
with contextlib.suppress(Exception):
    _cors_origins = load_config().api.cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, Any]:
    """Liveness. Reports an initialisation failure rather than hiding it."""
    if _state.get("error"):
        return {"status": "error", "detail": _state["error"], "version": __version__}
    return {"status": "ok", "version": __version__}


@app.get("/readyz", tags=["ops"])
def readyz(pipeline: RAGPipeline = Depends(get_pipeline)) -> dict[str, Any]:
    """Readiness. An empty index is not ready — it could only ever abstain."""
    if pipeline.is_empty():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Index is empty; run ingestion before serving traffic.",
        )
    return {"status": "ready", "chunks": len(pipeline.chunk_store)}


@app.post("/ask", response_model=AskResponse, tags=["query"])
def ask(
    request: AskRequest,
    pipeline: RAGPipeline = Depends(get_pipeline),
    _: None = Depends(require_api_key),
) -> AskResponse:
    """Answer a question from the indexed documents, or abstain."""
    if pipeline.is_empty():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Index is empty. POST /ingest first.",
        )
    try:
        answer = pipeline.answer(request.question, top_n=request.top_n)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Generation failed: {type(exc).__name__}",
        ) from exc
    return _to_response(answer, request.include_retrieval)


@app.post("/ingest", response_model=IngestResponse, tags=["index"])
def ingest(
    request: IngestRequest,
    pipeline: RAGPipeline = Depends(get_pipeline),
    _: None = Depends(require_api_key),
) -> IngestResponse:
    """Index documents from paths or URLs."""
    try:
        report = pipeline.ingest(request.targets or None, reset=request.reset)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return IngestResponse(
        documents=report.documents,
        chunks=report.chunks,
        skipped_unchanged=report.skipped_unchanged,
        total_chunks=len(pipeline.chunk_store),
        duration_s=report.duration_s,
        sources=report.sources,
    )


@app.get("/stats", tags=["ops"])
def stats(pipeline: RAGPipeline = Depends(get_pipeline)) -> dict[str, Any]:
    """Index and configuration state."""
    return pipeline.stats()


@app.get("/prompts", tags=["ops"])
def prompts(pipeline: RAGPipeline = Depends(get_pipeline)) -> dict[str, Any]:
    """The versioned prompt registry, and which version is active."""
    active = pipeline.answerer.prompt
    return {
        "active": {
            "name": active.name,
            "version": active.version,
            "content_hash": active.content_hash,
        },
        "available": pipeline.prompts.describe(),
    }


# ---------------------------------------------------------------------------
# Upload library
# ---------------------------------------------------------------------------

_UPLOAD_ERROR_STATUS: dict[type[UploadError], int] = {
    UnsupportedFileType: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLarge: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    DuplicateDocument: status.HTTP_409_CONFLICT,
}


@app.get("/library/documents", response_model=list[DocumentSummary], tags=["library"])
def list_documents(library: DocumentLibrary = Depends(get_library)) -> list[DocumentSummary]:
    """Every uploaded document."""
    return library.documents()


@app.post(
    "/library/documents",
    response_model=DocumentSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["library"],
)
def upload_document(
    file: UploadFile = File(..., description="A PDF, Markdown, text, or HTML file."),
    library: DocumentLibrary = Depends(get_library),
    _: None = Depends(require_api_key),
) -> DocumentSummary:
    """Upload one file and index it. Re-uploading a filename replaces that document."""
    # Read one byte past the limit: enough to know it is too large without
    # holding an arbitrarily large body in memory.
    data = file.file.read(library.max_bytes + 1)
    try:
        return library.add(file.filename or "", data)
    except UploadError as exc:
        code = _UPLOAD_ERROR_STATUS.get(type(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.delete(
    "/library/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["library"],
)
def delete_document(
    doc_id: str,
    library: DocumentLibrary = Depends(get_library),
    _: None = Depends(require_api_key),
) -> Response:
    """Remove a document from the library and delete its file."""
    try:
        library.delete(doc_id)
    except DocumentNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No document {doc_id!r}"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/library/ask", response_model=AskResponse, tags=["library"])
def ask_library(
    request: LibraryAskRequest,
    library: DocumentLibrary = Depends(get_library),
    _: None = Depends(require_api_key),
) -> AskResponse:
    """Answer from uploaded documents, or abstain."""
    if library.is_empty():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The library is empty. POST /library/documents first.",
        )
    try:
        answer = library.ask(request.question, doc_ids=request.doc_ids, top_n=request.top_n)
    except DocumentNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown document(s): {exc.args[0]}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Generation failed: {type(exc).__name__}",
        ) from exc
    return _to_response(answer, request.include_retrieval)
