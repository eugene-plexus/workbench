"""Vectors, and the scan that finds the nearest (MR5, design 4.1, MRC8): cosine over unit
vectors, the top k, scope as part of what ranks, and what is refused by name.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from eugene_plexus_workbench.knowledge import vectors
from eugene_plexus_workbench.knowledge.vectors import (
    BadVector,
    Matrix,
    MatrixCache,
    UnusableVectors,
)

DIM = 16


def unit_rows(n: int, seed: int = 7, dim: int = DIM) -> np.ndarray:
    rows = np.random.default_rng(seed).normal(size=(n, dim)).astype(np.float32)
    return vectors.normalised(rows.tolist())


def matrix_of(rows: np.ndarray, scopes: list[str] | None = None, writes: int = 0) -> Matrix:
    scopes = scopes or ["library"] * len(rows)
    return vectors.assemble(
        rows.shape[1],
        len(rows),
        ((i + 1, vectors.to_blob(rows[i]), scopes[i]) for i in range(len(rows))),
        writes,
    )


# --------------------------------------------------------------------------- #
# what is stored
# --------------------------------------------------------------------------- #


def test_vectors_are_scaled_to_length_one() -> None:
    got = vectors.normalised([[3.0, 4.0], [0.0, 2.0]])
    assert got.dtype == np.float32
    assert np.allclose(np.linalg.norm(got, axis=1), 1.0, atol=1e-6)
    assert np.allclose(got[0], [0.6, 0.8], atol=1e-6)


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ([[1.0, float("nan")]], "not a number"),
        ([[1.0, float("inf")]], "not a number"),
        ([[0.0, 0.0]], "all zeros"),
        ([[1.0, 2.0], [3.0]], "not all one length"),
        ([], "not all one length"),
    ],
)
def test_numbers_that_cannot_be_searched_are_refused_and_named(
    rows: list[list[float]], why: str
) -> None:
    with pytest.raises(UnusableVectors, match=why):
        vectors.normalised(rows)


def test_a_vector_is_stored_as_little_endian_float32_and_comes_back_whole() -> None:
    row = np.array([1.0, -2.5, 0.125], dtype=np.float32)
    blob = vectors.to_blob(row)
    assert blob == struct.pack("<3f", 1.0, -2.5, 0.125) and len(blob) == 12
    assert np.array_equal(vectors.from_blob(blob, 3), row)


def test_a_stored_vector_of_another_size_is_refused_by_name() -> None:
    with pytest.raises(BadVector) as caught:
        vectors.from_blob(b"\x00" * 20, 4, chunk_id=77)
    assert (caught.value.chunk_id, caught.value.size, caught.value.expected) == (77, 20, 16)
    assert "passage 77" in str(caught.value)


def test_a_matrix_is_not_assembled_from_a_vector_of_the_wrong_size() -> None:
    rows = [
        (1, vectors.to_blob(np.ones(4, dtype=np.float32)), "library"),
        (2, b"\x00" * 8, "library"),
    ]
    with pytest.raises(BadVector) as caught:
        vectors.assemble(4, 2, rows, 0)
    assert caught.value.chunk_id == 2


# --------------------------------------------------------------------------- #
# the scan
# --------------------------------------------------------------------------- #


def test_the_nearest_passages_are_the_largest_dot_products_best_first() -> None:
    rows = unit_rows(300)
    matrix = matrix_of(rows)
    question = unit_rows(1, seed=99)[0]
    got = vectors.scan(matrix, question, ["library"], 10)
    brute = sorted(((float(rows[i] @ question), i + 1) for i in range(len(rows))), reverse=True)[
        :10
    ]
    assert [i for i, _ in got] == [i for _, i in brute]
    assert [round(s, 5) for _, s in got] == [round(s, 5) for s, _ in brute]
    assert all(got[n][1] >= got[n + 1][1] for n in range(9)), "best first"
    assert all(-1.0001 <= s <= 1.0001 for _, s in got), "cosine of unit vectors"


def test_a_passage_identical_to_the_question_is_first_with_similarity_one() -> None:
    rows = unit_rows(50)
    got = vectors.scan(matrix_of(rows), rows[17], ["library"], 3)
    assert got[0][0] == 18 and abs(got[0][1] - 1.0) < 1e-5


def test_asking_for_more_than_there_are_gives_all_of_them() -> None:
    rows = unit_rows(5)
    assert len(vectors.scan(matrix_of(rows), rows[0], ["library"], 50)) == 5


def test_the_scope_is_part_of_what_ranks_so_another_scope_never_takes_a_place() -> None:
    rows = unit_rows(20)
    scopes = ["library"] * 10 + ["chat:one"] * 10
    matrix = matrix_of(rows, scopes)
    question = rows[15]  # exactly a passage of chat:one
    mine = vectors.scan(matrix, question, ["library"], 5)
    assert len(mine) == 5 and all(i <= 10 for i, _ in mine), "no passage of another scope"
    both = vectors.scan(matrix, question, ["library", "chat:one"], 5)
    assert both[0][0] == 16
    only_chat = vectors.scan(matrix, question, ["chat:one"], 50)
    assert len(only_chat) == 10 and all(i > 10 for i, _ in only_chat)
    assert vectors.scan(matrix, question, ["chat:two"], 5) == []
    assert vectors.scan(matrix, question, [], 5) == []


def test_equal_similarities_are_in_the_order_of_the_passage_ids() -> None:
    one = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    matrix = vectors.assemble(4, 4, ((i, vectors.to_blob(one), "library") for i in (4, 2, 9, 1)), 0)
    assert [i for i, _ in vectors.scan(matrix, one, ["library"], 4)] == [1, 2, 4, 9]


def test_nothing_is_found_in_nothing() -> None:
    empty = vectors.assemble(4, 0, iter(()), 0)
    q = np.ones(4, dtype=np.float32) / 2
    assert vectors.scan(empty, q, ["library"], 5) == []
    rows = unit_rows(3, dim=4)
    assert vectors.scan(matrix_of(rows), rows[0], ["library"], 0) == []


def test_a_question_of_another_size_than_the_vectors_is_not_scanned() -> None:
    rows = unit_rows(3)
    with pytest.raises(ValueError, match="cannot be searched"):
        vectors.scan(matrix_of(rows), np.ones(DIM + 1, dtype=np.float32), ["library"], 3)


# --------------------------------------------------------------------------- #
# the matrices in memory
# --------------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_matrix_is_kept_while_the_index_has_not_been_written_to_and_let_go_after() -> None:
    cache = MatrixCache()
    first = matrix_of(unit_rows(3), writes=4)
    cache.put("ada", first)
    assert cache.get("ada", 4) is first
    assert cache.get("ada", 5) is None, "a write made it stale"
    assert cache.get("ada", 4) is None, "and what was stale is not kept"
    assert len(cache) == 0


def test_a_matrix_nobody_uses_is_let_go_after_ten_minutes() -> None:
    clock = Clock()
    cache = MatrixCache(idle_seconds=600.0, clock=clock)
    cache.put("ada", matrix_of(unit_rows(3)))
    clock.now += 599
    assert cache.evict_idle() == 0
    clock.now += 2
    assert cache.evict_idle() == 1 and cache.get("ada", 0) is None


def test_using_a_matrix_keeps_it_for_another_ten_minutes() -> None:
    clock = Clock()
    cache = MatrixCache(idle_seconds=600.0, clock=clock)
    cache.put("ada", matrix_of(unit_rows(3)))
    clock.now += 500
    assert cache.get("ada", 0) is not None
    clock.now += 500
    assert cache.evict_idle() == 0, "used 500 s ago"


def test_the_matrices_in_memory_are_bounded_and_the_one_just_used_stays() -> None:
    cache = MatrixCache(max_bytes=2_000)
    big = unit_rows(10, dim=32)  # 1,280 bytes of numbers, and its ids
    for person in ("ada", "bo", "cy"):
        cache.put(person, matrix_of(big))
    assert cache.held() == ["cy"], "each is more than half the bound: only the newest stays"
    cache.drop("cy")
    assert len(cache) == 0
