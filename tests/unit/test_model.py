"""The value types and the tables of sr.template.model."""

from __future__ import annotations

import pytest

from sr.errors import BadValue
from sr.expr import Decimal, FrozenList, Record, Time
from sr.template.model import (
    PAGE_SIZES,
    VALUE_TYPES,
    Span,
    freeze,
    page_size,
    parse_text,
    span,
)

# Every page size in points, portrait, as doc/template.md#enumerations
# now states it.  Read off the reference binary one name at a time
# and written here rather than recomputed, so that an edit to the
# conversion is a failure rather than a new expectation.
POINTS = {
    "A1": (1683.78, 2383.937),
    "A2": (1190.551, 1683.78),
    "A3": (841.89, 1190.551),
    "A4": (595.276, 841.89),
    "A5": (419.528, 595.276),
    "A6": (297.638, 419.528),
    "B3": (1000.63, 1417.323),
    "B4": (708.661, 1000.63),
    "B5": (498.898, 708.661),
    "B6": (354.331, 498.898),
    "Letter": (612, 792),
    "Legal": (612, 1008),
    "Ledger": (792, 1224),
    "Executive": (522, 756),
    "Statement": (396, 612),
    "Quatro": (576, 720),
    "Royal": (1440, 1800),
    "BusinessCard": (153, 242.64),
    "EnvelopeC3": (918.425, 1298.268),
    "EnvelopeC4": (649.134, 918.425),
    "EnvelopeC5": (459.213, 649.134),
    "EnvelopeC6": (323.15, 459.213),
    "EnvelopeDL": (311.811, 623.622),
    "EnvelopeB4": (708.661, 1000.63),
    "EnvelopeB5": (498.898, 708.661),
    "Envelope#10": (297, 684),
    "EnvelopeA2": (315, 414),
    "EnvelopeA6": (342, 468),
    "EnvelopeA7": (378, 522),
}


@pytest.mark.parametrize("name", sorted(POINTS))
def test_a_page_size_is_what_the_specification_says(name: str) -> None:
    assert page_size(name) == POINTS[name]


def test_every_page_size_name_has_a_size() -> None:
    assert set(PAGE_SIZES) == set(POINTS)


def test_the_two_b_envelopes_are_the_iso_b_sizes() -> None:
    assert page_size("EnvelopeB4") == page_size("B4")
    assert page_size("EnvelopeB5") == page_size("B5")


# -- two of three -----------------------------------------------------


def test_an_axis_given_nothing_spans_the_container() -> None:
    assert span(None, None, None) == Span(0.0, 0.0, None, None)


def test_an_axis_given_a_start_reaches_the_far_edge() -> None:
    assert span(10.0, None, None) == Span(10.0, 0.0, None, None)


def test_an_axis_given_a_size_starts_at_the_near_edge() -> None:
    assert span(None, None, 30.0) == Span(0.0, None, 30.0, None)


def test_an_axis_given_an_end_starts_at_the_near_edge() -> None:
    assert span(None, 5.0, None) == Span(0.0, 5.0, None, None)


def test_an_axis_given_two_keeps_them() -> None:
    assert span(10.0, 5.0, None) == Span(10.0, 5.0, None, None)
    assert span(10.0, None, 30.0) == Span(10.0, None, 30.0, None)
    assert span(None, 5.0, 30.0) == Span(None, 5.0, 30.0, None)


def test_a_clamp_is_not_part_of_the_count() -> None:
    filled = span(None, None, None, 50.0)
    assert filled.limit == 50.0
    assert filled.given == 2


def test_a_zero_start_counts_as_given() -> None:
    assert span(0.0, None, None) == Span(0.0, 0.0, None, None)


# -- parameter values as text -----------------------------------------


def test_a_string_parameter_is_taken_verbatim() -> None:
    assert parse_text("string", " 12 ") == " 12 "


