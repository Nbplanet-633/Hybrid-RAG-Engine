"""The upload library: documents a user adds at runtime, in an index of their own.

    library = DocumentLibrary(load_config())
    summary = library.add("handbook.pdf", pdf_bytes)
    answer = library.ask("What is the notice period?", doc_ids=[summary.doc_id])

Why a separate index
--------------------
The corpus under ``storage_dir`` is what the evaluation gate measures. If uploads
went into the same index, every eval run would also search whatever a user had
uploaded, and retrieval metrics would move for reasons unrelated to the code. So
the library owns its own :class:`RAGPipeline` rooted at ``uploads.dir``:

    <uploads.dir>/files/            the uploaded files, shared by every profile
    <uploads.dir>/index/<profile>/  that profile's chunk store, vectors, manifest

Files are the source of truth. Each profile's index is rebuilt from them on
start-up (incrementally, by content hash), so switching profile — say, from
``offline`` to ``full`` once an API key is available — re-indexes the same
library with the new embedder instead of losing it.

Uploads are validated before they touch the library: allow-listed extension,
size limit, a sanitised filename, and at least some extractable text. The file
is parsed from a staging copy and only moved into place once it passes, so a
rejected upload never leaves a file behind.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path

from askmydocs.config import AppConfig
from askmydocs.ingest.loaders import SUPPORTED_SUFFIXES, load_file
from askmydocs.models import Answer, Document, DocumentSummary
from askmydocs.pipeline import RAGPipeline

logger = logging.getLogger("askmydocs.library")

# Device names Windows refuses as filenames, with or without an extension.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_STEM_CHARS = 100


class UploadError(ValueError):
    """An upload was rejected. The message is safe to show to the user."""


class UnsupportedFileType(UploadError):
    pass


class FileTooLarge(UploadError):
    pass


class EmptyDocument(UploadError):
    pass


class DuplicateDocument(UploadError):
    pass


class DocumentNotFound(KeyError):
    """No document with that id is in the library."""


def safe_filename(name: str) -> str:
    """Reduce a client-supplied filename to a safe basename, or raise.

    Drops any directory part (either separator, so ``..\\..\\x.md`` cannot
    escape the library), replaces characters outside a conservative set, and
    rejects extensions the loaders cannot read.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    # Split on the last dot by hand: os.path.splitext treats "....md" as a
    # dotfile with no extension.
    stem, dot, ext = base.rpartition(".")
    suffix = f".{ext.lower()}" if dot else ""
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise UnsupportedFileType(
            f"Unsupported file type {suffix or '(none)'!r}. Supported: {allowed}"
        )
    stem = _UNSAFE_CHARS.sub("_", stem).strip(" ._")[:_MAX_STEM_CHARS] or "document"
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return f"{stem}{suffix}"


