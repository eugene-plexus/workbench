"""Vectors, and the scan that finds the nearest (MR5, design 4.1, MRC8).

A passage's meaning is a vector of float32 numbers, kept as a little-endian BLOB in the
index's `chunks.vec` (NULL until it is embedded) and **scaled to length one**, so the
similarity of a question and a passage is their dot product (cosine), and the best passages
are the largest products. A person's vectors are read into one numpy matrix, kept in memory
while they are in use (and let go after ten idle minutes), and a search is one matrix-by-vector
product and a partial sort: 8.6 ms for 100,000 passages of 1,024 numbers, measured, against
218 ms for sqlite-vec's exact scan on the same machine, and no extension to load on any platform.
A person is past about 300,000 passages before this stops being the right answer, and the
matrix is the one thing to swap then (`Matrix` and `scan` are its whole interface).

**What is refused, by name.** A vector with a number that is not a number or all zeros
(`UnusableVectors`: EmbeddingGemma in half precision does this) is never stored. A stored
vector whose size is not the size the search was built with (`BadVector`) is never searched:
the search by meaning says so and offers Rebuild, and the words search goes on. A passage a
search model refused is stored as an **empty** BLOB (not NULL, which means "owed"), so it is
neither retried for ever nor searched.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

FLOAT = "<f4"
BYTES_PER_NUMBER = 4


class UnusableVectors(ValueError):
    """A search model gave numbers that cannot be searched (not numbers, or all zeros)."""


class BadVector(ValueError):
    """A stored vector is not the size the search was built with."""

    def __init__(self, chunk_id: int, size: int, expected: int) -> None:
        super().__init__(
            f"passage {chunk_id} holds {size} bytes of numbers where {expected} were expected"
        )
        self.chunk_id = chunk_id
        self.size = size
        self.expected = expected


def normalised(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """Vectors as float32 rows of length one. Raises `UnusableVectors` for a row that has a
    number that is not a number (NaN or infinite), or is all zeros."""
    try:
        lengths = {len(row) for row in vectors}
    except TypeError as exc:
        raise UnusableVectors(f"the vectors are not lists of numbers ({exc})") from exc
    if len(lengths) != 1 or 0 in lengths:
        raise UnusableVectors("the vectors are not all one length")
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise UnusableVectors(f"the vectors are not lists of numbers ({exc})") from exc
    if not np.isfinite(matrix).all():
        raise UnusableVectors("a vector holds a number that is not a number")
    norms = np.linalg.norm(matrix, axis=1)
    if (norms < 1e-12).any():
        raise UnusableVectors("a vector is all zeros")
    result: np.ndarray = matrix / norms[:, None]
    return result


def to_blob(row: np.ndarray) -> bytes:
    """One vector as it is stored: float32, little-endian."""
    return np.ascontiguousarray(row, dtype=FLOAT).tobytes()


def from_blob(blob: bytes, dim: int, chunk_id: int = 0) -> np.ndarray:
    """One stored vector. Raises `BadVector` when it is not `dim` numbers."""
    if len(blob) != dim * BYTES_PER_NUMBER:
        raise BadVector(chunk_id, len(blob), dim * BYTES_PER_NUMBER)
    result: np.ndarray = np.frombuffer(blob, dtype=FLOAT)
    return result


@dataclass
class Matrix:
    """One person's vectors, in memory: a row for each passage that has one."""

    dim: int
    #: The passage ids (`chunks.id`), one for each row.
    ids: np.ndarray
    data: np.ndarray
    #: For each row, which of `scopes` its document is in (a scope is `library` or `chat:<id>`).
    scope_codes: np.ndarray
    scopes: list[str]
    #: What the index's writes counted when this was read: a later write makes it stale.
    writes: int
    built_at: float = 0.0
    last_used: float = 0.0

    @property
    def size(self) -> int:
        return int(self.ids.shape[0])

    @property
    def nbytes(self) -> int:
        return int(self.data.nbytes + self.ids.nbytes + self.scope_codes.nbytes)