def test_an_int_parameter_is_arbitrary_precision() -> None:
    assert parse_text("int", "-1" + "0" * 40) == -(10**40)


@pytest.mark.parametrize("text", ["1.0", "one", "", "1_0", "٤", "0x10"])
def test_a_bad_int_parameter_is_refused(text: str) -> None:
    with pytest.raises(BadValue):
        parse_text("int", text)


def test_a_decimal_parameter_keeps_its_scale() -> None:
    value = parse_text("decimal", "1.50")
    assert isinstance(value, Decimal)
    assert str(value) == "1.50"


@pytest.mark.parametrize("text", ["1e3", "one", "1.2.3"])
def test_a_bad_decimal_parameter_is_refused(text: str) -> None:
    with pytest.raises(BadValue):
        parse_text("decimal", text)


def test_a_float_parameter_takes_an_exponent_and_the_two_specials() -> None:
    assert parse_text("float", "1e3") == 1000.0
    assert parse_text("float", "inf") == float("inf")
    assert parse_text("float", "-nan") != parse_text("float", "-nan")


@pytest.mark.parametrize("text", ["--inf", "+-nan", "-+1.5", "", "inf3"])
def test_a_bad_float_parameter_is_refused_as_a_bad_value(text: str) -> None:
    # One sign, and only the names themselves.
    # Reaching the host reader with more than that raises a `ValueError`,
    # which is not what a caller of `parse_text` catches.
    with pytest.raises(BadValue):
        parse_text("float", text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("false", False), ("0", False)],
)
def test_a_bool_parameter_is_case_insensitive(text: str, expected: bool) -> None:
    assert parse_text("bool", text) is expected


def test_a_bad_bool_parameter_is_refused() -> None:
    with pytest.raises(BadValue):
        parse_text("bool", "yes")


def test_a_date_parameter_has_no_time_of_day() -> None:
    value = parse_text("date", "2005-01-01")
    assert isinstance(value, Time)
    assert (value.year, value.month, value.day) == (2005, 1, 1)
    assert (value.hour, value.minute, value.second) == (0, 0, 0)


def test_a_datetime_parameter_reads_rfc_3339() -> None:
    value = parse_text("datetime", "2005-05-24T22:53:30Z")
    assert (value.hour, value.minute, value.second) == (22, 53, 30)


def test_a_date_parameter_reads_the_layout_it_is_given() -> None:
    value = parse_text("date", "31.12.2005", "02.01.2006")
    assert (value.year, value.month, value.day) == (2005, 12, 31)


def test_a_bad_date_parameter_names_its_type() -> None:
    with pytest.raises(BadValue, match="not a date"):
        parse_text("date", "yesterday")


def test_an_object_parameter_becomes_a_record() -> None:
    value = parse_text("object", '{"a": 1, "b": {"c": 2}}')
    assert isinstance(value, Record)
    assert value["b"]["c"] == 2


def test_a_list_parameter_becomes_a_frozen_list() -> None:
    value = parse_text("list", "[1, 2]")
    assert isinstance(value, FrozenList)
    assert list(value) == [1, 2]


@pytest.mark.parametrize(("kind", "text"), [("object", "[]"), ("list", "{}")])
def test_json_of_the_wrong_shape_is_refused(kind: str, text: str) -> None:
    with pytest.raises(BadValue):
        parse_text(kind, text)


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(BadValue, match="unknown type"):
        parse_text("money", "1")


def test_every_declared_type_reads_something() -> None:
    samples = {
        "string": "x",
        "int": "1",
        "decimal": "1.0",
        "float": "1",
        "bool": "true",
        "datetime": "2005-05-24T22:53:30Z",
        "date": "2005-01-01",
        "object": "{}",
        "list": "[]",
    }
    assert set(samples) == set(VALUE_TYPES)
    for kind, text in samples.items():
        parse_text(kind, text)


def test_freezing_leaves_a_scalar_alone() -> None:
    assert freeze(1) == 1
    assert freeze(None) is None