class DocumentLibrary:
    """User-uploaded documents, indexed apart from the evaluated corpus.

    Every public method holds one lock. Ingestion mutates the chunk store and
    swaps the BM25 index, so a question answered mid-upload could otherwise read
    a half-updated index. Serialising is the right trade for a personal library;
    a multi-user deployment would want a real database instead.
    """

    def __init__(self, config: AppConfig) -> None:
        self.root = Path(config.uploads.dir)
        self.files_dir = self.root / "files"
        self.max_bytes = int(config.uploads.max_file_mb * 1024 * 1024)
        self.files_dir.mkdir(parents=True, exist_ok=True)

        index_config = config.model_copy(
            update={
                "storage_dir": str(self.root / "index" / config.profile),
                # An explicit vector-store path would point back at the corpus index.
                "vector_store": config.vector_store.model_copy(update={"path": None}),
            }
        )
        self.pipeline = RAGPipeline.from_config(index_config)
        self._lock = threading.RLock()
        self.sync()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def documents(self) -> list[DocumentSummary]:
        """Every document in the library, ordered by filename."""
        with self._lock:
            summaries = [
                self._summary(doc_id, record)
                for doc_id, record in self.pipeline.documents().items()
            ]
        return sorted(summaries, key=lambda s: s.filename.lower())

    def get(self, doc_id: str) -> DocumentSummary:
        with self._lock:
            record = self.pipeline.documents().get(doc_id)
            if record is None:
                raise DocumentNotFound(doc_id)
            return self._summary(doc_id, record)

    def is_empty(self) -> bool:
        with self._lock:
            return self.pipeline.is_empty()

    def ask(
        self,
        question: str,
        doc_ids: Iterable[str] | None = None,
        top_n: int | None = None,
    ) -> Answer:
        """Answer from the library, optionally only from ``doc_ids``."""
        with self._lock:
            if doc_ids is not None:
                doc_ids = list(doc_ids)
                known = self.pipeline.documents()
                missing = [doc_id for doc_id in doc_ids if doc_id not in known]
                if missing:
                    raise DocumentNotFound(", ".join(missing))
            return self.pipeline.answer(question, top_n=top_n, doc_ids=doc_ids)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(self, filename: str, data: bytes) -> DocumentSummary:
        """Validate, store, and index one uploaded file.

        Re-uploading a filename replaces that document; re-uploading identical
        content is a no-op. Raises an :class:`UploadError` subclass on rejection.
        """
        name = safe_filename(filename)
        if not data:
            raise EmptyDocument(f"{name} is empty.")
        if len(data) > self.max_bytes:
            limit_mb = self.max_bytes / (1024 * 1024)
            raise FileTooLarge(f"{name} is larger than the {limit_mb:g} MB upload limit.")

        target = self.files_dir / name
        with self._lock:
            # Stage under a hidden name with the real suffix (loaders dispatch on
            # it). Hidden files are skipped by sync(), so a crash mid-upload
            # cannot surface a half-written file as a document.
            fd, staged_name = tempfile.mkstemp(
                dir=str(self.files_dir), prefix=".upload-", suffix=target.suffix
            )
            staged = Path(staged_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                document = self._parse(staged, name, source=target.as_posix())
                self._check_duplicate(document)
                os.replace(staged, target)
            finally:
                staged.unlink(missing_ok=True)

            self.pipeline.ingest_documents([document])
            return self.get(document.doc_id)

    def add_directory(self, directory: str | Path) -> tuple[list[DocumentSummary], dict[str, str]]:
        """Add every supported file in ``directory`` (not recursive).

        Returns the added documents and ``{filename: reason}`` for any rejected,
        so one bad file does not stop the rest. Used to load the sample corpus.
        """
        added: list[DocumentSummary] = []
        rejected: dict[str, str] = {}
        for path in sorted(Path(directory).iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                added.append(self.add(path.name, path.read_bytes()))
            except UploadError as exc:
                rejected[path.name] = str(exc)
        return added, rejected

    def delete(self, doc_id: str) -> None:
        """Remove a document and its file. Raises :class:`DocumentNotFound`."""
        with self._lock:
            record = self.pipeline.documents().get(doc_id)
            if record is None:
                raise DocumentNotFound(doc_id)
            self.pipeline.delete_document(doc_id)
            path = Path(record["source"])
            # Only ever delete inside the library, whatever the manifest says.
            if path.resolve().parent == self.files_dir.resolve():
                path.unlink(missing_ok=True)

    def sync(self) -> None:
        """Bring this profile's index in line with the files on disk.

        Indexes new or changed files and drops documents whose file is gone.
        A file that no longer parses is skipped with a warning, not fatal: one
        bad file must not take the whole library down.
        """
        with self._lock:
            documents: list[Document] = []
            for path in sorted(self.files_dir.iterdir()):
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                try:
                    documents.append(self._parse(path, path.name, source=path.as_posix()))
                except UploadError as exc:
                    logger.warning("skipping %s: %s", path, exc)

            on_disk = {document.doc_id for document in documents}
            for doc_id in set(self.pipeline.documents()) - on_disk:
                self.pipeline.delete_document(doc_id)
            self.pipeline.ingest_documents(documents)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(path: Path, name: str, source: str) -> Document:
        try:
            document = load_file(path, source=source)
        except Exception as exc:  # noqa: BLE001 - any parser failure is a bad upload
            raise UploadError(f"Could not read {name}: {exc}") from exc
        if not document.text.strip():
            hint = (
                " It may be a scanned PDF with no text layer."
                if path.suffix.lower() == ".pdf"
                else ""
            )
            raise EmptyDocument(f"No readable text found in {name}.{hint}")
        return document

    def _check_duplicate(self, document: Document) -> None:
        """Reject a doc_id already held by a *different* file.

        Markdown frontmatter can set ``doc_id`` explicitly, so two files can
        claim the same one. Indexing the second would silently evict the first.
        """
        existing = self.pipeline.documents().get(document.doc_id)
        if existing and existing["source"] != document.source:
            raise DuplicateDocument(
                f"{Path(existing['source']).name} already uses the document id "
                f"{document.doc_id!r}. Remove it first, or change the doc_id frontmatter."
            )

    def _summary(self, doc_id: str, record: dict) -> DocumentSummary:
        path = Path(record["source"])
        return DocumentSummary(
            doc_id=doc_id,
            filename=path.name,
            title=record.get("title", ""),
            content_type=record.get("content_type", ""),
            chunks=record.get("chunks", 0),
            size_bytes=path.stat().st_size if path.exists() else 0,
        )
