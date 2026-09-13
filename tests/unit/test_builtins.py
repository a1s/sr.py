"""The names in scope, the method tables, and the four resolvers."""

from __future__ import annotations

from typing import Any

import pytest

from sr.errors import ExpressionError
from sr.expr.builtins import (
    GLOBALS,
    MATH,
    TIME,
    getattr_,
    getitem_,
    getslice_,
    mod_,
    sr_hash,
    sr_round,
    sr_strftime,
)
from sr.expr.values import (
    Decimal,
    Duration,
    FrozenDict,
    FrozenList,
    Record,
    Set,
    Time,
)

# doc/expressions.md#starlark-builtins, copied out as a list so that a
# name quietly dropped from the table fails here rather than in a report.
DECLARED = """
abs all any bool bytes chr dict dir enumerate fail float getattr hasattr hash
int len list max min ord range repr reversed round set sorted str tuple type zip
""".split()


def test_every_builtin_the_specification_lists_is_in_scope() -> None:
    assert set(DECLARED) <= set(GLOBALS)


def test_the_four_predeclared_names_are_in_scope() -> None:
    assert {"format", "strftime", "decimal", "quantize"} <= set(GLOBALS)


def test_the_two_modules_are_in_scope_and_nothing_else_is() -> None:
    assert set(GLOBALS) - set(DECLARED) == {
        "decimal",
        "format",
        "math",
        "quantize",
        "strftime",
        "time",
    }


def test_print_is_not_in_scope() -> None:
    """A template has nowhere to print to."""
    assert "print" not in GLOBALS


# --------------------------------------------------------------- resolvers


def test_an_attribute_resolves_only_through_the_whitelist() -> None:
    assert getattr_("abc", "upper")() == "ABC"
    with pytest.raises(ExpressionError, match="no attribute"):
        getattr_("abc", "encode")
    with pytest.raises(ExpressionError, match="no attribute"):
        getattr_("abc", "__class__")


def test_the_dropped_string_methods_say_where_they_went() -> None:
    for name in ("elems", "elem_ords"):
        with pytest.raises(ExpressionError, match="codepoints"):
            getattr_("abc", name)


def test_a_string_indexes_and_slices_by_codepoint() -> None:
    assert getitem_("Šķūnis", 0) == "Š"
    assert getitem_("Šķūnis", -1) == "s"
    assert getslice_("Šķūnis", None, 3, None) == "Šķū"
    assert getslice_("Šķūnis", None, None, 2) == "Šūi"


def test_an_index_out_of_range_names_the_length() -> None:
    with pytest.raises(ExpressionError, match="out of range"):
        getitem_([1, 2], 5)


def test_a_record_is_indexed_by_name_and_reached_by_attribute() -> None:
    record = Record({"odd name": 1, "amount": 2})
    assert getitem_(record, "odd name") == 1
    assert getattr_(record, "amount") == 2


def test_a_missing_member_lists_the_ones_there_are() -> None:
    with pytest.raises(ExpressionError, match="amount"):
        getattr_(Record({"amount": 1}), "nope")


def test_percent_on_a_string_is_interpolation_and_on_a_number_is_modulo() -> None:
    assert mod_("%s!", "a") == "a!"
    assert mod_(5, -3) == -1


# ----------------------------------------------------------------- methods


def test_the_string_methods_behave_as_the_language_says() -> None:
    assert getattr_("a,b", "split")(",") == ["a", "b"]
    assert getattr_("a b", "title")() == "A B"
    assert getattr_(", ", "join")(["a", "b"]) == "a, b"
    assert getattr_("abc", "partition")("b") == ("a", "b", "c")
    assert getattr_("Šķūnis", "codepoints")()[:2] == ("Š", "ķ")
    assert getattr_("Šķūnis", "codepoint_ords")()[:2] == (352, 311)


def test_splitlines_splits_on_newline_and_nothing_else() -> None:
    assert getattr_("a\nb\x0cc", "splitlines")() == ["a", "b\x0cc"]
    assert getattr_("a\nb", "splitlines")(True) == ["a\n", "b"]


def test_format_takes_braces_and_refuses_a_format_spec() -> None:
    assert getattr_("{} and {1}", "format")("a", "b") == "a and b"
    assert getattr_("{x!r}", "format")(x="a") == '"a"'
    with pytest.raises(ExpressionError, match="format spec"):
        getattr_("{:>10}", "format")("a")


