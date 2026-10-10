"""Records: the two JSON shapes, and coercion by declared type."""

from __future__ import annotations

import codecs
import io
from pathlib import Path
from typing import Any

import pytest

from sr.data import as_text, coerce, read_records, records_from
from sr.errors import BadValue, BuildError, NodePath
from sr.expr import Decimal, Record, Time
from sr.template.load import load_text
from sr.template.model import Member, Records, parse_text

TEMPLATE = """
report name="Data" {
  records {
    member "s" type="string"
    member "i" type="int"
    member "d" type="decimal"
    member "f" type="float"
    member "b" type="bool"
    member "t" type="datetime"
    member "n" type="int" nullable=#true
    member "o" type="object"
  }
  layout width=100 height=100 {
    detail height=10
  }
}
"""


def declared() -> Records:
    """Return the `records` node of the template above."""
    report = load_text(TEMPLATE).require()
    assert report.records is not None
    return report.records


def member(kind: str, nullable: bool = False, format: str | None = None) -> Member:
    """Return one declared member."""
    return Member("m", kind, nullable, format, NodePath())


# -- the two shapes ---------------------------------------------------


def test_ndjson_is_one_record_per_line() -> None:
    rows = records_from('{"a":1}\n{"a":2}\n', None)
    assert [one["a"] for one in rows] == [1, 2]


def test_an_array_document_is_the_other_shape() -> None:
    rows = records_from('[{"a":1},{"a":2}]', None)
    assert [one["a"] for one in rows] == [1, 2]


def test_blank_lines_are_not_records() -> None:
    assert len(records_from('{"a":1}\n\n\n{"a":2}\n', None)) == 2


def test_an_empty_document_holds_no_records() -> None:
    assert records_from("   \n", None) == ()


def test_a_broken_line_names_its_line() -> None:
    with pytest.raises(BuildError) as refused:
        records_from('{"a":1}\n{oops}\n', None, "data.jsonl")
    assert "data.jsonl:2" in str(refused.value)


def test_a_row_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(BuildError) as refused:
        records_from("[1,2]", None)
    assert "record 0" in str(refused.value)


def test_a_path_is_read(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a":1}\n', encoding="utf-8")
    assert len(read_records(path, None)) == 1


def test_a_byte_that_is_not_utf8_reads_as_one_replacement_each(
    tmp_path: Path,
) -> None:
    # doc/template.md#data-input, as the reference reads it.  `\xe2\x82`
    # starts a sequence that never finishes, and is two U+FFFD rather
    # than the one that a decoder's own "replace" would make of it.
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":"Caf\xe2\x82A\xe9!"}\n')
    assert read_records(path, None)[0]["a"] == "Caf\ufffd\ufffdA\ufffd!"


@pytest.mark.parametrize("marks", [1, 2])
def test_byte_order_marks_at_the_start_are_skipped(
    tmp_path: Path,
    marks: int,
) -> None:
    # Two is what Windows PowerShell 5.1 pipes when the console's
    # input encoding and `$OutputEncoding` are both UTF-8 with a mark.
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" * marks + b'{"a":1}\n')
    assert read_records(path, None)[0]["a"] == 1


def test_files_that_each_open_with_a_mark_can_be_joined(
    tmp_path: Path,
) -> None:
    # Each NDJSON line is a JSON text, and a mark at its start is skipped.
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'\xef\xbb\xbf{"a":1}\n\xef\xbb\xbf{"a":2}\n')
    assert [row["a"] for row in read_records(path, None)] == [1, 2]


@pytest.mark.parametrize(
    ("written", "line"),
    [
        (b'{"a":1}\xef\xbb\xbf\n', 1),
        (b'{"a":1}\n{"a":\xef\xbb\xbf2}\n', 2),
        (b'[\xef\xbb\xbf{"a":1}]', None),
    ],
)
def test_a_mark_anywhere_else_outside_a_string_is_refused(
    tmp_path: Path, written: bytes, line: int | None
) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_bytes(written)
    with pytest.raises(BuildError) as refused:
        read_records(path, None)
    at = f"{path}:{line}" if line else str(path)
    assert str(refused.value).startswith(f"{at}: not JSON")


