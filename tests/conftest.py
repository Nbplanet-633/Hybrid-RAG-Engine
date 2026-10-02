"""Shared fixtures.

Every fixture uses the ``offline`` profile: deterministic hashing embeddings, a
numpy vector store, lexical reranking, and the extractive generator. The suite
therefore needs no network, no API key, and no model download, and asserts on
exact values rather than "roughly".
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from askmydocs.config import AppConfig, load_config
from askmydocs.models import Document
from askmydocs.pipeline import RAGPipeline

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_DOC = textwrap.dedent(
    """\
    ---
    title: Widget Handbook
    doc_id: widget-handbook
    owner: Docs Team
    ---

    # Widget Handbook

    Widgets are the core primitive of the platform. Every widget has an id and a
    colour, and both are immutable after creation.

    ## Refund window

    **You have 14 calendar days to request a refund.** After that the request is
    rejected with `refund_window_closed`. Refunds are processed within 5 business
    days.

    ## Rate limits

    The API allows 100 requests per second. Exceeding it returns HTTP 429 with a
    `Retry-After` header.

    ```python
    client.widgets.create(colour="blue")
    ```

    | Plan | Limit |
    |------|-------|
    | Free | 10/s  |
    | Pro  | 100/s |
    """
)


@pytest.fixture(autouse=True)
def _isolate_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the upload library at tmp_path, so no test touches ./storage/uploads."""
    monkeypatch.setenv("ASKMYDOCS_UPLOADS_DIR", str(tmp_path / "uploads"))


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """A two-document corpus on disk."""
    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "handbook.md").write_text(SAMPLE_DOC, encoding="utf-8")
    (directory / "glossary.md").write_text(
        textwrap.dedent(
            """\
            # Glossary

            ## Widget

            A widget is a billable unit. Widgets cannot be renamed.

            ## Sprocket

            A sprocket connects two widgets. Sprockets are free of charge.
            """
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def config(tmp_path: Path, corpus_dir: Path) -> AppConfig:
    """An offline-profile config isolated to this test's tmp_path."""
    return load_config(
        path=REPO_ROOT / "config" / "app.yaml",
        profile="offline",
        overrides={
            "storage_dir": str(tmp_path / "storage"),
            "corpus_dir": str(corpus_dir),
            "generation": {"prompts_dir": str(REPO_ROOT / "config" / "prompts")},
        },
    )


@pytest.fixture
def pipeline(config: AppConfig) -> RAGPipeline:
    """A pipeline with the fixture corpus already indexed."""
    built = RAGPipeline.from_config(config)
    built.ingest([config.corpus_dir], reset=True)
    return built


@pytest.fixture
def document() -> Document:
    return Document(
        doc_id="widget-handbook",
        source="handbook.md",
        title="Widget Handbook",
        text=SAMPLE_DOC.split("---", 2)[-1].strip(),
        content_type="markdown",
    )
