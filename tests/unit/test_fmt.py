"""Both percent formatters: the engine's, and Starlark's own."""

from __future__ import annotations

import pytest

from sr.errors import ExpressionError
from sr.expr.fmt import apply_format, format_value, interpolate
from sr.expr.values import Decimal, Set, Time

# ------------------------------------------------- the engine's formatter


@pytest.mark.parametrize(
    ("spec", "value", "shown"),
    [
        ("%s", "text", "text"),
        ("%s", 1.5, "1.5"),
        ("%s", [1, "a"], '[1, "a"]'),
        ("%q", 'a"b', '"a\\"b"'),
        ("%d", 42, "42"),
        ("%i", 42, "42"),
        ("%o", 8, "10"),
        ("%x", 255, "ff"),
        ("%X", 255, "FF"),
        ("%b", 5, "101"),
        ("%c", 65, "A"),
        ("%e", 1234.5, "1.234500e+03"),
        ("%E", 1234.5, "1.234500E+03"),
        ("%f", 1.5, "1.500000"),
        ("%g", 1234.5, "1234.5"),
        ("%G", 0.00001, "1E-05"),
        ("%%", None, "%"),
    ],
)
def test_every_conversion_the_specification_lists(
    spec: str, value: object, shown: str
) -> None:
    args = () if spec == "%%" else (value,)
    assert format_value(spec, *args) == shown


@pytest.mark.parametrize(
    ("spec", "value", "shown"),
    [
        ("%5d", 42, "   42"),
        ("%-5d|", 42, "42   |"),
        ("%05d", 42, "00042"),
        ("%+d", 42, "+42"),
        ("% d", 42, " 42"),
        ("%05d", -42, "-0042"),
        ("%#x", 255, "0xff"),
        ("%#o", 8, "010"),
        ("%.3d", 4, "004"),
        ("%.2f", 1.005, "1.00"),
        ("%8.2f", 1.5, "    1.50"),
        ("%-8.2f|", 1.5, "1.50    |"),
        ("%08.2f", -1.5, "-0001.50"),
        ("%.2s", "abcdef", "ab"),
        ("%3d.", 7, "  7."),
    ],
)
def test_flags_width_and_precision(spec: str, value: object, shown: str) -> None:
    assert format_value(spec, value) == shown


def test_a_decimal_is_formatted_exactly_rather_than_through_a_float() -> None:
    """1.005 is not 1.005 in binary64; 19.995 is not 19.995 either."""
    assert format_value("%.2f", Decimal("1.005")) == "1.01"
    assert format_value("%.2f", Decimal("19.995")) == "20.00"
    assert format_value("%.2f", Decimal("-19.995")) == "-20.00"


def test_a_decimal_keeps_its_digits_however_many_there_are() -> None:
    long = Decimal("123456789012345678901234567890.12")
    assert format_value("%.2f", long) == "123456789012345678901234567890.12"


def test_a_decimal_rounds_for_an_integer_conversion_and_a_float_truncates() -> None:
    """The reference's asymmetry, measured: `%d` renders, `int` converts."""
    assert format_value("%d", Decimal("1.9")) == "2"
    assert format_value("%d", Decimal("-1.9")) == "-2"
    assert format_value("%d", Decimal("2.5")) == "3"
    assert format_value("%x", Decimal("255.6")) == "100"
    assert format_value("%d", 1.9) == "1"
    assert format_value("%d", -1.9) == "-1"


@pytest.mark.parametrize(
    ("spec", "value", "shown"),
    [
        ("%e", "1234.5", "1.234500e+03"),
        ("%E", "1234.5", "1.234500E+03"),
        ("%.0e", "1234.5", "1e+03"),
        ("%e", "0", "0.000000e+00"),
        ("%g", "1234567", "1.234567e+06"),
        ("%g", "123456", "123456"),
        ("%g", "100.00", "100"),
        ("%g", "1234.5", "1234.5"),
        ("%g", "0.0000001", "1e-07"),
        ("%g", "0", "0"),
        ("%.3g", "1234.5", "1.23e+03"),
        ("%g", "-1234567", "-1.234567e+06"),
    ],
)
def test_a_decimal_takes_the_same_shape_a_float_does(
    spec: str, value: str, shown: str
) -> None:
    """Two exponent digits, and the turnover at 1e6 -- both measured.

    Python's own decimal formatting gives neither: it writes `e+3` for
    the exponent and knows nothing of Go's `%g` shape.

    """
    assert format_value(spec, Decimal(value)) == shown


