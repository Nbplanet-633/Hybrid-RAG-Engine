"""Chunker tests.

The chunker's contract: respect the token budget, overlap consecutive chunks,
never split a code block or table, keep heading text retrievable, and record
character offsets that point back into the source document.
"""

from __future__ import annotations

import pytest

from askmydocs.ingest.chunker import Chunker, parse_units
from askmydocs.models import Document
from askmydocs.text import count_tokens


@pytest.fixture
def chunker() -> Chunker:
    return Chunker(target_tokens=120, max_tokens=180, overlap_tokens=30, min_tokens=10)


def _doc(text: str) -> Document:
    return Document(doc_id="d1", source="d1.md", title="Doc One", text=text)


class TestUnitParsing:
    def test_headings_are_emitted_as_units(self, document: Document) -> None:
        # Regression: heading text used to live only in the breadcrumb of the
        # chunk that started at it, so a heading mid-chunk vanished entirely --
        # making terms that appear *only* in a heading unretrievable.
        units = parse_units(document)
        assert any(unit.text.startswith("## Refund window") for unit in units)

    def test_code_block_is_one_unit(self, document: Document) -> None:
        units = [u for u in parse_units(document) if "widgets.create" in u.text]
        assert len(units) == 1
        assert units[0].text.startswith("```")
        assert units[0].text.rstrip().endswith("```")

    def test_table_rows_are_one_unit(self, document: Document) -> None:
        units = [u for u in parse_units(document) if u.text.startswith("|")]
        assert len(units) == 1  # the whole table, not one unit per row
        assert "Free" in units[0].text and "Pro" in units[0].text

    def test_units_carry_heading_breadcrumbs(self, document: Document) -> None:
        units = parse_units(document)
        refund = next(u for u in units if "14 calendar days" in u.text)
        assert refund.headings == ("Widget Handbook", "Refund window")

    def test_offsets_point_at_the_source_text(self, document: Document) -> None:
        for unit in parse_units(document):
            assert 0 <= unit.start <= unit.end <= len(document.text)


class TestChunking:
    def test_respects_the_max_token_budget(self, chunker: Chunker, document: Document) -> None:
        for chunk in chunker.chunk_document(document):
            assert chunk.token_count <= chunker.max_tokens

    def test_reported_token_count_matches_the_text(
        self, chunker: Chunker, document: Document
    ) -> None:
        for chunk in chunker.chunk_document(document):
            assert chunk.token_count == count_tokens(chunk.text)

    def test_consecutive_chunks_overlap(self) -> None:
        # A fact straddling a boundary must survive in both chunks.
        text = "\n\n".join(
            f"Sentence number {i} carries fact {i} about widgets." for i in range(60)
        )
        chunker = Chunker(target_tokens=100, max_tokens=140, overlap_tokens=30, min_tokens=10)
        chunks = chunker.chunk_document(_doc(text))
        assert len(chunks) >= 3
        overlaps = 0
        for previous, following in zip(chunks, chunks[1:], strict=False):
            tail = set(previous.text.split("\n")[-3:])
            if tail & set(following.text.split("\n")):
                overlaps += 1
        assert overlaps >= 1

    def test_overlap_zero_produces_disjoint_chunks(self) -> None:
        text = "\n\n".join(f"Fact {i} about widgets and sprockets." for i in range(40))
        chunks = Chunker(target_tokens=60, max_tokens=90, overlap_tokens=0).chunk_document(
            _doc(text)
        )
        assert len(chunks) >= 2
        for previous, following in zip(chunks, chunks[1:], strict=False):
            assert not (set(previous.text.split("\n")) & set(following.text.split("\n")))

    def test_ids_are_stable_and_ordered(self, chunker: Chunker, document: Document) -> None:
        first = chunker.chunk_document(document)
        second = chunker.chunk_document(document)
        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
        assert [c.ordinal for c in first] == list(range(len(first)))
        assert all(c.chunk_id.startswith("widget-handbook::") for c in first)

    def test_oversized_single_unit_is_hard_split(self) -> None:
        # A giant code block cannot be sentence-split, but must still fit.
        giant = "```\n" + "\n".join(f"line_{i} = compute({i})" for i in range(400)) + "\n```"
        chunker = Chunker(target_tokens=100, max_tokens=150, overlap_tokens=20)
        chunks = chunker.chunk_document(_doc(giant))
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.token_count <= chunker.max_tokens

    def test_section_metadata_is_populated(self, chunker: Chunker, document: Document) -> None:
        chunks = chunker.chunk_document(document)
        # Every chunk carries the breadcrumb in effect where it starts. A chunk may
        # span several sections when packing to the token target, so the assertion
        # is that breadcrumbs exist and name the document, not that every heading
        # appears as some chunk's breadcrumb.
        assert all(chunk.section for chunk in chunks)
        assert all(chunk.section.startswith("Widget Handbook") for chunk in chunks)

    def test_section_boundary_is_honoured_once_a_chunk_is_substantial(self) -> None:
        # With the ratio at 0, every heading forces a break, so each section
        # becomes its own chunk and breadcrumbs are section-precise.
        text = (
            "# Doc\n\nIntro sentence one. Intro sentence two.\n\n"
            "## Alpha\n\nAlpha body sentence. More alpha detail here.\n\n"
            "## Beta\n\nBeta body sentence. More beta detail here.\n"
        )
        eager = Chunker(
            target_tokens=400,
            max_tokens=500,
            overlap_tokens=0,
            min_tokens=1,
            section_break_ratio=0.0,
        )
        sections = {c.section for c in eager.chunk_document(_doc(text))}
        assert "Doc > Alpha" in sections
        assert "Doc > Beta" in sections

        # At ratio 1.0 the same short sections are packed together instead, which
        # is what keeps realised chunk sizes inside the configured target band.
        packed = Chunker(
            target_tokens=400,
            max_tokens=500,
            overlap_tokens=0,
            min_tokens=1,
            section_break_ratio=1.0,
        )
        assert len(packed.chunk_document(_doc(text))) == 1

    def test_invalid_section_break_ratio_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="section_break_ratio"):
            Chunker(section_break_ratio=1.5)

    def test_empty_document_yields_no_chunks(self, chunker: Chunker) -> None:
        assert chunker.chunk_document(_doc("")) == []
        assert chunker.chunk_document(_doc("   \n\n  ")) == []

    def test_embedding_text_includes_heading_context(
        self, chunker: Chunker, document: Document
    ) -> None:
        chunk = chunker.chunk_document(document)[0]
        assert chunk.section.split(" > ")[0] in chunk.embedding_text

    def test_char_offsets_are_ordered(self, chunker: Chunker, document: Document) -> None:
        for chunk in chunker.chunk_document(document):
            assert chunk.start_char < chunk.end_char <= len(document.text)


class TestConfigValidation:
    def test_overlap_must_be_smaller_than_target(self) -> None:
        with pytest.raises(ValueError, match="overlap_tokens"):
            Chunker(target_tokens=100, max_tokens=200, overlap_tokens=100)

    def test_max_must_be_at_least_target(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            Chunker(target_tokens=200, max_tokens=100, overlap_tokens=10)
