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


def _minimal_pdf(page_texts: list[str], title: str | None = None) -> bytes:
    """A small, valid PDF with one line of Helvetica text per page.

    Built by hand so the tests need no binary fixture and no PDF-writing library;
    the xref offsets are computed, so pypdf parses it without repair.
    """
    objects: list[bytes] = []
    page_ids = [3 + 2 * i for i in range(len(page_texts))]
    font_id = 3 + 2 * len(page_texts)
    info_id = font_id + 1

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    for pid, text in zip(page_ids, page_texts, strict=True):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {pid + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Title ({title}) >>".encode() if title else b"<< >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        info_id,
        xref,
    )
    return bytes(out)


class TestPdf:
    def test_text_is_extracted_with_page_headings(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.pdf"
        path.write_bytes(_minimal_pdf(["Refunds take 14 days.", "Disputes cost 15 euros."]))

        document = load_file(path)

        assert document.content_type == "pdf"
        assert document.metadata == {"pages": 2}
        # Page markers become headings, so chunks keep their page in the breadcrumb.
        assert "## Page 1" in document.text and "Refunds take 14 days." in document.text
        assert "## Page 2" in document.text and "Disputes cost 15 euros." in document.text
        assert document.title == "policy"  # no /Title: falls back to the filename

    def test_the_document_title_comes_from_pdf_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "scan-0042.pdf"
        path.write_bytes(_minimal_pdf(["Hello."], title="Leave Policy 2026"))
        assert load_file(path).title == "Leave Policy 2026"

    def test_pages_without_text_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.pdf"
        path.write_bytes(_minimal_pdf(["", "Only page two has text."]))
        document = load_file(path)
        assert "## Page 1" not in document.text
        assert "## Page 2" in document.text


HTML_PAGE = """<!doctype html>
<html><head><title>Webhook Guide</title><style>body{color:red}</style></head>
<body>
  <nav>Home | Docs</nav>
  <h1>Webhooks</h1>
  <p>Events are signed with <b>HMAC-SHA256</b>.</p>
  <h2>Retries</h2>
  <ul><li>Retried for 72 hours</li><li>Exponential backoff</li></ul>
  <p>   </p>
  <script>trackEverything()</script>
  <footer>Copyright</footer>
</body></html>"""


class TestHtml:
    def test_structure_survives_as_markdown_and_boilerplate_is_dropped(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "webhooks.html"
        path.write_text(HTML_PAGE, encoding="utf-8")

        document = load_file(path)

        assert document.title == "Webhook Guide"
        assert document.content_type == "html"
        assert "# Webhooks" in document.text
        assert "## Retries" in document.text
        assert "- Retried for 72 hours" in document.text
        assert "Events are signed with HMAC-SHA256 ." in document.text
        for boilerplate in ("Home | Docs", "trackEverything", "Copyright", "color:red"):
            assert boilerplate not in document.text

    def test_title_falls_back_to_the_filename(self, tmp_path: Path) -> None:
        path = tmp_path / "untitled.htm"
        path.write_text("<p>Body only.</p>", encoding="utf-8")
        assert load_file(path).title == "untitled"


class TestUrl:
    def test_a_fetched_page_becomes_a_document(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        calls: list[dict] = []

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            calls.append({"url": url, **kwargs})
            return httpx.Response(200, text=HTML_PAGE, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)
        documents = load_path("https://docs.example.com/webhooks")

        assert len(documents) == 1
        document = documents[0]
        assert document.source == "https://docs.example.com/webhooks"
        assert document.title == "Webhook Guide"
        assert "## Retries" in document.text
        assert calls[0]["follow_redirects"] is True

    def test_an_http_error_is_raised_not_indexed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(404, text="nope", request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)
        with pytest.raises(httpx.HTTPStatusError):
            load_path("https://docs.example.com/missing")
