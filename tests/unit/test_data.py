"""Records: the two JSON shapes, coercion by declared type, and host rows."""

from __future__ import annotations

import codecs
import decimal
import io
import numbers
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest

from sr.data import as_text, coerce, read_records, records_from, records_given
from sr.errors import BadValue, BuildError, NodePath
from sr.expr import Decimal, Duration, FrozenList, Record, Time
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


@pytest.mark.parametrize(
    "before",
    [b"", codecs.BOM_UTF8],
    ids=["bare", "after-a-utf-8-mark"],
)
def test_utf16_on_a_stream_asks_for_it_to_be_sent_as_utf8(
    before: bytes,
) -> None:
    # Standard input as the command line sets it up.  A pipe is not
    # a file, so there is nothing to save.  Windows PowerShell 5.1 puts
    # a UTF-8 mark in front when the console's input encoding has one,
    # even of records that `$OutputEncoding` sends as UTF-16.
    utf16 = codecs.BOM_UTF16_LE + '{"a":1}\n'.encode("utf-16-le")
    written = before + utf16
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


# -- records from the host --------------------------------------------


class Pair(NamedTuple):
    """A row with names, by the protocol SQLAlchemy's ``Row`` keeps too."""

    number: int
    name: str


@dataclass
class Line:
    """A row as a dataclass, with a field that is not public."""

    number: int
    _note: str = ""


class Entity:
    """A row as a plain object, with bookkeeping beside its columns."""

    def __init__(self, number: int) -> None:
        """Hold one column and the state an ORM would keep."""
        self.number = number
        self._state = "bookkeeping"


class Colour(Enum):
    """What an enumerated column holds."""

    RED = "red"


class Expired:
    """An ORM entity a commit has expired: bookkeeping, and no column."""

    def __init__(self) -> None:
        """Hold the state an ORM keeps, and nothing else."""
        self._state = "expired"


class Order:
    """A row whose total is a method, which a program can forget to call."""

    def total(self) -> int:
        """Return the order's total."""
        return 5


class Loading:
    """An attribute an ORM loads when it is read, as a relationship is."""

    def __set_name__(self, owner: type, name: str) -> None:
        """Remember the attribute's name."""
        self.name = name

    def __get__(self, row: Any, owner: type | None = None) -> Any:
        """Return what the row holds, or fail as a load would."""
        if row is None:
            return self
        if self.name not in vars(row):
            raise AssertionError(f"the build loaded {self.name}")
        return vars(row)[self.name]

    def __set__(self, row: Any, value: Any) -> None:
        """Hold a value, as a load does."""
        vars(row)[self.name] = value


@dataclass
class Rental:
    """A mapped dataclass, as SQLAlchemy's ``MappedAsDataclass`` makes one."""

    number: int
    film: Any = Loading()


@dataclass
class Draft:
    """A dataclass with a field its constructor does not set."""

    number: int
    total: int = field(init=False)


class Slotted:
    """A row that keeps its columns in slots, as an ``attrs`` class does."""

    __slots__ = ("_cache", "name", "number")

    def __init__(self, number: int) -> None:
        """Hold one column, and leave the other empty."""
        self.number = number
        self._cache = None


@dataclass(slots=True)
class Point:
    """A dataclass that keeps its fields in slots."""

    x: int
    y: int


class Detached:
    """A row whose driver fails to read it, as a closed session's does."""

    def keys(self) -> list[str]:
        """Return the row's names."""
        return ["number"]

    def __getitem__(self, name: str) -> Any:
        """Fail, as reading a column of a detached row does."""
        raise RuntimeError("detached")


class Cents(int):
    """An int of a type of its own."""


class Ratio(float):
    """A float of a type of its own, as numpy's ``float64`` is."""


class Label(str):
    """A str of a type of its own."""

    __slots__ = ()


class Count:
    """An integer that is not an int, as numpy's ``int64`` is not."""

    def __init__(self, number: int) -> None:
        """Hold the number."""
        self._number = number

    def __int__(self) -> int:
        """Return the number."""
        return self._number


numbers.Integral.register(Count)


def records(**kinds: str) -> Records:
    """Return a `records` node declaring each member by its type."""
    members = (
        Member(name, kind, False, None, NodePath()) for name, kind in kinds.items()
    )
    return Records(tuple(members), NodePath())


def one(row: Any, declared: Records | None = None) -> Record:
    """Return the record a single host row makes."""
    (found,) = records_given([row], declared)
    return found


def test_a_mapping_is_a_record() -> None:
    rows = records_given([{"a": 1}, {"a": 2}], None)
    assert [row["a"] for row in rows] == [1, 2]


def test_a_named_tuple_is_a_record_by_its_names() -> None:
    assert list(one(Pair(1, "x")).items()) == [("number", 1), ("name", "x")]


def test_a_cursor_of_database_rows_is_records_by_their_keys() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            "select 1 as number, 'x' as name union all select 2, 'y'"
        )
        rows = records_given(cursor, None)
    finally:
        connection.close()
    assert [dict(row) for row in rows] == [
        {"number": 1, "name": "x"},
        {"number": 2, "name": "y"},
    ]


