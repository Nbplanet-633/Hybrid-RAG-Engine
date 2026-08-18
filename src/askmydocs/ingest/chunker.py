"""Structure-aware, token-budgeted chunking with overlap.

Why not a fixed character window
--------------------------------
A naive ``text[i:i+N]`` split severs sentences, tables, and code blocks, which
shows up later as citations that quote half a sentence. This chunker instead:

1. Parses the document into **units** — sentences, list items, table blocks, and
   whole fenced code blocks — each carrying its heading breadcrumb and its
   character offsets in the source file.
2. Greedily packs units into chunks up to ``chunk_target_tokens``, never
   exceeding ``chunk_max_tokens``, and preferring to break at section boundaries
   — but only once a chunk is substantial enough to stand on its own, so a
   document with many short sections still produces chunks in the target band
   rather than one thin chunk per heading.
3. Seeds each new chunk with the trailing ``chunk_overlap_tokens`` worth of units
   from the previous one, so a fact that straddles a boundary survives in both.

Every chunk keeps ``start_char``/``end_char``, so a citation can be traced back
to the exact span of the original file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from askmydocs.models import Chunk, Document
from askmydocs.text import count_tokens, split_sentences

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class _Unit:
    """An atomic piece of text that must not be split across chunks."""

    text: str
    start: int
    end: int
    headings: tuple[str, ...] = field(default_factory=tuple)
    tokens: int = 0

    @property
    def section(self) -> str:
        return " > ".join(self.headings)

    @property
    def section_key(self) -> tuple[str, ...]:
        """The heading path truncated to H1 > H2 — the natural section boundary.

        Comparing only ``headings[0]`` would compare the document's H1, which is
        constant for the whole file, so a section-change break would never fire
        and chunks would silently span unrelated sections. Truncating at depth 2
        breaks between top-level sections while keeping H3/H4 subsections
        together, which is the granularity citations read best at.
        """
        return self.headings[:2]


def _heading_path(stack: dict[int, str]) -> tuple[str, ...]:
    return tuple(stack[level] for level in sorted(stack))


def _hard_split(unit: _Unit, max_tokens: int) -> list[_Unit]:
    """Split an oversized unit (a huge code block or unpunctuated wall of text).

    Splits on line boundaries when possible, falling back to whitespace.
    """
    if unit.tokens <= max_tokens:
        return [unit]

    pieces: list[_Unit] = []
    lines = unit.text.splitlines(keepends=True) or [unit.text]
    buffer: list[str] = []
    buffer_tokens = 0
    cursor = unit.start

    def flush() -> None:
        nonlocal buffer, buffer_tokens, cursor
        if not buffer:
            return
        body = "".join(buffer)
        stripped = body.strip()
        if stripped:
            pieces.append(
                _Unit(
                    text=stripped,
                    start=cursor,
                    end=cursor + len(body),
                    headings=unit.headings,
                    tokens=count_tokens(stripped),
                )
            )
        cursor += len(body)
        buffer = []
        buffer_tokens = 0

    for line in lines:
        line_tokens = count_tokens(line)
        if buffer and buffer_tokens + line_tokens > max_tokens:
            flush()
        buffer.append(line)
        buffer_tokens += line_tokens
    flush()
    return pieces or [unit]


def parse_units(document: Document) -> list[_Unit]:
    """Break a document into ordered, heading-annotated units."""
    text = document.text
    units: list[_Unit] = []
    headings: dict[int, str] = {}

    offset = 0
    lines = text.splitlines(keepends=True)
    index = 0

    def emit_paragraph(block: str, block_start: int) -> None:
        """Split a prose/list block into sentence units with real char offsets."""
        cursor = 0
        for sentence in split_sentences(block):
            found = block.find(sentence, cursor)
            if found == -1:  # normalisation moved things; fall back to the cursor
                found = cursor
            start = block_start + found
            units.append(
                _Unit(
                    text=sentence,
                    start=start,
                    end=start + len(sentence),
                    headings=_heading_path(headings),
                    tokens=count_tokens(sentence),
                )
            )
            cursor = found + len(sentence)

    while index < len(lines):
        line = lines[index]
        line_start = offset
        stripped = line.strip()

        # --- heading -------------------------------------------------------
        heading_match = _HEADING_RE.match(line.rstrip("\n"))
        if heading_match:
            level = len(heading_match.group(1))
            headings = {lvl: name for lvl, name in headings.items() if lvl < level}
            headings[level] = heading_match.group(2)
            # Emit the heading as a unit rather than only recording it in the
            # breadcrumb. A heading is often the *only* place a key term appears
            # -- the Encryption section of the security doc never repeats the word
            # "encryption" in its body -- so dropping heading text silently makes
            # that term unretrievable. Only the chunk that *starts* at a heading
            # gets it in `section`, so headings mid-chunk would be lost entirely.
            heading_text = line.strip()
            units.append(
                _Unit(
                    text=heading_text,
                    start=line_start,
                    end=line_start + len(heading_text),
                    headings=_heading_path(headings),
                    tokens=count_tokens(heading_text),
                )
            )
            offset += len(line)
            index += 1
            continue

        # --- fenced code block (kept whole) ---------------------------------
        if _FENCE_RE.match(line):
            fence = _FENCE_RE.match(line).group(1)
            block_lines = [line]
            offset += len(line)
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                closes = lines[index].strip().startswith(fence)
                offset += len(lines[index])
                index += 1
                if closes:
                    break
            body = "".join(block_lines).strip()
            if body:
                units.append(
                    _Unit(
                        text=body,
                        start=line_start,
                        end=offset,
                        headings=_heading_path(headings),
                        tokens=count_tokens(body),
                    )
                )
            continue

        # --- markdown table (kept whole) ------------------------------------
        if stripped.startswith("|"):
            block_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block_lines.append(lines[index])
                offset += len(lines[index])
                index += 1
            body = "".join(block_lines).strip()
            if body:
                units.append(
                    _Unit(
                        text=body,
                        start=line_start,
                        end=offset,
                        headings=_heading_path(headings),
                        tokens=count_tokens(body),
                    )
                )
            continue

        # --- blank line ------------------------------------------------------
        if not stripped:
            offset += len(line)
            index += 1
            continue

        # --- prose / list paragraph -------------------------------------------
        block_lines = []
        while index < len(lines):
            current = lines[index]
            if not current.strip():
                break
            if _HEADING_RE.match(current.rstrip("\n")) or _FENCE_RE.match(current):
                break
            if current.strip().startswith("|"):
                break
            block_lines.append(current)
            offset += len(current)
            index += 1
        block = "".join(block_lines)
        if block.strip():
            emit_paragraph(block, line_start)

    return units


class Chunker:
    """Packs document units into overlapping, token-budgeted chunks."""

    def __init__(
        self,
        target_tokens: int = 600,
        max_tokens: int = 800,
        overlap_tokens: int = 100,
        min_tokens: int = 40,
        section_break_ratio: float = 1.0,
    ) -> None:
        if overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        if max_tokens < target_tokens:
            raise ValueError("max_tokens must be >= target_tokens")
        if not 0.0 <= section_break_ratio <= 1.0:
            raise ValueError("section_break_ratio must be between 0 and 1")
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_tokens
        # A section boundary is a *preferred* break, not a mandatory one. Breaking
        # at every heading regardless of size turns a document of short sections
        # into many thin chunks, which pushes the average well below the target
        # band and costs retrieval quality. Only honour the boundary once the
        # current chunk has reached this fraction of the target size.
        self.section_break_min_tokens = int(target_tokens * section_break_ratio)

    # -- internals ---------------------------------------------------------

    def _overlap_units(self, units: list[_Unit]) -> list[_Unit]:
        """Trailing units of a finished chunk, up to the overlap budget."""
        if self.overlap_tokens <= 0:
            return []
        carried: list[_Unit] = []
        total = 0
        for unit in reversed(units):
            if total + unit.tokens > self.overlap_tokens:
                break
            carried.insert(0, unit)
            total += unit.tokens
        # Never carry the entire chunk forward — that would loop forever.
        if len(carried) >= len(units):
            carried = carried[1:]
        return carried

    def _build_chunk(self, document: Document, units: list[_Unit], ordinal: int) -> Chunk:
        body = "\n".join(u.text for u in units).strip()
        headings = units[0].headings
        return Chunk(
            chunk_id=f"{document.doc_id}::{ordinal:04d}",
            doc_id=document.doc_id,
            source=document.source,
            title=document.title,
            section=" > ".join(headings),
            text=body,
            ordinal=ordinal,
            token_count=count_tokens(body),
            start_char=units[0].start,
            end_char=units[-1].end,
            metadata={
                "content_type": document.content_type,
                "doc_title": document.title,
                **{
                    k: v
                    for k, v in document.metadata.items()
                    if isinstance(v, (str, int, float, bool))
                },
            },
        )

    # -- public API --------------------------------------------------------

    def chunk_document(self, document: Document) -> list[Chunk]:
        raw_units = parse_units(document)
        units: list[_Unit] = []
        for unit in raw_units:
            units.extend(_hard_split(unit, self.max_tokens))
        units = [u for u in units if u.text.strip()]
        if not units:
            return []

        chunks: list[Chunk] = []
        current: list[_Unit] = []
        current_tokens = 0
        ordinal = 0

        def flush() -> list[_Unit]:
            nonlocal current, current_tokens, ordinal
            if not current:
                return []
            chunks.append(self._build_chunk(document, current, ordinal))
            ordinal += 1
            carried = self._overlap_units(current)
            current = []
            current_tokens = 0
            return carried

        for unit in units:
            would_exceed_max = current and current_tokens + unit.tokens > self.max_tokens
            section_changed = (
                current
                and unit.section_key != current[0].section_key
                and current_tokens >= self.section_break_min_tokens
            )
            reached_target = current and current_tokens >= self.target_tokens

            if would_exceed_max or reached_target or section_changed:
                carried = flush()
                # Overlap is only meaningful when we continue the same section.
                if section_changed:
                    carried = []
                current = list(carried)
                current_tokens = sum(u.tokens for u in current)

            current.append(unit)
            current_tokens += unit.tokens

        flush()

        # A short trailing chunk carries little signal on its own; fold it back
        # into its predecessor when the result still fits the budget.
        if len(chunks) >= 2:
            last = chunks[-1]
            previous = chunks[-2]
            if (
                last.token_count < self.min_tokens
                and previous.token_count + last.token_count <= self.max_tokens
            ):
                merged_text = f"{previous.text}\n{last.text}".strip()
                chunks[-2] = previous.model_copy(
                    update={
                        "text": merged_text,
                        "token_count": count_tokens(merged_text),
                        "end_char": last.end_char,
                    }
                )
                chunks.pop()

        return chunks

    def chunk_documents(self, documents: list[Document]) -> list[Chunk]:
        out: list[Chunk] = []
        for document in documents:
            out.extend(self.chunk_document(document))
        return out