def test_a_precision_with_no_digits_is_a_precision_of_zero() -> None:
    """`%.f` is a conversion, not an `int("")`."""
    assert format_value("%.f", 1.5) == "2"
    assert format_value("%.f", Decimal("1.5")) == "2"
    assert format_value("%.f", 7) == "7"


def test_percent_g_without_a_precision_is_the_shortest_form() -> None:
    """Go's default for %g is shortest; Python's is six significant digits."""
    assert format_value("%g", 1234567.0) == "1.234567e+06"
    assert format_value("%g", 0.1) == "0.1"


def test_a_tuple_spreads_across_the_conversions() -> None:
    assert (
        apply_format("Total for %s, %s: %.2f", ("Smith", "John", Decimal("19.99")))
        == "Total for Smith, John: 19.99"
    )


def test_the_default_format_is_the_whole_value() -> None:
    assert apply_format("%s", Set(["a"])) == 'set(["a"])'


def test_the_count_of_values_must_match_the_conversions() -> None:
    with pytest.raises(ExpressionError, match="takes 1 values, 2 given"):
        format_value("%s", 1, 2)
    with pytest.raises(ExpressionError, match="wants more values"):
        format_value("%s %s", 1)


def test_an_unknown_conversion_is_refused() -> None:
    with pytest.raises(ExpressionError, match="unknown conversion %z"):
        format_value("%z", 1)


def test_a_format_that_ends_in_a_percent_is_truncated_rather_than_literal() -> None:
    """The one `%` no conversion pattern can match, in both formatters."""
    for spec in ("100%", "%", "%%%", "%d%"):
        with pytest.raises(ExpressionError, match="truncated conversion"):
            format_value(spec, *([5] if "d" in spec else []))
        with pytest.raises(ExpressionError, match="truncated conversion"):
            interpolate(spec, (5,) if "d" in spec else ())
    assert format_value("100%%") == "100%"
    assert format_value("%d%%", 5) == "5%"


def test_a_conversion_refuses_a_value_it_cannot_write() -> None:
    with pytest.raises(ExpressionError, match="%d wants a number"):
        format_value("%d", "a")
    with pytest.raises(ExpressionError, match="%f wants a number"):
        format_value("%f", Time(0))


# -------------------------------------------------------- Starlark's own


def test_starlarks_percent_takes_the_plain_conversions() -> None:
    assert interpolate("%s, %s", ("Smith", "John")) == "Smith, John"
    assert interpolate("%d items", 3) == "3 items"
    assert interpolate("%r", "a") == '"a"'
    assert interpolate("100%%", ()) == "100%"


def test_starlarks_percent_has_no_flags_width_or_precision() -> None:
    """`%.2f` is a `%.` that is not a conversion, which is an error."""
    with pytest.raises(ExpressionError, match="unknown conversion"):
        interpolate("%.2f", 1.5)
    with pytest.raises(ExpressionError, match="unknown conversion"):
        interpolate("%5d", 1)


def test_starlarks_percent_has_no_q_and_the_engines_has_no_r() -> None:
    with pytest.raises(ExpressionError, match="unknown conversion %q"):
        interpolate("%q", "a")
    with pytest.raises(ExpressionError, match="unknown conversion %r"):
        format_value("%r", "a")


def test_starlarks_percent_reads_a_dict_by_key() -> None:
    assert interpolate("%(name)s", {"name": "Smith"}) == "Smith"


def test_starlarks_percent_counts_its_arguments() -> None:
    with pytest.raises(ExpressionError, match="takes 1 values, 2 given"):
        interpolate("%s", (1, 2))


def test_a_single_value_need_not_be_a_tuple() -> None:
    assert interpolate("%s", 1) == "1"