def test_a_dataclass_is_a_record_by_its_public_fields() -> None:
    assert dict(one(Line(3, "private"))) == {"number": 3}


def test_an_object_is_a_record_by_its_public_attributes() -> None:
    assert dict(one(Entity(4))) == {"number": 4}


def test_a_generator_is_read_once_in_order() -> None:
    rows = records_given(({"a": number} for number in range(3)), None)
    assert [row["a"] for row in rows] == [0, 1, 2]


@pytest.mark.parametrize(
    "rows",
    ["text", {"a": 1}, 5, bytearray(b"[]"), memoryview(b"[]"), frozenset()],
)
def test_what_is_not_a_sequence_of_records_is_refused(rows: Any) -> None:
    with pytest.raises(BuildError) as refused:
        records_given(rows, None)
    assert str(refused.value).startswith("want a sequence of records, got ")


def test_a_plain_tuple_is_refused_for_having_no_names() -> None:
    # What a DB-API cursor fetches without a row factory.
    with pytest.raises(BuildError) as refused:
        records_given([{"a": 1}, (1, 2)], None)
    want = (
        "record 1: want a mapping or an object, got tuple, whose fields have no names"
    )
    assert str(refused.value) == want


def test_a_host_decimal_stays_exact_declared_or_not() -> None:
    row = one(
        {"d": decimal.Decimal("1.50"), "extra": decimal.Decimal("0.10")},
        records(d="decimal"),
    )
    assert isinstance(row["d"], Decimal)
    assert str(row["d"]) == "1.50"
    # JSON would have made a float of an undeclared number.
    assert isinstance(row["extra"], Decimal)
    assert str(row["extra"]) == "0.10"


def test_a_decimal_that_is_not_finite_is_refused() -> None:
    with pytest.raises(BuildError) as refused:
        one({"d": decimal.Decimal("NaN")})
    assert str(refused.value) == "record 0: member 'd': not a finite decimal: NaN"


def test_a_datetime_with_no_zone_is_read_in_utc() -> None:
    declared = records(t="datetime")
    row = one({"t": datetime(2005, 5, 24, 22, 53, 30)}, declared)
    from_json = records_from('{"t":"2005-05-24T22:53:30Z"}', declared)[0]["t"]
    assert row["t"] == from_json


def test_a_datetime_with_a_zone_keeps_its_instant_and_its_offset() -> None:
    plus_three = timezone(timedelta(hours=3))
    row = one({"t": datetime(2005, 5, 25, 1, 53, 30, 250000, tzinfo=plus_three)})
    moment = row["t"]
    assert isinstance(moment, Time)
    assert moment.format("2006-01-02T15:04:05.000Z07:00") == (
        "2005-05-25T01:53:30.250+03:00"
    )
    in_utc = datetime(2005, 5, 24, 22, 53, 30, 250000, tzinfo=UTC)
    assert moment == Time.from_datetime(in_utc)


def test_a_date_is_the_start_of_its_day_in_utc() -> None:
    declared = records(day="date")
    from_json = records_from('{"day":"2005-05-24"}', declared)[0]["day"]
    assert one({"day": date(2005, 5, 24)}, declared)["day"] == from_json
    assert one({"day": date(2005, 5, 24)})["day"] == from_json


def test_a_timedelta_is_a_duration_to_the_microsecond() -> None:
    lag = one({"lag": timedelta(days=1, microseconds=3)})["lag"]
    assert isinstance(lag, Duration)
    assert lag.nanos == 86_400_000_003_000


def test_a_uuid_is_its_text_and_an_enum_member_its_value() -> None:
    row = one({"id": UUID(int=255), "colour": Colour.RED}, records(id="string"))
    assert row["id"] == "00000000-0000-0000-0000-0000000000ff"
    assert row["colour"] == "red"


def test_a_nested_object_is_a_record_and_a_list_of_them_a_list() -> None:
    row = one({"lines": [Pair(1, "x"), Line(2)], "head": Entity(5)})
    lines = row["lines"]
    assert isinstance(lines, FrozenList)
    assert [dict(line) for line in lines] == [{"number": 1, "name": "x"}, {"number": 2}]
    assert isinstance(row["head"], Record)
    assert row["head"]["number"] == 5


def test_a_list_member_keeps_the_records_a_subreport_runs_over() -> None:
    row = one({"lines": (Pair(1, "x"),)}, records(lines="list"))
    assert isinstance(row["lines"], FrozenList)
    assert isinstance(row["lines"][0], Record)


def test_a_float_from_the_host_is_no_string() -> None:
    # As for a subreport's elements: a float the host computed
    # has no one spelling as text.
    with pytest.raises(BuildError) as refused:
        one({"s": 1.5}, records(s="string"))
    assert "member 's': want text, got float" in str(refused.value)


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (b"\x89PNG", "bytes"),
        (datetime(2005, 5, 24, 9, 30).time(), "datetime.time"),
        ({1, 2}, "set"),
    ],
)
def test_a_value_with_no_counterpart_is_refused_naming_its_type(
    value: Any, kind: str
) -> None:
    with pytest.raises(BuildError) as refused:
        records_given([{"a": 1}, {"v": value}], None)
    want = f"record 1: member 'v': want a value a record can hold, got {kind}"
    assert str(refused.value) == want


