"""Records: the two JSON shapes, and coercion by declared type."""

from __future__ import annotations

from pathlib import Path

import pytest

from sr.data import as_text, coerce, read_records, records_from
from sr.errors import BadValue, BuildError, NodePath
from sr.expr import Decimal, Record, Time
from sr.template.load import load_text
from sr.template.model import Member, Records

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
