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
from tests.unit.test_verify import marks, printout


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


# -- what the dump says -------------------------------------------------
#
# doc/cli.md#sr-inspect, line by line, where the reference's dump
# settled what doc/ had left open.  tests/differential/test_reports.py
# holds the whole dump to the reference's over the corpus.


def dumped(records: list[Any]) -> list[str]:
    """Return the dump of some records, line by line.

    Args:
        records: The printout's records.

    """
    return list(dump("out.srp.jsonl", records, None))


def test_the_first_line_names_the_file_as_it_was_given() -> None:
    records = printout()
    given = "some\\where/out.srp.jsonl"
    assert next(iter(dump(given, records, None))) == f"printout {given}"


def test_the_counts_are_pages_and_fonts_and_any_data_blobs() -> None:
    records = printout()
    records[0]["warnings"] = [{"kind": "font", "message": "substituted"}]
    assert "  1 page, 1 font, 1 data blob" in dumped(records)
    del records[0]["data"]
    records[0]["fonts"] = []
    assert "  1 page, 0 fonts" in dumped(records)


@pytest.mark.parametrize(
    ("content", "size"),
    [("AA==", "1 byte"), ("AAA=", "2 bytes"), ("AAAA", "3 bytes"), ("", "0 bytes")],
)
def test_a_blob_is_counted_in_the_bytes_it_holds(content: str, size: str) -> None:
    records = printout()
    records[0]["data"] = {"pic.png": {"encoding": "base64", "content": content}}
    assert f"  pic.png  base64  {size}" in dumped(records)


def test_literal_text_is_counted_in_its_utf8_bytes() -> None:
    records = printout()
    records[0]["data"] = {"note": {"content": chr(0x160)}}
    assert "  note  2 bytes" in dumped(records)


def test_a_group_of_one_run_says_so_in_the_singular() -> None:
    records = printout()
    records[0]["groupRuns"] = {"a": 1, "bb": 2}
    records[0]["groupKeys"] = {"a": 1, "bb": 2}
    lines = dumped(records)
    assert "  a   1 run  1 key" in lines
    assert "  bb  2 runs  2 keys" in lines


def test_a_warning_is_its_kind_record_node_and_message() -> None:
    records = printout()
    records[0]["warnings"] = [
        {
            "kind": "overflow",
            "node": "report > layout > detail",
            "record": 0,
            "message": "too tall",
        },
        {
            "kind": "unknown",
            "node": "report > layout",
            "prop": "wobble",
            "message": "unknown property",
        },
        {"kind": "font", "message": "substituted"},
    ]
    lines = dumped(records)
    start = lines.index("warnings")
    assert lines[start + 1 : start + 4] == [
        "  overflow  record 0  report > layout > detail  too tall",
        "  unknown  report > layout wobble=  unknown property",
        "  font  substituted",
    ]


def test_a_number_is_shown_as_the_printout_holds_it() -> None:
    records = printout()
    marks(records)[1]["box"]["width"] = 23.759999999999998
    marks(records)[1]["box"]["x"] = 1e-7
    lines = dumped(records)
    assert any("box 0.0000001,5 23.759999999999998x6" in one for one in lines)


def test_a_page_of_its_own_geometry_names_its_margins() -> None:
    records = printout()
    records[1]["leftMargin"] = 0
    heading = [one for one in dumped(records) if one.startswith("page 1")]
    assert heading == [
        "page 1  100 x 60 pt, margins left 0 right 5 top 5 bottom 5  6 marks"
    ]
    del records[1]["leftMargin"]
    heading = [one for one in dumped(records) if one.startswith("page 1")]
    assert heading == ["page 1  100 x 60  6 marks"]


def test_an_outline_entry_is_its_level_title_and_quoted_name() -> None:
    records = printout()
    marks(records)[0].update(name="a b", closed=True)
    assert '  outline  at 5,5  level 1  "top"  name "a b"  closed' in dumped(records)


def test_text_is_quoted_as_the_expression_language_quotes_it() -> None:
    """doc/expressions.md#quoted-strings: not printable, then escaped."""
    records = printout()
    space = chr(0xA0)
    marks(records)[1]["lines"] = [f'a{space}b\t"c"\\\x07\U0001f600']
    expected = '    "a' + "\\u00a0" + 'b\\t\\"c\\"\\\\\\a\U0001f600"'
    assert expected in dumped(records)


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
    list(dump("broken.srp.jsonl", records, None))
    list(dump("broken.srp.jsonl", records, {1}))
    violations(records)
