"""Upload library tests: validation, lifecycle, scoping, and isolation from the corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from askmydocs.config import AppConfig
from askmydocs.library import (
    DocumentLibrary,
    DocumentNotFound,
    DuplicateDocument,
    EmptyDocument,
    FileTooLarge,
    UnsupportedFileType,
    UploadError,
    safe_filename,
)
from askmydocs.pipeline import RAGPipeline
from conftest import SAMPLE_DOC

LEAVE_POLICY = b"""# Leave Policy

## Annual leave

Employees receive 24 days of annual leave per calendar year. Unused leave expires
on 31 March and cannot be carried over.

## Notice period

The notice period for resignation is 60 days for permanent staff.
"""


@pytest.fixture
def library(config: AppConfig) -> DocumentLibrary:
    return DocumentLibrary(config)


def _files(library: DocumentLibrary) -> list[str]:
    return sorted(p.name for p in library.files_dir.iterdir())


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("report.pdf", "report.pdf"),
            ("Report.PDF", "Report.pdf"),
            ("../../etc/passwd.md", "passwd.md"),
            ("..\\..\\Windows\\evil.txt", "evil.txt"),
            ("my notes (final).md", "my notes _final.md"),
            ("CON.txt", "_CON.txt"),
            ("....md", "document.md"),
        ],
    )
    def test_reduces_to_a_safe_basename(self, raw: str, expected: str) -> None:
        assert safe_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["photo.png", "archive.zip", "noextension", ""])
    def test_rejects_types_the_loaders_cannot_read(self, raw: str) -> None:
        with pytest.raises(UnsupportedFileType, match="Supported"):
            safe_filename(raw)

    def test_truncates_very_long_names(self) -> None:
        assert len(safe_filename("a" * 500 + ".md")) == 100 + len(".md")


class TestAdd:
    def test_upload_is_stored_indexed_and_listed(self, library: DocumentLibrary) -> None:
        summary = library.add("leave-policy.md", LEAVE_POLICY)

        assert summary.filename == "leave-policy.md"
        assert summary.title == "Leave Policy"
        assert summary.chunks >= 1
        assert summary.size_bytes == len(LEAVE_POLICY)
        assert [d.doc_id for d in library.documents()] == [summary.doc_id]
        assert _files(library) == ["leave-policy.md"]

    def test_uploaded_content_is_answerable_with_a_citation(self, library: DocumentLibrary) -> None:
        library.add("leave-policy.md", LEAVE_POLICY)
        answer = library.ask("How many days of annual leave do employees get?")

        assert not answer.abstained
        assert "24 days" in answer.text
        assert answer.citations[0].source.endswith("leave-policy.md")

    def test_reupload_with_same_content_is_a_no_op(self, library: DocumentLibrary) -> None:
        first = library.add("leave-policy.md", LEAVE_POLICY)
        second = library.add("leave-policy.md", LEAVE_POLICY)
        assert first == second
        assert len(library.documents()) == 1

    def test_reupload_with_new_content_replaces_the_old_version(
        self, library: DocumentLibrary
    ) -> None:
        library.add("leave-policy.md", LEAVE_POLICY)
        library.add("leave-policy.md", LEAVE_POLICY.replace(b"24 days", b"30 days"))

        assert len(library.documents()) == 1
        texts = " ".join(chunk.text for chunk in library.pipeline.chunk_store)
        assert "30 days" in texts
        # The previous revision's chunks must not linger in either index.
        assert "24 days" not in texts

    @pytest.mark.parametrize(
        ("name", "data", "error"),
        [
            ("empty.md", b"", EmptyDocument),
            ("blank.md", b"   \n\n  ", EmptyDocument),
            ("image.png", b"\x89PNG\r\n", UnsupportedFileType),
            ("broken.pdf", b"this is not a pdf", UploadError),
        ],
    )
    def test_rejected_uploads_leave_nothing_behind(
        self, library: DocumentLibrary, name: str, data: bytes, error: type[Exception]
    ) -> None:
        with pytest.raises(error):
            library.add(name, data)
        assert library.documents() == []
        assert _files(library) == []  # not even the hidden staging file

    def test_rejects_files_over_the_size_limit(self, config: AppConfig) -> None:
        small = config.model_copy(
            update={"uploads": config.uploads.model_copy(update={"max_file_mb": 0.001})}
        )
        library = DocumentLibrary(small)
        with pytest.raises(FileTooLarge, match="upload limit"):
            library.add("big.md", b"# Big\n\n" + b"word " * 1000)
        assert _files(library) == []

    def test_rejects_a_second_file_claiming_the_same_doc_id(self, library: DocumentLibrary) -> None:
        # SAMPLE_DOC sets `doc_id: widget-handbook` in its frontmatter.
        library.add("handbook.md", SAMPLE_DOC.encode())
        with pytest.raises(DuplicateDocument, match="handbook.md"):
            library.add("copy-of-handbook.md", SAMPLE_DOC.encode())
        assert _files(library) == ["handbook.md"]


class TestDeleteAndScope:
    def test_delete_removes_the_document_its_chunks_and_its_file(
        self, library: DocumentLibrary
    ) -> None:
        summary = library.add("leave-policy.md", LEAVE_POLICY)
        library.delete(summary.doc_id)

        assert library.documents() == []
        assert library.is_empty()
        assert _files(library) == []

    def test_delete_unknown_document_raises(self, library: DocumentLibrary) -> None:
        with pytest.raises(DocumentNotFound):
            library.delete("nope")

    def test_doc_ids_restrict_the_answer_to_that_document(self, library: DocumentLibrary) -> None:
        policy = library.add("leave-policy.md", LEAVE_POLICY)
        handbook = library.add("handbook.md", SAMPLE_DOC.encode())

        scoped = library.ask("What is the refund window?", doc_ids=[handbook.doc_id])
        assert not scoped.abstained
        assert all(c.source.endswith("handbook.md") for c in scoped.citations)
        assert all(r.chunk.doc_id == handbook.doc_id for r in scoped.retrieved)

        # Asked of the other document only, the same question has no support.
        other = library.ask("What is the refund window?", doc_ids=[policy.doc_id])
        assert all(r.chunk.doc_id == policy.doc_id for r in other.retrieved)

    def test_unknown_doc_id_in_a_question_raises(self, library: DocumentLibrary) -> None:
        library.add("leave-policy.md", LEAVE_POLICY)
        with pytest.raises(DocumentNotFound):
            library.ask("What is the notice period?", doc_ids=["nope"])


class TestIsolationAndSync:
    def test_uploads_never_reach_the_corpus_index(
        self, pipeline: RAGPipeline, config: AppConfig
    ) -> None:
        corpus_chunks = len(pipeline.chunk_store)
        library = DocumentLibrary(config)
        library.add("leave-policy.md", LEAVE_POLICY)

        reopened = RAGPipeline.from_config(config)
        assert len(reopened.chunk_store) == corpus_chunks
        assert not any("leave-policy" in s for s in reopened.chunk_store.sources())
        assert Path(config.uploads.dir).resolve() != Path(config.storage_dir).resolve()

    def test_a_restart_rebuilds_the_index_from_the_files(self, config: AppConfig) -> None:
        DocumentLibrary(config).add("leave-policy.md", LEAVE_POLICY)
        # Lose the index entirely; the files are the source of truth.
        index_dir = Path(config.uploads.dir) / "index"
        for path in sorted(index_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()

        restarted = DocumentLibrary(config)
        assert [d.filename for d in restarted.documents()] == ["leave-policy.md"]
        assert not restarted.ask("What is the notice period?").abstained

    def test_a_file_removed_from_disk_is_dropped_on_restart(self, config: AppConfig) -> None:
        library = DocumentLibrary(config)
        library.add("leave-policy.md", LEAVE_POLICY)
        library.add("handbook.md", SAMPLE_DOC.encode())
        (library.files_dir / "leave-policy.md").unlink()

        assert [d.filename for d in DocumentLibrary(config).documents()] == ["handbook.md"]

    def test_an_unreadable_file_on_disk_is_skipped_not_fatal(self, config: AppConfig) -> None:
        library = DocumentLibrary(config)
        library.add("leave-policy.md", LEAVE_POLICY)
        (library.files_dir / "corrupt.pdf").write_bytes(b"not a pdf")

        assert [d.filename for d in DocumentLibrary(config).documents()] == ["leave-policy.md"]

    def test_each_profile_gets_its_own_index(self, library: DocumentLibrary) -> None:
        assert library.pipeline.config.storage_path == library.root / "index" / "offline"
