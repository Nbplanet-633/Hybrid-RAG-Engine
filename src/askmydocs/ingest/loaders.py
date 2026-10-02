"""Loaders for the supported source types: markdown, plain text, PDF, and HTML.

PDF and HTML support is optional (``pip install 'ask-my-docs[loaders]'``). A
missing dependency raises a clear error naming the extra to install, rather than
an ImportError from three frames down.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from askmydocs.models import Document

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
TEXT_SUFFIXES = {".txt", ".text", ".rst"}
PDF_SUFFIXES = {".pdf"}
HTML_SUFFIXES = {".html", ".htm"}

SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | TEXT_SUFFIXES | PDF_SUFFIXES | HTML_SUFFIXES


def _doc_id(source: str) -> str:
    """Stable id derived from the source path, readable enough to debug with."""
    stem = re.sub(r"[^a-z0-9]+", "-", Path(source).stem.lower()).strip("-")
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
    return f"{stem or 'doc'}-{digest}"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from the body. Returns ``({}, text)`` when absent."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[match.end() :]


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped:
            break
    return fallback


def _load_markdown(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(raw)
    title = str(meta.get("title") or _title_from_markdown(body, path.stem))
    source = path.as_posix()
    return Document(
        doc_id=str(meta.get("doc_id") or _doc_id(source)),
        source=source,
        title=title,
        text=body.strip(),
        content_type="markdown",
        metadata={k: v for k, v in meta.items() if k not in {"title", "doc_id"}},
    )


def _load_text(path: Path) -> Document:
    source = path.as_posix()
    return Document(
        doc_id=_doc_id(source),
        source=source,
        title=path.stem,
        text=path.read_text(encoding="utf-8", errors="replace").strip(),
        content_type="text",
    )


def _load_pdf(path: Path) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "PDF support requires pypdf. Install it with: pip install 'ask-my-docs[loaders]'"
        ) from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        content = (page.extract_text() or "").strip()
        if content:
            # A page marker doubles as a heading, so the chunker keeps page
            # provenance in each chunk's section breadcrumb.
            pages.append(f"## Page {number}\n\n{content}")

    info = reader.metadata or {}
    source = path.as_posix()
    return Document(
        doc_id=_doc_id(source),
        source=source,
        title=str(info.get("/Title") or path.stem),
        text="\n\n".join(pages),
        content_type="pdf",
        metadata={"pages": len(reader.pages)},
    )


def _html_to_text(html: str) -> tuple[str, str]:
    """Return ``(title, text)`` extracted from an HTML string."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "HTML support requires beautifulsoup4. "
            "Install it with: pip install 'ask-my-docs[loaders]'"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "aside"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    # Rebuild a lightweight markdown skeleton so headings survive into chunk
    # metadata instead of being flattened into one wall of text.
    lines: list[str] = []
    body = soup.body or soup
    for element in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td"]):
        content = element.get_text(" ", strip=True)
        if not content:
            continue
        if element.name.startswith("h"):
            lines.append(f"{'#' * int(element.name[1])} {content}")
        elif element.name == "li":
            lines.append(f"- {content}")
        else:
            lines.append(content)

    return title, "\n\n".join(lines)


def _load_html(path: Path) -> Document:
    title, text = _html_to_text(path.read_text(encoding="utf-8", errors="replace"))
    source = path.as_posix()
    return Document(
        doc_id=_doc_id(source),
        source=source,
        title=title or path.stem,
        text=text,
        content_type="html",
    )


_LOADERS = [
    (MARKDOWN_SUFFIXES, _load_markdown),
    (TEXT_SUFFIXES, _load_text),
    (PDF_SUFFIXES, _load_pdf),
    (HTML_SUFFIXES, _load_html),
]


def load_file(path: Path) -> Document:
    """Load a single file, dispatching on its suffix."""
    suffix = path.suffix.lower()
    for suffixes, loader in _LOADERS:
        if suffix in suffixes:
            return loader(path)
    raise ValueError(
        f"Unsupported file type {suffix!r} for {path}. "
        f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def load_url(url: str, timeout: float = 20.0) -> Document:
    """Fetch a web page and return it as a Document."""
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    title, text = _html_to_text(response.text)
    return Document(
        doc_id=_doc_id(url),
        source=url,
        title=title or url,
        text=text,
        content_type="html",
        metadata={"fetched_from": url},
    )


def _matches(path: Path, root: Path, patterns: Iterable[str]) -> bool:
    relative = path.relative_to(root)
    return any(relative.match(pattern) or path.match(pattern) for pattern in patterns)


def load_path(
    target: str | Path,
    include_globs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
) -> list[Document]:
    """Load a file, a directory tree, or a URL into a list of Documents."""
    if isinstance(target, str) and target.startswith(("http://", "https://")):
        return [load_url(target)]

    path = Path(target)
    if not path.exists():
        raise FileNotFoundError(f"No such file or directory: {path}")

    if path.is_file():
        return [load_file(path)]

    includes = list(include_globs or ["**/*"])
    excludes = list(exclude_globs or [])

    documents: list[Document] = []
    for candidate in sorted(path.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if not _matches(candidate, path, includes):
            continue
        if excludes and _matches(candidate, path, excludes):
            continue
        # A hidden directory anywhere in the path disqualifies the file.
        if any(part.startswith(".") for part in candidate.relative_to(path).parts):
            continue
        documents.append(load_file(candidate))

    return documents


def load_paths(
    targets: Iterable[str | Path],
    include_globs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
) -> list[Document]:
    """Load several targets, de-duplicating by ``doc_id``."""
    seen: dict[str, Document] = {}
    for target in targets:
        for document in load_path(target, include_globs, exclude_globs):
            seen.setdefault(document.doc_id, document)
    return list(seen.values())
