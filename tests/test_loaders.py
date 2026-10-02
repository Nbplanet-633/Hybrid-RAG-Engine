"""Loader tests: frontmatter, titles, directory walking, and error messages."""

from __future__ import annotations

from pathlib import Path

import pytest

from askmydocs.ingest.loaders import load_file, load_path, load_paths


class TestMarkdown:
    def test_frontmatter_is_parsed_and_stripped(self, corpus_dir: Path) -> None:
        document = load_file(corpus_dir / "handbook.md")
        assert document.title == "Widget Handbook"
        assert document.doc_id == "widget-handbook"
        assert document.metadata["owner"] == "Docs Team"
        assert not document.text.startswith("---")

    def test_title_falls_back_to_the_h1(self, tmp_path: Path) -> None:
        path = tmp_path / "no-frontmatter.md"
        path.write_text("# Actual Title\n\nBody text.\n", encoding="utf-8")
        assert load_file(path).title == "Actual Title"

    def test_doc_id_is_derived_and_stable(self, tmp_path: Path) -> None:
        path = tmp_path / "My Doc.md"
        path.write_text("# Hi\n", encoding="utf-8")
        assert load_file(path).doc_id == load_file(path).doc_id
        assert load_file(path).doc_id.startswith("my-doc-")

    def test_malformed_frontmatter_is_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.md"
        path.write_text("---\n: : not valid: yaml:\n---\n\n# Title\n", encoding="utf-8")
        # A broken header must not lose the document; the body still loads.
        assert "Title" in load_file(path).text or load_file(path).title


class TestContentHash:
    def test_hash_changes_only_when_content_changes(self, corpus_dir: Path) -> None:
        first = load_file(corpus_dir / "handbook.md")
        again = load_file(corpus_dir / "handbook.md")
        assert first.content_hash == again.content_hash

        modified = corpus_dir / "handbook.md"
        modified.write_text(modified.read_text(encoding="utf-8") + "\nExtra.\n", encoding="utf-8")
        assert load_file(modified).content_hash != first.content_hash


class TestDirectoryLoading:
    def test_loads_every_supported_file(self, corpus_dir: Path) -> None:
        assert {Path(d.source).name for d in load_path(corpus_dir)} == {
            "handbook.md",
            "glossary.md",
        }

    def test_sources_use_forward_slashes_on_every_os(self, corpus_dir: Path) -> None:
        # The golden set records sources as POSIX paths; a backslash source on
        # Windows silently zeroes every source-matching eval metric.
        nested = corpus_dir / "sub"
        nested.mkdir()
        (nested / "deep.md").write_text("# Deep\n", encoding="utf-8")
        assert all("\\" not in d.source for d in load_path(corpus_dir))

    def test_include_globs_filter(self, corpus_dir: Path) -> None:
        (corpus_dir / "notes.txt").write_text("plain text notes", encoding="utf-8")
        assert len(load_path(corpus_dir, include_globs=["**/*.md"])) == 2
        assert len(load_path(corpus_dir, include_globs=["**/*"])) == 3

    def test_hidden_directories_are_skipped(self, corpus_dir: Path) -> None:
        hidden = corpus_dir / ".cache"
        hidden.mkdir()
        (hidden / "junk.md").write_text("# Junk\n", encoding="utf-8")
        assert all(".cache" not in d.source for d in load_path(corpus_dir))

    def test_unsupported_extensions_are_ignored_in_a_directory(self, corpus_dir: Path) -> None:
        (corpus_dir / "image.png").write_bytes(b"\x89PNG\r\n")
        assert len(load_path(corpus_dir)) == 2

    def test_load_paths_deduplicates(self, corpus_dir: Path) -> None:
        assert len(load_paths([corpus_dir, corpus_dir])) == 2


class TestErrors:
    def test_missing_path_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_path(tmp_path / "nope")

    def test_unsupported_single_file_names_the_supported_types(self, tmp_path: Path) -> None:
        path = tmp_path / "data.xlsx"
        path.write_bytes(b"not a spreadsheet")
        with pytest.raises(ValueError, match="Unsupported file type"):
            load_file(path)
