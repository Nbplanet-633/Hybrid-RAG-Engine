"""Vector store backends.

* ``ChromaVectorStore`` — persistent ChromaDB collection. The default.
* ``NumpyVectorStore``  — a float32 matrix plus an id list, persisted as ``.npy``
  + JSON. Zero extra dependencies, exact (not approximate) search, and fast
  enough for tens of thousands of chunks. Used by CI and by anyone who does not
  want a database in the loop.

Both store **only** vectors keyed by ``chunk_id``. Chunk text lives in the
:class:`~askmydocs.index.chunk_store.ChunkStore`, so the two can never disagree.

Scores are cosine similarity in ``[-1, 1]``, higher is better, for both backends —
Chroma natively returns squared-L2 or cosine *distance*, so the adapter converts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

# A (chunk_id, score) pair.
ScoredId = tuple[str, float]


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, ids: list[str], vectors: np.ndarray) -> None: ...

    def query(self, vector: np.ndarray, k: int) -> list[ScoredId]: ...

    def delete(self, ids: Iterable[str]) -> None: ...

    def count(self) -> int: ...

    def reset(self) -> None: ...

    def persist(self) -> None: ...


class NumpyVectorStore:
    """Exact cosine search over an in-memory matrix, persisted to disk."""

    provider = "numpy"

    def __init__(self, path: str | Path, collection: str = "askmydocs") -> None:
        self.dir = Path(path)
        self.collection = collection
        self._ids: list[str] = []
        self._index: dict[str, int] = {}
        self._matrix: np.ndarray | None = None
        self._load()

    # -- paths -------------------------------------------------------------

    @property
    def _vectors_path(self) -> Path:
        return self.dir / f"{self.collection}.vectors.npy"

    @property
    def _ids_path(self) -> Path:
        return self.dir / f"{self.collection}.ids.json"

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if self._vectors_path.exists() and self._ids_path.exists():
            self._matrix = np.load(self._vectors_path).astype(np.float32, copy=False)
            self._ids = json.loads(self._ids_path.read_text(encoding="utf-8"))
            if len(self._ids) != (0 if self._matrix is None else self._matrix.shape[0]):
                raise ValueError(
                    f"Vector store at {self.dir} is inconsistent: "
                    f"{len(self._ids)} ids vs {self._matrix.shape[0]} vectors. "
                    "Re-run ingestion with --reset."
                )
            self._index = {cid: i for i, cid in enumerate(self._ids)}

    def persist(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        matrix = self._matrix if self._matrix is not None else np.zeros((0, 0), dtype=np.float32)
        np.save(self._vectors_path, matrix)
        self._ids_path.write_text(json.dumps(self._ids), encoding="utf-8")

    # -- mutation ----------------------------------------------------------

    def upsert(self, ids: list[str], vectors: np.ndarray) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must be the same length")
        if not ids:
            return

        vectors = np.asarray(vectors, dtype=np.float32)
        if (
            self._matrix is not None
            and self._matrix.size
            and self._matrix.shape[1] != vectors.shape[1]
        ):
            raise ValueError(
                f"Embedding dimension changed ({self._matrix.shape[1]} -> {vectors.shape[1]}). "
                "Re-run ingestion with --reset after changing the embedding model."
            )

        new_ids: list[str] = []
        new_rows: list[np.ndarray] = []
        for chunk_id, row in zip(ids, vectors, strict=True):
            existing = self._index.get(chunk_id)
            if existing is not None:
                self._matrix[existing] = row
            else:
                new_ids.append(chunk_id)
                new_rows.append(row)

        if new_rows:
            block = np.vstack(new_rows)
            if self._matrix is None or not self._matrix.size:
                self._matrix = block
            else:
                self._matrix = np.vstack([self._matrix, block])
            for chunk_id in new_ids:
                self._index[chunk_id] = len(self._ids)
                self._ids.append(chunk_id)

    def delete(self, ids: Iterable[str]) -> None:
        doomed = {cid for cid in ids if cid in self._index}
        if not doomed or self._matrix is None:
            return
        keep = [i for i, cid in enumerate(self._ids) if cid not in doomed]
        self._matrix = (
            self._matrix[keep] if keep else np.zeros((0, self._matrix.shape[1]), np.float32)
        )
        self._ids = [self._ids[i] for i in keep]
        self._index = {cid: i for i, cid in enumerate(self._ids)}

    def reset(self) -> None:
        self._ids = []
        self._index = {}
        self._matrix = None
        self._vectors_path.unlink(missing_ok=True)
        self._ids_path.unlink(missing_ok=True)

    # -- search ------------------------------------------------------------

    def query(self, vector: np.ndarray, k: int) -> list[ScoredId]:
        if self._matrix is None or not self._matrix.size:
            return []
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm == 0:
            return []
        query = query / norm

        # Rows are stored L2-normalised by the embedder, but normalise defensively
        # so a store built with normalize=false still yields true cosine scores.
        row_norms = np.linalg.norm(self._matrix, axis=1)
        row_norms[row_norms == 0] = 1.0
        scores = (self._matrix @ query) / row_norms

        k = min(k, scores.shape[0])
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(self._ids[i], float(scores[i])) for i in top]

    def count(self) -> int:
        return len(self._ids)


class ChromaVectorStore:
    """Persistent ChromaDB backend."""

    provider = "chroma"

    def __init__(self, path: str | Path, collection: str = "askmydocs") -> None:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The chroma vector store requires an extra install:\n"
                "    pip install 'ask-my-docs[chroma]'\n"
                "Or set vector_store.provider: numpy in config/app.yaml."
            ) from exc

        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection
        self._client = chromadb.PersistentClient(path=str(self.dir))
        self._collection = self._client.get_or_create_collection(
            name=collection,
            # Cosine space, so distances map cleanly onto similarity scores.
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, ids: list[str], vectors: np.ndarray) -> None:
        if not ids:
            return
        self._collection.upsert(
            ids=list(ids),
            embeddings=[row.tolist() for row in np.asarray(vectors, dtype=np.float32)],
        )

    def query(self, vector: np.ndarray, k: int) -> list[ScoredId]:
        total = self.count()
        if total == 0:
            return []
        result = self._collection.query(
            query_embeddings=[np.asarray(vector, dtype=np.float32).reshape(-1).tolist()],
            n_results=min(k, total),
            include=["distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        # Chroma's cosine space returns distance = 1 - cosine_similarity.
        return [(cid, 1.0 - float(distance)) for cid, distance in zip(ids, distances, strict=True)]

    def delete(self, ids: Iterable[str]) -> None:
        doomed = list(ids)
        if doomed:
            self._collection.delete(ids=doomed)

    def count(self) -> int:
        return int(self._collection.count())

    def reset(self) -> None:
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def persist(self) -> None:
        # PersistentClient writes through on every call; nothing to flush.
        return None


def get_vector_store(config, path: str | Path) -> VectorStore:
    """Construct the vector store described by a :class:`VectorStoreConfig`."""
    if config.provider == "numpy":
        return NumpyVectorStore(path=path, collection=config.collection)
    if config.provider == "chroma":
        return ChromaVectorStore(path=path, collection=config.collection)
    raise ValueError(f"Unknown vector store provider: {config.provider!r}")
