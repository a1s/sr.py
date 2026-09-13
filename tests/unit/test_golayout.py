"""Go reference-time layouts, written out and read back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sr.errors import ExpressionError
from sr.expr.golayout import RFC3339, format_layout, parse_layout, split_layout

# The reference moment itself: 15:04:05 on Monday 2 January 2006, -0700.
# Every token is a digit of this, which is what makes a layout an example
# rather than a set of directives.
REFERENCE = datetime(2006, 1, 2, 15, 4, 5, tzinfo=timezone(timedelta(hours=-7)))


def written(layout: str, nanosecond: int = 0) -> str:
    """Return the reference moment written by a layout."""
    return format_layout(layout, REFERENCE, nanosecond)


@pytest.mark.parametrize(
    ("layout", "shown"),
    [
        ("2006-01-02", "2006-01-02"),
        ("02.01.2006", "02.01.2006"),
        ("2/1/06", "2/1/06"),
        ("January 2, 2006", "January 2, 2006"),
        ("Jan _2 15:04:05", "Jan  2 15:04:05"),
        ("Monday, Mon", "Monday, Mon"),
        ("3:04PM", "3:04PM"),
        ("03:04pm", "03:04pm"),
        ("15:04:05", "15:04:05"),
        ("002", "002"),
        ("-0700", "-0700"),
        ("-07:00", "-07:00"),
        ("-07", "-07"),
        ("-070000", "-070000"),
        ("Z07:00", "-07:00"),
        ("MST", "-0700"),
    ],
)
def test_every_token_writes_the_reference_moment_back(layout: str, shown: str) -> None:
    assert written(layout) == shown


def test_a_layout_is_scanned_longest_token_first() -> None:
    """`2006` is a year, not a `2` and then `006`, which would give `2006`."""
    assert [kind for kind, _ in split_layout("2006")] == ["long-year"]
    assert [kind for kind, _ in split_layout("20060102")] == [
        "long-year",
        "zero-month",
        "zero-day",
    ]


def test_a_padded_fraction_keeps_its_zeros_and_a_trimmed_one_drops_them() -> None:
    assert written(".000", 120_000_000) == ".120"
    assert written(".999", 120_000_000) == ".12"
    assert written(".000000000", 1) == ".000000001"


def test_a_trimmed_fraction_of_nothing_takes_its_point_with_it() -> None:
    assert written("05.999", 0) == "05"
    assert written("05.000", 0) == "05.000"


def test_the_zone_abbreviation_falls_back_to_the_offset() -> None:
    """A fixed offset has no name, which is what a parsed `-0700` leaves."""
    assert written("MST") == "-0700"


def test_the_iso_zone_writes_z_for_no_offset() -> None:
    moment = REFERENCE.astimezone(UTC)
    assert format_layout("Z07:00", moment, 0) == "Z"
    assert format_layout("-07:00", moment, 0) == "+00:00"


def test_rfc3339_reads_what_it_writes() -> None:
    moment, nanosecond = parse_layout(RFC3339, "2005-05-24T22:53:30Z", UTC)
    assert format_layout(RFC3339, moment, nanosecond) == "2005-05-24T22:53:30Z"


def test_a_parsed_offset_is_kept_rather_than_normalized_away() -> None:
    moment, _ = parse_layout(RFC3339, "2005-05-24T22:53:30+03:00", UTC)
    assert moment.hour == 22
    assert moment.utcoffset() == timedelta(hours=3)


def test_a_layout_with_no_zone_reads_in_the_location_given() -> None:
    riga = timezone(timedelta(hours=3))
    moment, _ = parse_layout("2006-01-02 15:04:05", "2005-05-24 22:53:30", riga)
    assert moment.utcoffset() == timedelta(hours=3)


def test_a_twelve_hour_clock_reads_its_meridiem() -> None:
    moment, _ = parse_layout("3:04PM", "3:04PM", UTC)
    assert moment.hour == 15
    morning, _ = parse_layout("3:04PM", "3:04AM", UTC)
    assert morning.hour == 3


def test_a_two_digit_year_splits_at_sixty_nine() -> None:
    assert parse_layout("06", "68", UTC)[0].year == 2068
    assert parse_layout("06", "69", UTC)[0].year == 1969


def test_a_month_name_is_read_whatever_its_case() -> None:
    assert parse_layout("Jan 2 2006", "MAY 24 2005", UTC)[0].month == 5
    assert parse_layout("January 2 2006", "may 24 2005", UTC)[0].month == 5


def test_an_optional_fraction_may_be_absent_or_present() -> None:
    layout = "2006-01-02T15:04:05.999Z07:00"
    assert parse_layout(layout, "2005-05-24T22:53:30Z", UTC)[1] == 0
    assert parse_layout(layout, "2005-05-24T22:53:30.5Z", UTC)[1] == 500_000_000


def test_a_seconds_token_takes_a_fraction_the_layout_never_mentions() -> None:
    """Go's rule, and what lets plain RFC 3339 read `22:53:30.6Z`."""
    moment, nanosecond = parse_layout(RFC3339, "2005-05-24T22:53:30.6Z", UTC)
    assert (moment.second, nanosecond) == (30, 600_000_000)
    assert parse_layout(RFC3339, "2005-05-24T22:53:30,6Z", UTC)[1] == 600_000_000
    assert parse_layout(RFC3339, "2005-05-24T22:53:30Z", UTC)[1] == 0


def test_a_layout_that_names_its_fraction_still_reads_it_itself() -> None:
    """A fixed-width token stays a requirement rather than an option."""
    layout = "15:04:05.000"
    assert parse_layout(layout, "22:53:30.600", UTC)[1] == 600_000_000
    with pytest.raises(ExpressionError, match="cannot parse"):
        parse_layout(layout, "22:53:30", UTC)


def test_text_that_is_not_the_layout_is_refused() -> None:
    with pytest.raises(ExpressionError, match="cannot parse"):
        parse_layout(RFC3339, "24 May 2005", UTC)


def test_trailing_text_is_refused_rather_than_ignored() -> None:
    with pytest.raises(ExpressionError, match="the end of the value"):
        parse_layout("2006-01-02", "2005-05-24 and more", UTC)


def test_a_date_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ExpressionError, match="cannot parse"):
        parse_layout("2006-01-02", "2005-02-30", UTC)


def test_only_ascii_digits_are_digits() -> None:
    """Python says the Devanagari digits are digits; a timestamp does not."""
    with pytest.raises(ExpressionError, match="cannot parse"):
        parse_layout("2006", "२०२०", UTC)