def assemble(
    dim: int,
    count: int,
    rows: Iterable[tuple[int, bytes, str]],
    writes: int,
    clock: Callable[[], float] = time.perf_counter,
) -> Matrix:
    """A matrix from `(passage id, stored vector, scope)` rows, `count` of them. Raises
    `BadVector` for the first row whose vector is not `dim` numbers."""
    ids = np.empty(count, dtype=np.int64)
    data = np.empty((count, dim), dtype=np.float32)
    codes = np.empty(count, dtype=np.int32)
    scopes: list[str] = []
    by_name: dict[str, int] = {}
    filled = 0
    for chunk_id, blob, scope in rows:
        if filled >= count:
            break
        data[filled] = from_blob(blob, dim, chunk_id)
        ids[filled] = chunk_id
        code = by_name.get(scope)
        if code is None:
            code = by_name[scope] = len(scopes)
            scopes.append(scope)
        codes[filled] = code
        filled += 1
    now = clock()
    return Matrix(
        dim,
        ids[:filled],
        data[:filled],
        codes[:filled],
        scopes,
        writes,
        built_at=now,
        last_used=now,
    )


def scan(
    matrix: Matrix, query: np.ndarray, scopes: Sequence[str], k: int
) -> list[tuple[int, float]]:
    """The `k` passages nearest `query` among those in these scopes, best first, as
    `(passage id, similarity)`. Both are length one, so similarity is a dot product. **The
    scope is part of what ranks**: a passage in another scope is never a candidate, so it
    cannot take the place of one that is. Ties are in the order of the passage ids."""
    if matrix.size == 0 or k <= 0:
        return []
    if query.shape != (matrix.dim,):
        raise ValueError(f"a question of {query.size} numbers cannot be searched in {matrix.dim}")
    wanted = [code for code, name in enumerate(matrix.scopes) if name in scopes]
    if not wanted:
        return []
    scores = matrix.data @ query.astype(np.float32, copy=False)
    available = matrix.size
    if len(wanted) != len(matrix.scopes):
        mask = np.isin(matrix.scope_codes, wanted)
        scores = np.where(mask, scores, -np.inf)
        available = int(mask.sum())
    k = min(k, available)
    if k <= 0:
        return []
    chosen = np.argpartition(-scores, k - 1)[:k]
    order = np.lexsort((matrix.ids[chosen], -scores[chosen]))
    chosen = chosen[order]
    return [(int(matrix.ids[i]), float(scores[i])) for i in chosen]


class MatrixCache:
    """The matrices in memory: one for a person, at most what fits `max_bytes` (the one
    just used always stays), and each let go after `idle_seconds` unused."""

    def __init__(
        self,
        *,
        idle_seconds: float = 600.0,
        max_bytes: int = 640 * 1024 * 1024,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.idle_seconds = idle_seconds
        self.max_bytes = max_bytes
        self._clock = clock
        self._by_person: OrderedDict[str, Matrix] = OrderedDict()

    def get(self, sub: str, writes: int) -> Matrix | None:
        """The person's matrix if it is still what their index holds (`writes` is what
        the index counts now), else None, and what was kept is let go."""
        found = self._by_person.get(sub)
        if found is None:
            return None
        if found.writes != writes:
            del self._by_person[sub]
            return None
        found.last_used = self._clock()
        self._by_person.move_to_end(sub)
        return found

    def put(self, sub: str, matrix: Matrix) -> None:
        matrix.last_used = self._clock()
        self._by_person[sub] = matrix
        self._by_person.move_to_end(sub)
        while len(self._by_person) > 1 and self.total_bytes > self.max_bytes:
            self._by_person.popitem(last=False)

    def drop(self, sub: str) -> None:
        self._by_person.pop(sub, None)

    def clear(self) -> None:
        self._by_person.clear()

    def evict_idle(self) -> int:
        """Let go of every matrix unused for `idle_seconds`. How many."""
        now = self._clock()
        stale = [s for s, m in self._by_person.items() if now - m.last_used >= self.idle_seconds]
        for sub in stale:
            del self._by_person[sub]
        return len(stale)

    @property
    def total_bytes(self) -> int:
        return sum(m.nbytes for m in self._by_person.values())

    def __len__(self) -> int:
        return len(self._by_person)

    def held(self) -> list[str]:
        return list(self._by_person)