def test_a_mark_inside_a_string_is_a_character(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":"x\xef\xbb\xbfy"}\n')
    value = read_records(path, None)[0]["a"]
    assert value == "x\N{BYTE ORDER MARK}y"


@pytest.mark.parametrize(
    "mark",
    [codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE],
    ids=["little-endian", "big-endian"],
)
def test_utf16_is_refused_in_words_that_say_so(
    tmp_path: Path,
    mark: bytes,
) -> None:
    # What Windows PowerShell 5.1 writes for `>`.
    # Read as UTF-8 it would be refused with only "Expecting value" to go on.
    codec = "utf-16-le" if mark == codecs.BOM_UTF16_LE else "utf-16-be"
    path = tmp_path / "rows.jsonl"
    path.write_bytes(mark + '{"a":1}\n'.encode(codec))
    with pytest.raises(BuildError) as refused:
        read_records(path, None)
    want = f"{path}:1: not JSON: it is UTF-16; save it as UTF-8"
    assert str(refused.value) == want


def test_utf16_on_a_stream_asks_for_it_to_be_sent_as_utf8() -> None:
    # Standard input as the command line sets it up.
    # A pipe is not a file, so there is nothing to save.
    written = codecs.BOM_UTF16_LE + '{"a":1}\n'.encode("utf-16-le")
    stream = io.TextIOWrapper(
        io.BytesIO(written), encoding="utf-8", errors="surrogateescape"
    )
    with pytest.raises(BuildError) as refused:
        read_records(stream, None)
    want = "standard input:1: not JSON: it is UTF-16; send it as UTF-8"
    assert str(refused.value) == want


# -- coercion ---------------------------------------------------------


def test_a_declared_member_is_coerced_once() -> None:
    row = records_from(
        '{"s":"txt","i":"6","d":"1.50","f":"1.5","b":"true",'
        '"t":"2005-05-24T22:53:30Z","n":null,"o":{"k":1}}',
        declared(),
    )[0]
    assert row["s"] == "txt"
    assert row["i"] == 6
    assert isinstance(row["d"], Decimal)
    assert str(row["d"]) == "1.50"
    assert row["f"] == 1.5
    assert row["b"] is True
    assert isinstance(row["t"], Time)
    assert row["n"] is None
    assert isinstance(row["o"], Record)


def test_a_json_number_reaches_a_decimal_through_its_own_spelling() -> None:
    row = records_from('{"d":1.50}', declared())[0]
    assert str(row["d"]) == "1.5"


def test_a_lone_surrogate_escape_reads_as_u_fffd() -> None:
    rows = records_from('{"s":"a\\ud800b","\\udfff":["\\ud83d\\ude00"]}', None)
    assert rows[0]["s"] == "a\ufffdb"
    # A whole pair is one character, and stays it.
    assert list(rows[0]["\ufffd"]) == ["\U0001f600"]


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("string", "A\udcffB", "A\ufffdB"),
        ("string", "A\udce2\udc82B", "A\ufffd\ufffdB"),
        ("list", '["C\udcffD"]', ["C\ufffdD"]),
    ],
)
def test_parameter_text_reads_each_byte_that_is_not_utf8_as_u_fffd(
    kind: str, text: str, expected: Any
) -> None:
    # A command line on Linux hands Python such a byte as a surrogate.
    found = parse_text(kind, text)
    assert (list(found) if kind == "list" else found) == expected


def test_an_undeclared_member_is_passed_through() -> None:
    row = records_from('{"extra":{"k":[1,2]}}', declared())[0]
    assert isinstance(row["extra"], Record)


def test_a_null_in_a_member_that_is_not_nullable_names_it() -> None:
    with pytest.raises(BuildError) as refused:
        records_from('{"i":null}', declared())
    said = str(refused.value)
    assert "'i'" in said
    assert "record 0" in said


def test_a_value_of_the_wrong_type_names_the_member() -> None:
    with pytest.raises(BuildError) as refused:
        records_from('{"i":"nine"}', declared())
    assert "'i'" in str(refused.value)


def test_an_object_member_wants_an_object() -> None:
    with pytest.raises(BadValue):
        coerce([1, 2], member("object"))


def test_a_scalar_member_will_not_take_a_container() -> None:
    with pytest.raises(BadValue):
        coerce({"k": 1}, member("int"))


@pytest.mark.parametrize(
    ("value", "text"),
    [(True, "true"), (False, "false"), (5, "5"), (1.5, "1.5"), ("x", "x")],
)
def test_a_scalar_is_read_through_the_text_that_spells_it(
    value: object, text: str
) -> None:
    assert as_text(value) == text
