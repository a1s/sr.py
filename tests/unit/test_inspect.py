"""Reading a printout back, as `sr inspect` reads one another engine wrote.

doc/printout.md#cbor writes a printout in a small part of CBOR,
and the reader takes that part and refuses the rest at its byte:
a general decoder would turn a tag or a byte string into a value
no printout holds, and the dump would show it as if one could.

Whatever the records hold, the dump and the check read them
without crashing.  The last tests here replace each value of a valid
printout with values of every wrong type in turn, and run both.

"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cbor2
import pytest

from sr.errors import BuildError
from sr.printout.inspect import cbor_records, dump, read_cbor
from sr.printout.verify import violations
from tests.unit.test_verify import printout


def test_the_part_of_cbor_a_printout_is_written_in_is_read() -> None:
    value = {
        "text": "é",
        "counts": [0, 23, 24, 255, 256, 65536, 2**32, 2**64 - 1, -1, -(2**64)],
        "flags": [True, False, None],
        "length": 1.5,
    }
    assert cbor_records(cbor2.dumps(value) + cbor2.dumps([])) == [value, []]


def test_a_float_of_any_width_is_read() -> None:
    """A length is written as a double, and a narrower one means the same."""
    half, single = bytes.fromhex("f93e00"), bytes.fromhex("fa3fc00000")
    assert cbor_records(half + single) == [1.5, 1.5]


@pytest.mark.parametrize(
    ("data", "said"),
    [
        ("c074323032362d30382d30345430393a31323a34345a", "at byte 0: a tag"),
        ("d9d9f7a0", "at byte 0: a tag"),
        ("a1636b657944ffd8ffe0", "at byte 5: a byte string"),
        ("bfff", "at byte 0: an indefinite length"),
        ("7f6161ff", "at byte 0: an indefinite length"),
        ("f7", "at byte 0: a simple value"),
        ("a10102", "at byte 1: a map key that is not text"),
        ("a2616101", "at byte 4: the bytes end inside an item"),
        ("62c328", "at byte 0: a text string not in UTF-8"),
        ("1c", "at byte 0: a reserved additional information"),
    ],
)
def test_what_a_printout_does_not_hold_is_refused_at_its_byte(
    data: str, said: str
) -> None:
    with pytest.raises(ValueError, match=said):
        cbor_records(bytes.fromhex(data))


def test_nesting_deeper_than_any_printout_is_refused() -> None:
    with pytest.raises(ValueError, match="nested deeper than any printout"):
        cbor_records(b"\x81" * 1000 + b"\x80")


def test_a_printout_holding_a_tag_or_bytes_is_not_one(tmp_path: Path) -> None:
    """A tagged `built` and a blob's bytes, where both are text."""
    header = cbor2.dumps(
        {"sr": 1, "kind": "header", "built": cbor2.CBORTag(0, "2026-08-04")}
    )
    path = tmp_path / "tagged.srp.cbor"
    path.write_bytes(header)
    with pytest.raises(BuildError, match="not a printout: at byte 23: a tag"):
        read_cbor(path)
    blob = {"img": {"encoding": "base64", "content": b"\x89PNG"}}
    path.write_bytes(cbor2.dumps({"sr": 1, "kind": "header", "data": blob}))
    with pytest.raises(BuildError, match="a byte string"):
        read_cbor(path)


# -- wrong types everywhere ---------------------------------------------


# One value of every type a record can hold, and the numbers that are
# not lengths: an infinity, a NaN, and integers no double holds.
WRONG = [
    None,
    "x",
    [],
    ["x"],
    {},
    {"a": 1},
    True,
    -1,
    10**400,
    float("inf"),
    float("nan"),
    1e308,
]


def places(
    value: Any,
    path: tuple[Any, ...] = (),
) -> Iterator[tuple[Any, ...]]:
    """Yield the path to every value inside a record, containers too.

    Args:
        value: The record, or a value inside one.
        path: Where it is.

    """
    if path:
        yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from places(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from places(item, (*path, index))


def changed(path: tuple[Any, ...], wrong: Any) -> list[Any]:
    """Return a valid printout with one value replaced.

    Args:
        path: Where the value is, from the record list down.
        wrong: What replaces it.

    """
    records = copy.deepcopy(printout())
    here: Any = records
    for step in path[:-1]:
        here = here[step]
    here[path[-1]] = wrong
    return records


# The header record itself is not replaced: a reader returns a header
# object or refuses the file, so neither reads anything else.
PLACES = [path for path in places(printout()) if path != (0,)]
CHANGES = [(path, wrong) for path in PLACES for wrong in WRONG]


@pytest.mark.parametrize(("path", "wrong"), CHANGES)
def test_inspect_reads_a_wrong_value_anywhere_without_crashing(
    path: tuple[Any, ...],
    wrong: Any,
) -> None:
    records = changed(path, wrong)
    list(dump(Path("broken.srp.jsonl"), records, None))
    list(dump(Path("broken.srp.jsonl"), records, {1}))
    violations(records)
