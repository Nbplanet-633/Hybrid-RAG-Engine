"""Durable store for chunk text and metadata.

The chunk store is the single source of truth for chunk *content*. The vector
store holds only embeddings keyed by ``chunk_id``, and the BM25 index is rebuilt
from here at load time. Keeping content in exactly one place means the two
indexes can never disagree about what a chunk says.

Backed by JSONL: append-friendly, greppable, diffable, and trivial to inspect
when a retrieval result looks wrong.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from askmydocs.models import Chunk


class ChunkStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._chunks: dict[str, Chunk] = {}
        self._loaded = False

    # -- persistence -------------------------------------------------------

    def load(self) -> ChunkStore:
        """Read the JSONL file into memory. Safe to call repeatedly."""
        self._chunks = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = Chunk(**json.loads(line))
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ValueError(
                            f"Corrupt chunk store at {self.path}:{line_number}: {exc}"
                        ) from exc
                    self._chunks[chunk.chunk_id] = chunk
        self._loaded = True
        return self

    def save(self) -> None:
        """Atomically rewrite the JSONL file.

        Write-to-temp-then-rename means an interrupted ingest leaves the previous
        index intact rather than a half-written file.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for chunk in self._chunks.values():
                    handle.write(chunk.model_dump_json() + "\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- mutation ----------------------------------------------------------

    def add(self, chunks: Iterable[Chunk]) -> int:
        """Insert or replace chunks. Returns the number written."""
        self._ensure_loaded()
        count = 0
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk
            count += 1
        return count

    def delete_document(self, doc_id: str) -> list[str]:
        """Remove every chunk belonging to ``doc_id``. Returns the removed ids."""
        self._ensure_loaded()
        removed = [cid for cid, chunk in self._chunks.items() if chunk.doc_id == doc_id]
        for chunk_id in removed:
            del self._chunks[chunk_id]
        return removed

    def reset(self) -> None:
        self._chunks = {}
        self._loaded = True
        self.path.unlink(missing_ok=True)

    # -- reads -------------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        self._ensure_loaded()
        return self._chunks.get(chunk_id)

    def get_many(self, chunk_ids: Iterable[str]) -> list[Chunk]:
        self._ensure_loaded()
        return [self._chunks[cid] for cid in chunk_ids if cid in self._chunks]

    def all(self) -> list[Chunk]:
        self._ensure_loaded()
        return list(self._chunks.values())

    def doc_ids(self) -> set[str]:
        self._ensure_loaded()
        return {chunk.doc_id for chunk in self._chunks.values()}

    def sources(self) -> set[str]:
        self._ensure_loaded()
        return {chunk.source for chunk in self._chunks.values()}

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._chunks)

    def __iter__(self) -> Iterator[Chunk]:
        self._ensure_loaded()
        return iter(self._chunks.values())

    def __contains__(self, chunk_id: object) -> bool:
        self._ensure_loaded()
        return chunk_id in self._chunks
