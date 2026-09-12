"""Dimensions and the rounding that follows them.

The numbers here come from three places: doc/layout.md's own worked example
for the rounding, the `rounding/` probes for the tie-break, and the
`values/dimensions` probe for the unit table and the grammar.  Where a case
is also a probe, `test_values_against_probes.py` holds the two to the same
answer; these are the cases a probe cannot carry, because a template that
writes one does not build.

"""

from __future__ import annotations

import math

import pytest

from sr.errors import BadValue
from sr.units import (
    POINTS_PER_UNIT,
    TOLERANCE,
    fits,
    parse_dimension,
    round_half_away,
    round_points,
)


def test_halves_go_away_from_zero() -> None:
    assert round_half_away(0.5) == 1
    assert round_half_away(-0.5) == -1
    assert round_half_away(2.5) == 3
    assert round_half_away(-2.5) == -3


def test_what_is_not_a_half_is_unaffected() -> None:
    assert round_half_away(0.4) == 0
    assert round_half_away(-0.4) == 0
    assert round_half_away(1.6) == 2


def test_a_value_just_below_a_half_still_rounds_down() -> None:
    """The case `floor(value + 0.5)` gets wrong.

    Adding a half to 0.49999999999999994 reaches 1.0 on its own,
    so an implementation written that way answers 1.  Everything
    downstream of this is a page break, so it is worth a test
    of its own.

    """
    assert round_half_away(0.49999999999999994) == 0


def test_the_scaling_comes_before_the_rounding() -> None:
    """doc/layout.md#coordinates-and-rounding, worked out in full.

    0.1235 is held as 0.12349999999999999866..., which is below the half
    and would round down.  Scaled by a thousand first it is exactly 123.5,
    which rounds up.

    """
    assert round_points(0.1235) == 0.124


def test_a_coordinate_on_the_half_goes_away_from_zero() -> None:
    assert round_points(0.0005) == 0.001
    assert round_points(-0.0005) == -0.001
    assert round_points(0.0045) == 0.005
    assert round_points(10.0005) == 10.001


def test_rounding_never_produces_a_negative_zero() -> None:
    """-0 and 0 compare equal, so only one of them may reach a printout."""
    assert not math.copysign(1, round_points(-0.0004)) < 0


def test_a_band_that_matches_the_space_exactly_fits() -> None:
    assert fits(100.0, 100.0)
    assert fits(100.001, 100.0)
    assert not fits(100.002, 100.0)


def test_the_tolerance_is_an_addition_and_not_a_count_of_thousandths() -> None:
    """The `rounding/tolerance-*` probes, as arithmetic.

    One thousandth over fits at one magnitude and not at another, because
    binary64 addition does not always reach the next three-decimal value:
    20.003 + 0.001 is 20.004 exactly, and 1.001 + 0.001 is a hair under
    1.002.  Comparing thousandths as integers would admit both, and would
    move a page break on some seven percent of exact fits.

    """
    assert TOLERANCE == 0.001
    assert fits(20.004, 20.003)
    assert not fits(1.002, 1.001)
    assert not fits(20.005, 20.003)


def test_a_bare_number_is_points() -> None:
    assert parse_dimension(35) == 35.0
    assert parse_dimension(35.5) == 35.5


def test_each_suffix_converts_as_the_table_says() -> None:
    assert parse_dimension("35pt") == 35.0
    assert parse_dimension("1in") == 72.0
    assert parse_dimension("1000mil") == 72.0
    assert parse_dimension("2.54cm") == 72.0
    assert parse_dimension("12mm") == 34.016
    assert set(POINTS_PER_UNIT) == {"pt", "mil", "mm", "cm", "in"}


def test_no_suffix_is_points_too() -> None:
    assert parse_dimension("35") == parse_dimension("35pt") == 35.0


def test_whitespace_is_not_part_of_the_value() -> None:
    assert parse_dimension("12 mm") == 34.016
    assert parse_dimension(" 12mm ") == 34.016
    assert parse_dimension("12\tmm") == 34.016


def test_the_result_is_rounded_as_it_is_parsed() -> None:
    assert parse_dimension("0.0005") == 0.001
    assert parse_dimension("-5mm") == -14.173


@pytest.mark.parametrize(
    ("text", "points"),
    [
        ("1e2", 100.0),
        ("1.5e+1", 15.0),
        ("1.5E1", 15.0),
        ("1_0", 10.0),
        ("+5", 5.0),
        (".5in", 36.0),
        ("12.mm", 34.016),
        ("1e2mm", 283.465),
    ],
)
def test_the_grammar_takes_these(text: str, points: float) -> None:
    assert parse_dimension(text) == points


@pytest.mark.parametrize(
    "text",
    [
        "1MM",  # the suffix is lower case
        "1Mm",
        "12px",  # not a unit
        "12mmm",
        "mm",  # no number
        "1 2",  # two numbers
        "1,5",
        "1/2",
        "0x10",  # Go reads hexadecimal floats; the grammar does not
        "0x1p-2",
        "_1",  # an underscore has to sit between digits
        "1_",
        "1__2",
        "inf",  # Python reads these; the grammar does not
        "nan",
        "Infinity",
        "١٢",  # non-ASCII digits, which Python's float() takes
        "1e400",  # a number binary64 cannot hold
    ],
)
def test_the_grammar_refuses_these(text: str) -> None:
    with pytest.raises(BadValue, match="bad dimension"):
        parse_dimension(text)


def test_an_empty_dimension_says_so_rather_than_being_zero() -> None:
    with pytest.raises(BadValue, match="empty dimension"):
        parse_dimension("   ")


def test_a_boolean_is_not_a_dimension() -> None:
    with pytest.raises(BadValue, match="want a dimension, got boolean"):
        parse_dimension(True)


def test_a_refusal_quotes_the_value_as_it_was_written() -> None:
    with pytest.raises(BadValue) as refused:
        parse_dimension(" 1MM ")
    assert str(refused.value) == 'bad dimension " 1MM "'