def test_join_refuses_a_value_that_is_not_a_string() -> None:
    with pytest.raises(ExpressionError, match="join\\(\\) wants strings"):
        getattr_(",", "join")(["a", 1])


def test_a_dict_gives_lists_rather_than_views() -> None:
    entries = {"a": 1, "b": 2}
    assert getattr_(entries, "keys")() == ["a", "b"]
    assert getattr_(entries, "items")() == [("a", 1), ("b", 2)]
    assert getattr_(entries, "get")("c", 0) == 0


def test_a_fresh_list_may_be_changed_and_a_frozen_one_may_not() -> None:
    fresh: list[Any] = [1]
    getattr_(fresh, "append")(2)
    assert fresh == [1, 2]
    with pytest.raises(ExpressionError, match="frozen list"):
        getattr_(FrozenList([1]), "append")(2)


def test_a_frozen_value_keeps_its_mutating_methods_and_they_fail() -> None:
    """The distinction between "you may not" and "there is no such thing"."""
    assert callable(getattr_(FrozenDict({"a": 1}), "update"))
    with pytest.raises(ExpressionError, match="frozen dict"):
        getattr_(FrozenDict({"a": 1}), "update")({"b": 2})
    with pytest.raises(ExpressionError, match="frozen set"):
        getattr_(Set([1], frozen=True), "add")(2)


def test_the_query_methods_of_a_frozen_value_still_work() -> None:
    assert getattr_(FrozenList([1, 2]), "index")(2) == 1
    assert getattr_(FrozenDict({"a": 1}), "get")("a") == 1


def test_set_methods_answer_with_sets_in_order() -> None:
    left = Set([3, 1, 2])
    assert list(getattr_(left, "union")(Set([4]))) == [3, 1, 2, 4]
    assert list(getattr_(left, "intersection")(Set([2, 3]))) == [3, 2]
    assert getattr_(left, "issubset")(Set([1, 2, 3, 4])) is True
    assert getattr_(left, "issuperset")(Set([1])) is True


def test_a_time_reads_its_attributes_and_a_duration_its_floats() -> None:
    moment = Time(0)
    assert getattr_(moment, "year") == 1970
    assert getattr_(moment, "unix_nano") == 0
    assert getattr_(Duration(3600 * 1_000_000_000), "hours") == 1.0


def test_bytes_keeps_its_own_elems() -> None:
    """The byte pair came off the string type; bytes are still bytes."""
    assert getattr_(b"ab", "elems")() == (97, 98)


# ------------------------------------------------------------- the modules


def test_math_has_every_member_the_specification_names() -> None:
    named = """
    ceil floor round mod pow sqrt fabs exp log hypot copysign remainder
    degrees radians gamma pi e
    """.split()
    assert set(named) <= set(MATH.members)
    assert {"sin", "cos", "tan", "asin", "sinh", "atan2"} <= set(MATH.members)


def test_ceil_and_floor_answer_with_ints() -> None:
    assert MATH.members["ceil"](1.2) == 2
    assert MATH.members["floor"](1.8) == 1


def test_round_takes_one_argument_and_goes_away_from_zero() -> None:
    """The probe in tests/differential/probes/expressions/round.kdl."""
    assert sr_round(1.5) == 2
    assert sr_round(2.5) == 3
    assert sr_round(-2.5) == -3


def test_math_round_answers_with_a_float_where_round_answers_with_an_int() -> None:
    """The host module's function, whose result the reference fixes."""
    assert MATH.members["round"](2.5) == 3.0
    assert isinstance(MATH.members["round"](2.5), float)
    assert isinstance(sr_round(2.5), int)


def test_round_of_a_decimal_stays_exact() -> None:
    assert sr_round(Decimal("2.5")) == 3
    assert sr_round(Decimal("-2.5")) == -3


def test_a_math_function_refuses_a_decimal_rather_than_converting_it() -> None:
    with pytest.raises(ExpressionError, match="float\\(d\\)"):
        MATH.members["floor"](Decimal("1.5"))


def test_the_time_module_has_its_constructors_and_constants() -> None:
    assert {"time", "parse_time", "from_timestamp", "parse_duration"} <= set(
        TIME.members
    )
    assert TIME.members["hour"] == Duration(3600 * 1_000_000_000)
    assert TIME.members["nanosecond"] == Duration(1)