def test_a_name_that_is_not_text_is_refused() -> None:
    with pytest.raises(BuildError) as refused:
        one({1: "one"})
    assert str(refused.value) == (
        "record 0: a name is int, and a record's names are strings"
    )


def test_a_value_that_contains_itself_is_refused() -> None:
    # An ORM entity with both ends of a relationship loaded is one.
    looped: dict[str, Any] = {"a": 1}
    looped["self"] = looped
    with pytest.raises(BuildError) as refused:
        one({"v": looped})
    assert str(refused.value) == "record 0: member 'v': dict contains itself"


def test_a_set_of_rows_is_refused_for_having_no_order() -> None:
    with pytest.raises(BuildError) as refused:
        records_given({Pair(1, "x")}, None)
    assert str(refused.value) == (
        "want a sequence of records, got set, which has no order"
    )


@pytest.mark.parametrize(
    ("row", "want"),
    [
        (len, "builtin_function_or_method"),
        (Order().total, "method"),
        (ValueError("x"), "ValueError"),
        (Expired(), "Expired, which holds no public attributes"),
    ],
)
def test_what_has_no_fields_is_refused_rather_than_an_empty_record(
    row: Any, want: str
) -> None:
    with pytest.raises(BuildError) as refused:
        records_given([row], None)
    message = str(refused.value)
    assert message.startswith("record 0: want a mapping or an object, got ")
    assert message.endswith(want)


def test_a_method_the_program_forgot_to_call_is_refused() -> None:
    with pytest.raises(BuildError) as refused:
        one({"total": Order().total})
    assert str(refused.value) == (
        "record 0: member 'total': want a value a record can hold, got method"
    )


def test_a_dataclass_gives_what_it_holds_and_loads_nothing() -> None:
    # A relationship that is not loaded is not in the entity's `__dict__`,
    # and reading it by name would load it.
    loaded = Rental(1, "ACADEMY DINOSAUR")
    unloaded = Rental(2, "ACE GOLDFINGER")
    del vars(unloaded)["film"]
    rows = records_given([loaded, unloaded], None)
    assert [dict(row) for row in rows] == [
        {"number": 1, "film": "ACADEMY DINOSAUR"},
        {"number": 2},
    ]


def test_a_field_a_dataclass_has_not_set_is_absent() -> None:
    assert dict(one(Draft(1))) == {"number": 1}


def test_an_object_with_slots_is_a_record_by_what_they_hold() -> None:
    assert dict(one(Slotted(3))) == {"number": 3}
    assert dict(one(Point(1, 2))) == {"x": 1, "y": 2}


def test_an_error_reading_a_row_names_the_record() -> None:
    with pytest.raises(BuildError) as refused:
        records_given([{"number": 1}, Detached()], None)
    want = "reading its fields raised RuntimeError: detached"
    assert str(refused.value) == f"record 1: {want}"
    assert isinstance(refused.value.__cause__, RuntimeError)
    with pytest.raises(BuildError) as nested:
        one({"film": Detached()})
    assert str(nested.value) == f"record 0: member 'film': {want}"


def test_a_datetime_outside_the_years_in_utc_is_refused() -> None:
    # A sentinel that ends a validity range, in a zone west of UTC.
    west = timezone(timedelta(hours=-5))
    with pytest.raises(BuildError) as refused:
        one({"valid_to": datetime(9999, 12, 31, 23, 59, tzinfo=west)})
    assert str(refused.value) == (
        "record 0: member 'valid_to': out of range: 9999-12-31T23:59:00-05:00"
        " is outside the years 1 to 9999 in UTC"
    )


def nested(depth: int) -> list[Any]:
    """Return lists nested `depth` deep."""
    value: list[Any] = []
    for _ in range(depth - 1):
        value = [value]
    return value


def test_a_value_nests_a_hundred_deep_at_most() -> None:
    assert isinstance(one({"v": nested(100)})["v"], FrozenList)
    want = "record 0: member 'v': nested more than 100 deep"
    for depth in (101, 3000):
        with pytest.raises(BuildError) as refused:
            one({"v": nested(depth)})
        assert str(refused.value) == want


def test_a_subclass_or_an_integer_of_another_type_is_the_type_itself() -> None:
    row = one({"i": Cents(5), "f": Ratio(0.5), "s": Label("x"), "n": Count(7)})
    assert [type(row[name]) for name in "ifsn"] == [int, float, str, int]
    assert [row[name] for name in "ifsn"] == [5, 0.5, "x", 7]


def test_a_duration_has_no_member_type_to_go_in() -> None:
    with pytest.raises(BuildError) as refused:
        one({"lag": timedelta(hours=1)}, records(lag="string"))
    want = "no member type holds a duration; leave it undeclared"
    assert str(refused.value) == f"record 0: member 'lag': {want}"


def test_a_lone_surrogate_from_the_host_reads_as_u_fffd() -> None:
    row = one({"s\udfff": ["a\ud800b"]})
    assert list(row["s\ufffd"]) == ["a\ufffdb"]
