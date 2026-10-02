"""Typed configuration, loaded from YAML with profile and environment overrides.

Precedence (lowest to highest):

    config/app.yaml defaults
      -> the selected profile's overrides
        -> environment variables
          -> explicit overrides passed to load_config()

Profiles exist so the same code path can run two very different stacks:

* ``full``    — sentence-transformers embeddings, cross-encoder reranking, Claude generation.
* ``offline`` — deterministic hashing embeddings, lexical reranking, extractive generation.
                No network, no model downloads, no API key. This is what CI runs.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path("config/app.yaml")


class IngestConfig(BaseModel):
    # The project brief calls for 500-800 token chunks with 100 tokens of overlap.
    chunk_target_tokens: int = Field(600, ge=64)
    chunk_max_tokens: int = Field(800, ge=64)
    chunk_overlap_tokens: int = Field(100, ge=0)
    # Chunks below this are merged into their neighbour rather than indexed alone;
    # a 12-token fragment is almost never a useful retrieval unit.
    min_chunk_tokens: int = Field(40, ge=0)
    # A heading boundary is only honoured once the current chunk reaches this
    # fraction of the target size — see Chunker.section_break_min_tokens.
    # Swept on the golden set; see README "Chunking trade-off".
    section_break_ratio: float = Field(1.0, ge=0.0, le=1.0)
    include_globs: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt", "**/*.pdf", "**/*.html"]
    )
    exclude_globs: list[str] = Field(default_factory=lambda: ["**/.*", "**/node_modules/**"])

    @model_validator(mode="after")
    def _check_bounds(self) -> IngestConfig:
        if self.chunk_max_tokens < self.chunk_target_tokens:
            raise ValueError("chunk_max_tokens must be >= chunk_target_tokens")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be < chunk_target_tokens")
        return self


class EmbeddingConfig(BaseModel):
    provider: Literal["sentence-transformers", "hashing"] = "sentence-transformers"
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Only used by the hashing provider.
    dimension: int = Field(512, ge=32)
    batch_size: int = Field(32, ge=1)
    normalize: bool = True


class VectorStoreConfig(BaseModel):
    provider: Literal["chroma", "numpy"] = "chroma"
    collection: str = "askmydocs"
    path: str | None = None  # defaults to <storage_dir>/<provider>


class RetrievalConfig(BaseModel):
    dense_k: int = Field(20, ge=1)
    lexical_k: int = Field(20, ge=1)
    fusion: Literal["rrf", "dense_only", "lexical_only"] = "rrf"
    # Reciprocal Rank Fusion smoothing constant. 60 is the value from the original
    # Cormack et al. paper and works well without tuning.
    rrf_k: int = Field(60, ge=1)
    # How many fused candidates to hand to the reranker.
    candidates: int = Field(24, ge=1)


class RerankConfig(BaseModel):
    enabled: bool = True
    provider: Literal["cross-encoder", "cohere", "lexical"] = "cross-encoder"
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    top_n: int = Field(5, ge=1)
    batch_size: int = Field(16, ge=1)


class GenerationConfig(BaseModel):
    provider: Literal["anthropic", "extractive"] = "anthropic"
    model: str = "claude-opus-5"
    # Hard cap on thinking + answer tokens combined, so it carries headroom well
    # above the expected answer length.
    max_tokens: int = Field(2048, ge=64)
    # Claude effort level; ignored by the extractive provider.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Context budget. Enforced by the answerer so prompt size is bounded by
    # configuration rather than by whatever the model's context window allows.
    max_chars_per_source: int = Field(2000, ge=200)
    max_context_chars: int = Field(12000, ge=500)
    prompt: str = "answer"
    prompt_version: str = "latest"
    prompts_dir: str = "config/prompts"
    timeout_s: float = 60.0


class CitationConfig(BaseModel):
    """Rules that decide whether an answer is allowed to be returned at all."""

    # Pre-generation gate: the best reranked candidate must clear this score, or
    # we abstain without calling the LLM at all.
    min_top_score: float = 0.0
    # At least this many candidates must clear `min_top_score`.
    min_sources: int = Field(1, ge=1)
    # Pre-generation gate: the IDF-weighted fraction of the question's content
    # terms that appear anywhere in the retrieved context. A question whose
    # pivotal term ("Salesforce", "parental leave") occurs nowhere in the corpus
    # scores near zero here, which is a far more reliable out-of-scope signal
    # than any relevance-score threshold. 0.0 disables the gate.
    min_question_coverage: float = Field(0.0, ge=0.0, le=1.0)
    # Post-generation gate: the answer must carry at least one [S#] marker.
    require_citations: bool = True
    # Post-generation gate: fraction of the answer's content words that must also
    # appear in the cited chunks. Catches confident-sounding fabrication.
    min_grounding: float = Field(0.35, ge=0.0, le=1.0)
    # Sentinel the model is instructed to emit when the context is insufficient.
    refusal_marker: str = "INSUFFICIENT_EVIDENCE"
    refusal_message: str = (
        "I can't answer that from the indexed documents. The retrieved passages "
        "don't contain enough evidence to support an answer."
    )


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    # Optional shared secret; when set, /ask and /ingest require `X-API-Key`.
    api_key: str | None = None


class UploadsConfig(BaseModel):
    """The user document library: files uploaded through the UI or API.

    Kept in its own index, apart from ``storage_dir``, so that uploads can never
    leak into the corpus the evaluation gate measures.
    """

    # Uploaded files live in <dir>/files, shared by every profile. Each profile
    # indexes them into <dir>/index/<profile>, since vectors from different
    # embedders are not comparable.
    dir: str = "./storage/uploads"
    max_file_mb: float = Field(20.0, gt=0)


class AppConfig(BaseModel):
    profile: str = "offline"
    storage_dir: str = "./storage"
    corpus_dir: str = "./data/corpus"
    uploads: UploadsConfig = Field(default_factory=UploadsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    citations: CitationConfig = Field(default_factory=CitationConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

    # --- derived paths -------------------------------------------------------

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir)

    @property
    def chunk_store_path(self) -> Path:
        return self.storage_path / "chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.storage_path / "manifest.json"

    @property
    def vector_store_path(self) -> Path:
        if self.vector_store.path:
            return Path(self.vector_store.path)
        return self.storage_path / self.vector_store.provider


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`, returning a new dict."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _env_overrides() -> dict[str, Any]:
    """Map the handful of supported env vars onto config paths."""
    out: dict[str, Any] = {}
    if v := os.getenv("ASKMYDOCS_STORAGE_DIR"):
        out["storage_dir"] = v
    if v := os.getenv("ASKMYDOCS_CORPUS_DIR"):
        out["corpus_dir"] = v
    if v := os.getenv("ASKMYDOCS_UPLOADS_DIR"):
        out.setdefault("uploads", {})["dir"] = v
    if v := os.getenv("ASKMYDOCS_MAX_UPLOAD_MB"):
        out.setdefault("uploads", {})["max_file_mb"] = float(v)
    if v := os.getenv("ASKMYDOCS_GENERATION_MODEL"):
        out.setdefault("generation", {})["model"] = v
    if v := os.getenv("ASKMYDOCS_API_KEY"):
        out.setdefault("api", {})["api_key"] = v
    if v := os.getenv("PORT"):  # container platforms conventionally set PORT
        out.setdefault("api", {})["port"] = int(v)
    return out


def load_config(
    path: str | Path | None = None,
    profile: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Build an :class:`AppConfig`.

    Args:
        path: YAML file to read. Defaults to ``$ASKMYDOCS_CONFIG`` or ``config/app.yaml``.
              A missing file is not an error — built-in defaults are used.
        profile: Profile name to apply. Defaults to ``$ASKMYDOCS_PROFILE`` or the
                 ``profile`` key in the YAML file.
        overrides: Final dict merged on top of everything else.
    """
    config_path = Path(path or os.getenv("ASKMYDOCS_CONFIG") or DEFAULT_CONFIG_PATH)

    raw: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping at the top level")
        raw = loaded

    profiles: dict[str, Any] = raw.pop("profiles", {}) or {}
    selected = profile or os.getenv("ASKMYDOCS_PROFILE") or raw.get("profile") or "offline"

    if selected not in profiles and profiles:
        known = ", ".join(sorted(profiles)) or "(none)"
        raise ValueError(f"Unknown profile {selected!r}. Available profiles: {known}")

    merged = _deep_merge(raw, profiles.get(selected, {}))
    merged["profile"] = selected
    merged = _deep_merge(merged, _env_overrides())
    if overrides:
        merged = _deep_merge(merged, overrides)

    return AppConfig(**merged)
