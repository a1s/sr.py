"""The value model: decimals, times, durations, records, sets, and text."""

from __future__ import annotations

import decimal as pydecimal
import math
from typing import Any

import pytest

from sr.errors import ExpressionError
from sr.expr.values import (
    Decimal,
    Duration,
    FrozenDict,
    FrozenList,
    Namespace,
    Record,
    Set,
    Time,
    find_location,
    format_float,
    go_shortest,
    quantize,
    starlark_repr,
    starlark_str,
    truthy,
    type_name,
)

UTC_MIDNIGHT = 1_116_979_200 * 1_000_000_000  # 2005-05-25T00:00:00Z


# --------------------------------------------------------------- decimals


def test_a_decimal_comes_from_a_string_or_an_int() -> None:
    assert str(Decimal("19.99")) == "19.99"
    assert str(Decimal(5)) == "5"


def test_a_decimal_will_not_come_from_a_float() -> None:
    with pytest.raises(ExpressionError, match="decimal\\(str"):
        Decimal(0.1)


def test_addition_keeps_the_scale_exactness_requires() -> None:
    assert str(Decimal("0.10") + Decimal("0.20")) == "0.30"


def test_multiplication_gives_the_sum_of_the_scales() -> None:
    assert str(Decimal("0.10") * Decimal("0.20")) == "0.0200"


def test_arithmetic_is_exact_beyond_the_default_precision() -> None:
    """Thirty digits, which the default 28-digit context would round."""
    big = Decimal("123456789012345678901234567890")
    assert str(big + 1) == "123456789012345678901234567891"


def test_division_quantizes_to_six_places() -> None:
    assert str(Decimal("1") / Decimal("3")) == "0.333333"
    assert str(Decimal("2") / Decimal("3")) == "0.666667"


def test_division_rounds_halves_away_from_zero() -> None:
    """A quotient that lands exactly on a half at the seventh place."""
    assert str(Decimal("1") / Decimal("16")) == "0.062500"
    assert str(Decimal("0.0000005") / Decimal("1")) == "0.000001"
    assert str(Decimal("-0.0000005") / Decimal("1")) == "-0.000001"


def test_dividing_by_zero_is_an_error() -> None:
    with pytest.raises(ExpressionError, match="division by zero"):
        Decimal("1") / Decimal("0")


def test_a_decimal_compares_with_an_int() -> None:
    assert Decimal("1") > 0
    assert Decimal("1") == 1
    assert not Decimal("1") != 1
    assert Decimal("0.5") < 1


def test_a_decimal_and_the_equal_int_hash_the_same() -> None:
    """The part of the rule easiest to leave out, and the one a dict needs."""
    assert hash(Decimal("1")) == hash(1)
    keyed: dict[Any, str] = {Decimal("1"): "d"}
    assert keyed.get(1) == "d"


def test_a_decimal_is_not_equal_to_a_float_and_does_not_refuse_it() -> None:
    assert not (Decimal("1") == 1.0)
    assert Decimal("1") != 1.0


def test_a_decimal_refuses_ordered_comparison_with_a_float() -> None:
    with pytest.raises(ExpressionError, match="do not mix"):
        _ = Decimal("1") < 1.0


def test_a_decimal_refuses_arithmetic_with_a_float() -> None:
    with pytest.raises(ExpressionError, match="decimal \\+ float"):
        _ = Decimal("1") + 1.0


def test_sorting_mixes_decimals_and_ints() -> None:
    mixed: list[Any] = [Decimal("2"), 1, Decimal("1.5")]
    assert [str(one) for one in sorted(mixed)] == [
        "1",
        "1.5",
        "2",
    ]


def test_quantize_rounds_halves_away_from_zero() -> None:
    assert str(quantize(Decimal("2.345"), 2)) == "2.35"
    assert str(quantize(Decimal("-2.345"), 2)) == "-2.35"
    assert str(quantize(Decimal("2.5"), 0)) == "3"


def test_int_of_a_decimal_truncates_toward_zero() -> None:
    assert int(Decimal("1.9")) == 1
    assert int(Decimal("-1.9")) == -1


def test_str_of_a_decimal_has_no_exponent() -> None:
    assert str(Decimal(pydecimal.Decimal("1E+3"))) == "1000"
    assert str(Decimal("0.000001")) == "0.000001"


# ------------------------------------------------------ times & durations


def test_a_time_reads_its_fields_in_its_location() -> None:
    moment = Time(UTC_MIDNIGHT)
    assert (moment.year, moment.month, moment.day) == (2005, 5, 25)
    assert (moment.hour, moment.minute, moment.second) == (0, 0, 0)
    assert moment.unix == 1_116_979_200
    assert moment.unix_nano == UTC_MIDNIGHT


def test_a_time_keeps_nanoseconds_a_datetime_cannot_hold() -> None:
    moment = Time(UTC_MIDNIGHT + 123_456_789)
    assert moment.nanosecond == 123_456_789
    assert moment.format("15:04:05.000000000") == "00:00:00.123456789"


def test_in_location_changes_the_reading_and_not_the_instant() -> None:
    moment = Time(UTC_MIDNIGHT).in_location("Europe/Riga")
    assert moment.unix_nano == UTC_MIDNIGHT
    assert moment.hour == 3
    assert moment.format("15:04 -0700") == "03:00 +0300"


def test_the_zero_time_is_false_and_every_other_time_is_true() -> None:
    zero = Time(-62135596800 * 1_000_000_000)
    assert zero.year == 1
    assert not truthy(zero)
    assert truthy(Time(0))


