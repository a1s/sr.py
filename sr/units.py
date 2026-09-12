"""Lengths: what a dimension says, and the rounding that follows it.

doc/template.md#dimension is the grammar and the unit table;
doc/layout.md#coordinates-and-rounding is the rounding and the tolerance.
Both are normative, and the second is the reason this module exists at all
rather than being three lines inside the template loader: every computed
coordinate and extent in the engine passes through :func:`round_points`
immediately, because rounding only at output time moves page breaks.

Three details are easy to get wrong and each is pinned here.

* **The scaling comes first.**  ``round(value * 1000) / 1000`` in binary64,
  not rounding of the exact decimal the template spelled.  ``0.1235`` is
  held as 0.12349999999999999866…, which would round down; times 1000 it
  is exactly 123.5, which rounds up.  0.124 is the answer.
* **Halves go away from zero**, which is neither Python's ``round``
  nor what ``math.floor(value + 0.5)`` computes: the latter answers 1
  for 0.49999999999999994, because the addition alone reaches 1.0.
  :func:`round_half_away` splits the value with ``math.modf`` instead,
  which is exact.
* **A number is what the grammar says**, not what the host's float parser
  happens to take.  Python's reads ``inf``, ``nan`` and non-ASCII digits;
  Go's reads hexadecimal floats.  Neither set belongs in a template.

"""

from __future__ import annotations

import math
import re

from sr.errors import BadValue

__all__ = [
    "NUMBER",
    "POINTS_PER_UNIT",
    "TOLERANCE",
    "as_written",
    "fits",
    "parse_dimension",
    "parse_number",
    "round_half_away",
    "round_points",
]

# Points per unit, one factor each, per doc/template.md#dimension.
# The conversion multiplies by the factor rather than dividing in turn
# by the unit's denominator; the two associations differ in the last bit,
# and no value within a page of either has been found where that survives
# the rounding below.
POINTS_PER_UNIT = {
    "pt": 1.0,
    "mil": 0.072,
    "mm": 72 / 25.4,
    "cm": 72 / 2.54,
    "in": 72.0,
}

# Longest first, so that a string ending in a longer suffix is not cut
# by a shorter one.  None of the five is a suffix of another today;
# the sort is what keeps that from being a fact to remember.
SUFFIXES = tuple(sorted(POINTS_PER_UNIT, key=len, reverse=True))

# Three decimal places, as a scale rather than a digit count,
# since that is how the rounding is written down.
SCALE = 1000.0

# Comparisons against frame boundaries carry this, so a band whose
# height matches the space exactly fits rather than ejecting.
# Both sides are already rounded, so it absorbs one unit
# in the last place rather than an accumulated error.
TOLERANCE = 0.001

# A run of ASCII digits, which single underscores may break up:
# `1_000` is a thousand, and `_1`, `1_` and `1__0` are not numbers.
DIGITS = "[0-9](?:_?[0-9])*"

# The number a dimension is written with, and the one a colour component
# takes.  Deliberately not `float()`: see the module docstring.
NUMBER = re.compile(
    rf"[+-]?(?:{DIGITS}(?:\.(?:{DIGITS})?)?|\.{DIGITS})(?:[eE][+-]?{DIGITS})?\Z"
)


def round_half_away(value: float) -> float:
    """Return ``value`` rounded to a whole number, halves away from zero.

    The split is exact, so the answer is a rounding of the value itself
    and not of a value plus a half that has already been rounded.

    """
    fraction, whole = math.modf(value)
    if abs(fraction) >= 0.5:
        whole += math.copysign(1.0, value)
    return whole


def round_points(value: float) -> float:
    """Return ``value`` rounded to 3 decimal places, in points.

    This is the rounding doc/layout.md calls normative, and every computed
    coordinate and extent goes through it as soon as it is computed.
    A negative zero comes back as zero: the two compare equal everywhere
    in the engine, and only one of them may reach a printout.

    """
    return round_half_away(value * SCALE) / SCALE + 0.0


def fits(extent: float, limit: float) -> bool:
    """Report whether ``extent`` is within ``limit``, to the tolerance.

    The one comparison doc/layout.md gives a tolerance for.  There
    is no companion that asks whether two lengths are *equal* to it,
    because at three decimals that is not a question binary64 answers
    consistently: 34.017 minus 34.016 is not 0.001, and 34.016 plus 0.001
    is not 34.017. A comparison written as this one is, against a limit,
    has neither problem.

    Args:
        extent: A height or width, already rounded.
        limit: The space available for it, already rounded.

    """
    return extent <= limit + TOLERANCE


def parse_number(text: str) -> float:
    """Return the value of a number written as the grammar spells it.

    A number too large for binary64 comes back as an infinity rather than
    as a refusal.  Whether that is an error depends on what the number is
    for, and both callers already have to ask: a dimension is checked for
    finiteness once, on the points it resolves to, and a colour component
    has a range that an infinity is outside of.  Refusing here as well
    would mean two answers to one question.

    Args:
        text: The number alone, with no unit and no surrounding whitespace.

    Raises:
        BadValue: The text is not a number.

    """
    if not NUMBER.match(text):
        raise BadValue(f"not a number: {text!r}")
    return float(text.replace("_", ""))


def as_written(value: str | int | float) -> str:
    """Return a value as a diagnostic should show it.

    A string keeps its quotes and a number does not, so a diagnostic reads
    back the way the property was written.  A non-finite number is named by
    the KDL keyword that is the only way to write one, since `width=#inf`
    quoted back as Python's ``inf`` would send a reader looking for a word
    that is not in their template.

    The spelling comes from the value rather than from the source text,
    so it is what the template *could* have written rather than what it did:
    `1e308` comes back as ``1e+308``.

    Args:
        value: The property's value, as the KDL reader produced it.

    """
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "#nan"
        return "#inf" if value > 0 else "#-inf"
    return str(value)


def parse_dimension(value: str | int | float) -> float:
    """Return a dimension in points, rounded to 3 decimal places.

    A KDL integer or number is points as it stands.
    A string is optional whitespace, a number, optional whitespace,
    and one of the five unit suffixes in lower case -- or no suffix,
    which means points.

    The result is checked rather than the input, which is what it takes to
    cover both ways a coordinate reaches infinity.  KDL v2 has `#inf`,
    `#-inf` and `#nan` as number keywords, so a template can write one
    directly; and a number this side of the limit does not stay there,
    since :func:`round_points` multiplies by a thousand before it rounds
    and ``1e308`` pt is an infinity three lines later.

    Args:
        value: The property's value, as the KDL reader produced it.

    Raises:
        BadValue: The value is not a dimension, or is not a finite one.

    """
    if isinstance(value, bool):
        raise BadValue("want a dimension, got boolean")
    if isinstance(value, int | float):
        points = round_points(float(value))
    else:
        points = from_text(value)
    if not math.isfinite(points):
        raise BadValue(f"bad dimension {as_written(value)}: not finite")
    return points


def from_text(value: str) -> float:
    """Return the dimension a string spells, in points.

    Args:
        value: The property's value, as it was written.

    Raises:
        BadValue: The string is not a dimension.

    """
    text = value.strip()
    if not text:
        raise BadValue("empty dimension")
    factor = 1.0
    number = text
    for suffix in SUFFIXES:
        if text.endswith(suffix):
            factor = POINTS_PER_UNIT[suffix]
            number = text[: -len(suffix)].rstrip()
            break
    try:
        return round_points(parse_number(number) * factor)
    except BadValue:
        raise BadValue(f"bad dimension {as_written(value)}") from None