def test_there_is_no_clock_in_scope() -> None:
    """doc/expressions.md#determinism: `time.now` is not available."""
    assert "now" not in TIME.members


def test_a_time_is_built_from_the_fields_it_is_given() -> None:
    made = TIME.members["time"](year=2005, month=5, day=24, hour=22, minute=53)
    assert made.format("2006-01-02T15:04:05Z07:00") == "2005-05-24T22:53:00Z"


def test_a_field_left_out_is_zero_and_carries() -> None:
    """`time.time(year=2005)` is month 0 and day 0, which is 30 November 2004."""
    made = TIME.members["time"](year=2005)
    assert made.format("2006-01-02 15:04:05") == "2004-11-30 00:00:00"
    assert TIME.members["time"](year=2005, month=3, day=0).format("01-02") == "02-28"


def test_a_time_before_year_one_is_refused_rather_than_built() -> None:
    """What `time.time()` with nothing named would be."""
    with pytest.raises(ExpressionError, match=r"time\(\)"):
        TIME.members["time"]()


def test_a_time_is_built_in_the_location_it_is_given() -> None:
    made = TIME.members["time"](year=2005, month=5, day=25, location="Europe/Riga")
    assert made.format("2006-01-02T15:04:05Z07:00") == "2005-05-25T00:00:00+03:00"
    assert made.unix == 1_116_968_400


def test_a_timestamp_and_a_duration_are_read_from_their_own_spellings() -> None:
    assert TIME.members["from_timestamp"](0).unix_nano == 0
    assert TIME.members["parse_duration"]("1h30m") == Duration(90 * 60 * 1_000_000_000)
    assert TIME.members["parse_duration"]("-500ms") == Duration(-500_000_000)
    assert TIME.members["parse_duration"]("1.5µs") == Duration(1_500)


def test_a_duration_that_is_not_one_is_refused() -> None:
    with pytest.raises(ExpressionError, match="not a duration"):
        TIME.members["parse_duration"]("half an hour")


def test_a_timezone_is_valid_or_it_is_not() -> None:
    assert TIME.members["is_valid_timezone"]("Europe/Riga") is True
    assert TIME.members["is_valid_timezone"]("UTC") is True
    assert TIME.members["is_valid_timezone"]("Mars/Olympus") is False


# ---------------------------------------------------------------- strftime


def test_strftime_writes_every_directive() -> None:
    moment = TIME.members["parse_time"]("2005-05-24T22:53:30Z")
    assert sr_strftime(moment, "%d.%m.%Y") == "24.05.2005"
    assert sr_strftime(moment, "%Y-%m-%dT%H:%M:%S") == "2005-05-24T22:53:30"
    assert sr_strftime(moment, "%A %a %B %b") == "Tuesday Tue May May"
    assert sr_strftime(moment, "%I%p %j %y") == "10PM 144 05"
    assert sr_strftime(moment, "%Z %z") == "UTC +0000"
    assert sr_strftime(moment, "100%%") == "100%"


def test_strftime_is_locale_independent() -> None:
    """English names whatever the machine's locale says."""
    riga = TIME.members["parse_time"]("2005-05-24T22:53:30Z").in_location("Europe/Riga")
    assert sr_strftime(riga, "%B %A") == "May Wednesday"


def test_an_unknown_directive_is_refused() -> None:
    moment = Time(0)
    with pytest.raises(ExpressionError, match="unknown directive"):
        sr_strftime(moment, "%Q")


# --------------------------------------------------------------------- odds


def test_hash_is_stable_across_runs_and_counts_codepoints() -> None:
    """Python's string hash is seeded per process; a report cannot be."""
    assert sr_hash("") == 0
    assert sr_hash("a") == 97
    assert sr_hash("abc") == 96354
    assert sr_hash("Š") == 352
    assert sr_hash("abcdefghijklmnop") == -2093879032


def test_a_bytes_value_is_hashed_as_bytes() -> None:
    """A different function from the string one, which is the reference's."""
    assert sr_hash(b"a") == 0xE40C292C


def test_hash_takes_strings_and_bytes_and_nothing_else() -> None:
    with pytest.raises(ExpressionError, match="wants a string or bytes"):
        sr_hash(1)