def test_subtracting_two_times_gives_a_duration() -> None:
    later = Time(UTC_MIDNIGHT + 2 * 86400 * 1_000_000_000)
    between = later - Time(UTC_MIDNIGHT)
    assert isinstance(between, Duration)
    assert between.hours == 48.0


def test_adding_a_duration_to_a_time_gives_a_time() -> None:
    hour = Duration(3600 * 1_000_000_000)
    assert (Time(UTC_MIDNIGHT) + 3 * hour).hour == 3


def test_every_duration_accessor_is_a_float() -> None:
    length = Duration(90 * 60 * 1_000_000_000)
    assert length.hours == 1.5
    assert length.minutes == 90.0
    assert isinstance(length.nanoseconds, float)


def test_a_duration_prints_the_way_go_prints_one() -> None:
    assert str(Duration(0)) == "0s"
    assert str(Duration(90 * 60 * 1_000_000_000)) == "1h30m0s"
    assert str(Duration(1_500_000_000)) == "1.5s"
    assert str(Duration(1_500)) == "1.5µs"
    assert str(Duration(-2_000_000_000)) == "-2s"


def test_a_zero_duration_is_false() -> None:
    assert not truthy(Duration(0))
    assert truthy(Duration(1))


def test_an_unknown_timezone_is_refused() -> None:
    with pytest.raises(ExpressionError, match="unknown timezone"):
        find_location("Mars/Olympus")


# ------------------------------------------------- records and containers


def test_a_record_reads_its_members_and_is_true_when_empty() -> None:
    record = Record({"amount": Decimal("1.00")})
    assert record["amount"] == Decimal("1.00")
    assert truthy(Record({}))


def test_a_frozen_list_compares_with_a_plain_one() -> None:
    frozen = FrozenList([1, 2])
    assert frozen == [1, 2]
    joined = frozen + [3]  # noqa: RUF005 -- concatenation is what is tested
    assert list(joined) == [1, 2, 3]


def test_a_frozen_dict_compares_with_a_plain_one() -> None:
    assert FrozenDict({"a": 1}) == {"a": 1}


def test_a_set_iterates_in_first_seen_order() -> None:
    assert list(Set(["b", "a", "b", "c"])) == ["b", "a", "c"]


def test_set_operators_keep_the_left_hand_order() -> None:
    left, right = Set([3, 1, 2]), Set([2, 3])
    assert list(left | right) == [3, 1, 2]
    assert list(left & right) == [3, 2]
    assert list(left - right) == [1]


def test_an_empty_container_is_false_and_a_full_one_is_true() -> None:
    assert not truthy(Set())
    assert not truthy(FrozenList())
    assert not truthy(FrozenDict())
    assert truthy(Set([0]))


# ------------------------------------------------------------------ text


def test_the_truth_table_holds_for_every_row() -> None:
    """doc/expressions.md#truth-values, one assertion per row."""
    assert not truthy(None)
    assert truthy(True) and not truthy(False)
    assert truthy(1) and not truthy(0)
    assert truthy(0.5) and not truthy(0.0)
    assert truthy(Decimal("0.1")) and not truthy(Decimal("0.0"))
    assert truthy("a") and not truthy("")
    assert truthy([1]) and not truthy([])
    assert truthy({"a": 1}) and not truthy({})


def test_type_names_are_the_languages_rather_than_pythons() -> None:
    assert type_name("a") == "string"
    assert type_name(None) == "NoneType"
    assert type_name(Decimal("1")) == "decimal"
    assert type_name(Time(0)) == "time.time"
    assert type_name(Duration(1)) == "time.duration"
    assert type_name(Record({})) == "record"
    assert type_name(Set()) == "set"
    assert type_name(FrozenList()) == "list"
    assert type_name(Namespace("math", {})) == "module"


def test_str_shows_a_string_bare_and_repr_quotes_it() -> None:
    assert starlark_str("a") == "a"
    assert starlark_repr("a") == '"a"'
    assert starlark_repr('a"b\nc') == '"a\\"b\\nc"'


def test_repr_leaves_a_printable_non_ascii_character_alone() -> None:
    """What the reference's own printout carries for `%r` on a letter."""
    assert starlark_repr("Š") == '"Š"'


def test_containers_print_the_way_the_language_prints_them() -> None:
    assert starlark_str([1, "a"]) == '[1, "a"]'
    assert starlark_str({"a": 1}) == '{"a": 1}'
    assert starlark_str(Set(["b", "a"])) == 'set(["b", "a"])'
    assert starlark_str((1,)) == "(1,)"
    assert starlark_str((1, 2)) == "(1, 2)"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (3.0, "3.0"),
        (1.0 / 3, "0.3333333333333333"),
        (0.0001, "0.0001"),
        (0.00001, "1e-05"),
        (123456.0, "123456.0"),
        (1234567.0, "1.234567e+06"),
        (1e7, "1e+07"),
        (1e100, "1e+100"),
        (-0.0, "-0.0"),
        (0.0, "0.0"),
    ],
)
def test_a_float_is_written_the_way_go_writes_one(value: float, shown: str) -> None:
    """Shortest that round-trips, turning over at 1e6 rather than at 1e16."""
    assert format_float(value) == shown
    assert float(shown) == value


def test_every_float_round_trips_through_its_shortest_form() -> None:
    for value in (1.1, 2.5e-10, 6.02214076e23, math.pi, 1e15, 9.999999e5):
        assert float(go_shortest(value)) == value


def test_the_non_finite_floats_are_named_rather_than_spelled() -> None:
    assert format_float(math.inf) == "+inf"
    assert format_float(-math.inf) == "-inf"
    assert format_float(math.nan) == "nan"
