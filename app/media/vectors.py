"""FAISS vector indexes, held locally like every other heavy artefact.

An index over a few hundred thousand chunks is hundreds of megabytes -- exactly
the kind of file that stays on this machine rather than going to Neon. It is
persisted through the media store, so it gets the same content addressing,
deduplication and integrity checking as a CAD upload.

The index is deliberately thin: build, add, search, save, load. What gets
embedded, and how, belongs to the AI layer when it lands.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.media.service import MediaService
from app.media.store import MediaError
from app.models.media import Media, MediaKind

Metric = Literal["l2", "cosine"]


class VectorIndexError(MediaError):
    """The index could not be built, loaded, or queried as asked."""


@dataclass
class SearchHit:
    id: int
    score: float
    """Squared L2 distance for `l2` (lower is closer), cosine similarity for
    `cosine` (higher is closer)."""


class LocalVectorIndex:
    def __init__(self, index, dimension: int, metric: Metric) -> None:
        self._index = index
        self.dimension = dimension
        self.metric: Metric = metric

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(cls, dimension: int, metric: Metric = "cosine") -> "LocalVectorIndex":
        import faiss

        if dimension <= 0:
            raise VectorIndexError("dimension must be positive")
        # Inner product on L2-normalised vectors is cosine similarity; FAISS has
        # no separate cosine metric.
        base = faiss.IndexFlatIP(dimension) if metric == "cosine" else faiss.IndexFlatL2(dimension)
        # IDMap so callers can address vectors by their own row ids rather than
        # by insertion order, which shifts the moment anything is rebuilt.
        return cls(faiss.IndexIDMap2(base), dimension, metric)

    @classmethod
    def build(
        cls,
        vectors: NDArray[np.float32],
        ids: NDArray[np.int64] | list[int],
        metric: Metric = "cosine",
    ) -> "LocalVectorIndex":
        vectors = cls._as_float32(vectors)
        index = cls.create(vectors.shape[1], metric)
        index.add(vectors, ids)
        return index

    # -- writing --------------------------------------------------------------

    def add(self, vectors: NDArray[np.float32], ids: NDArray[np.int64] | list[int]) -> None:
        vectors = self._as_float32(vectors)
        if vectors.shape[1] != self.dimension:
            raise VectorIndexError(
                f"vectors have dimension {vectors.shape[1]}, index expects {self.dimension}"
            )
        id_array = np.asarray(ids, dtype=np.int64)
        if len(id_array) != len(vectors):
            raise VectorIndexError(
                f"got {len(vectors)} vectors but {len(id_array)} ids"
            )
        self._index.add_with_ids(self._prepare(vectors), id_array)

    def remove(self, ids: NDArray[np.int64] | list[int]) -> int:
        import faiss

        selector = faiss.IDSelectorBatch(np.asarray(ids, dtype=np.int64))
        return int(self._index.remove_ids(selector))

    # -- querying -------------------------------------------------------------

    def search(self, query: NDArray[np.float32], k: int = 5) -> list[list[SearchHit]]:
        """Nearest neighbours for each query row, closest first."""
        if k <= 0:
            raise VectorIndexError("k must be positive")
        if self.count == 0:
            raise VectorIndexError("index is empty")

        vectors = self._as_float32(query)
        if vectors.shape[1] != self.dimension:
            raise VectorIndexError(
                f"query has dimension {vectors.shape[1]}, index expects {self.dimension}"
            )

        scores, ids = self._index.search(self._prepare(vectors), min(k, self.count))
        return [
            [SearchHit(id=int(i), score=float(s)) for i, s in zip(row_ids, row_scores) if i != -1]
            for row_ids, row_scores in zip(ids, scores)
        ]

    @property
    def count(self) -> int:
        return int(self._index.ntotal)

    # -- persistence ----------------------------------------------------------

    def save(self, service: MediaService, *, owner_id: str, name: str) -> Media:
        """Write the index to the local blob store and register it."""
        import faiss

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "index.faiss"
            faiss.write_index(self._index, str(path))
            return service.store_path(
                owner_id=owner_id,
                kind=MediaKind.VECTOR_INDEX,
                path=path,
                filename=f"{name}.faiss",
                content_type="application/x-faiss-index",
                meta={
                    "dimension": self.dimension,
                    "metric": self.metric,
                    "vector_count": self.count,
                },
            )

    @classmethod
    def load(cls, service: MediaService, media: Media) -> "LocalVectorIndex":
        import faiss

        if media.kind is not MediaKind.VECTOR_INDEX:
            raise VectorIndexError(f"media {media.id} is a {media.kind.value}, not an index")

        index = faiss.read_index(str(service.local_path(media)))
        metric: Metric = media.meta.get("metric", "cosine")
        return cls(index, index.d, metric)

    # -- internals ------------------------------------------------------------

    def _prepare(self, vectors: NDArray[np.float32]) -> NDArray[np.float32]:
        if self.metric != "cosine":
            return vectors
        import faiss

        # normalize_L2 works in place, so never hand it the caller's array.
        normalised = np.ascontiguousarray(vectors.copy())
        faiss.normalize_L2(normalised)
        return normalised

    @staticmethod
    def _as_float32(vectors) -> NDArray[np.float32]:
        array = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
        if array.ndim == 1:
            array = array.reshape(1, -1)
        if array.ndim != 2:
            raise VectorIndexError(f"expected a 2-D array of vectors, got shape {array.shape}")
        return array
