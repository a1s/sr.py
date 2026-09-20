"""Measurement and line breaking, rule by rule.

The `breaking/` probes hold this engine to the reference's own printouts
and are checked in `test_breaking_against_probes.py`.  What is here is
the other half: each rule of doc/layout.md#line-breaking stated on its own,
so that a change breaks the test named after the sentence it broke rather
than forty parametrised rows at once.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from sr.fonts.face import open_face
from sr.fonts.text import (
    BREAK,
    LEADING,
    Metrics,
    chunks,
    missing_glyph,
    quote_char,
    trim,
    walk,
    wrap,
)
from sr.units import round_points

ROOT = Path(__file__).resolve().parents[2]
REGULAR = ROOT / "example" / "fonts" / "Go-Regular.ttf"

# Go-Regular at size 10: `x` is exactly 5 pt, a space 2.7783203125 pt,
# and a character the face has not got measures `.notdef` at 7.5 pt.
WIDE = 1000.0


@pytest.fixture(scope="module")
def metrics() -> Metrics:
    """Return Go-Regular at ten points."""
    return Metrics(open_face(REGULAR), 10)


def lines(text: str, width: float, metrics: Metrics) -> list[str]:
    """Return just the lines a string wraps into.

    Args:
        text: What is being set.
        width: The box's width in points.
        metrics: The face and size.

    """
    return wrap(text, width, metrics).lines


# -- measurement ------------------------------------------------------


def test_leading_is_one_point_two_times_the_size() -> None:
    face = open_face(REGULAR)
    for size in (7, 8, 9, 10, 12, 14, 18):
        assert Metrics(face, size).leading == round_points(size * LEADING)
    assert Metrics(face, 10).leading == 12


def test_an_advance_scales_with_the_size() -> None:
    face = open_face(REGULAR)
    assert Metrics(face, 10).advance("x") == 5.0
    assert Metrics(face, 20).advance("x") == 10.0


def test_a_width_is_the_sum_of_the_advances_rounded_once(
    metrics: Metrics,
) -> None:
    assert metrics.width("xxxx xxxx") == 42.778
    assert metrics.width("") == 0.0


def test_nothing_is_kerned(metrics: Metrics) -> None:
    # A pair Go-Regular kerns is still measured as two advances,
    # because the engine reads `hmtx` and does not shape.
    assert metrics.width("AV") == round_points(
        metrics.advance("A") + metrics.advance("V")
    )


def test_a_character_the_face_lacks_measures_as_notdef(metrics: Metrics) -> None:
    assert metrics.advance("\t") == 7.5
    assert metrics.advance("\u4e2d") == 7.5


def test_the_missing_characters_come_back_in_the_order_they_were_met(
    metrics: Metrics,
) -> None:
    assert metrics.missing("a\tb\u4e2dc\t") == ("\t", "\u4e2d")
    assert metrics.missing("plain text") == ()


# -- mandatory breaks -------------------------------------------------


def test_a_newline_ends_a_line_and_appears_on_neither_side(
    metrics: Metrics,
) -> None:
    assert lines("one\ntwo", WIDE, metrics) == ["one", "two"]


def test_an_empty_paragraph_is_still_a_line(metrics: Metrics) -> None:
    assert lines("", WIDE, metrics) == [""]
    assert lines("\n", WIDE, metrics) == ["", ""]
    assert lines("\na", WIDE, metrics) == ["", "a"]
    assert lines("a\n", WIDE, metrics) == ["a", ""]
    assert lines("a\n\n\nb", WIDE, metrics) == ["a", "", "", "b"]


@pytest.mark.parametrize("character", ["\r", "\v", "\f", "\u0085", "\u2028"])
def test_nothing_else_ends_a_line(character: str, metrics: Metrics) -> None:
    assert lines(f"one{character}two", WIDE, metrics) == [f"one{character}two"]


def test_a_carriage_return_stays_at_the_end_of_the_line_before_a_newline(
    metrics: Metrics,
) -> None:
    assert lines("one\r\ntwo", WIDE, metrics) == ["one\r", "two"]


# -- break opportunities ----------------------------------------------


def test_the_two_break_opportunities_are_space_and_tab() -> None:
    assert set(BREAK) == {" ", "\t"}


@pytest.mark.parametrize(
    "character",
    ["-", "\u00ad", "/", "\u2014", "\u00a0", "\u200b", "\u2003", "\u2007", "\u3000"],
)
def test_nothing_else_breaks_a_line(character: str, metrics: Metrics) -> None:
    text = f"xxxxx{character}xxxxx"
    assert lines(text, 50, metrics) != ["xxxxx", "xxxxx"]


def test_a_run_of_han_characters_does_not_break_at_its_own_boundaries(
    metrics: Metrics,
) -> None:
    # Six `.notdef` boxes of 7.5 pt fill a 50 pt box, and the seventh
    # starts a line -- a cut, not a break between two characters.
    assert lines("\u4e2d" * 7, 50, metrics) == ["\u4e2d" * 6, "\u4e2d"]


# -- chunks -----------------------------------------------------------


def test_a_chunk_is_a_word_and_the_whitespace_after_it() -> None:
    assert chunks("one two") == ["one ", "two"]
    assert chunks("one  two ") == ["one  ", "two "]


def test_leading_whitespace_is_a_chunk_of_its_own() -> None:
    assert chunks("  one") == ["  ", "one"]
    assert chunks("\tone") == ["\t", "one"]


def test_a_paragraph_of_nothing_has_no_chunks() -> None:
    assert chunks("") == []


def test_spaces_and_tabs_make_one_run() -> None:
    assert chunks("one \t two") == ["one \t ", "two"]


def test_the_whitespace_in_a_chunk_counts_toward_the_fit(
    metrics: Metrics,
) -> None:
    # "xxxx xxxx" is 42.778 pt, and 45.557 counting the space after it.
    # In a box between those two widths a line takes one word when
    # more text follows and both words when nothing does.
    assert metrics.width("xxxx xxxx") == 42.778
    assert metrics.width("xxxx xxxx ") == 45.557
    assert lines("xxxx xxxx xxxx", 42.8, metrics) == ["xxxx", "xxxx xxxx"]
    assert lines("xxxx xxxx", 42.8, metrics) == ["xxxx xxxx"]


# -- accumulation -----------------------------------------------------


def test_a_line_is_accumulated_and_rounded_at_every_step(
    metrics: Metrics,
) -> None:
    # 72.2802734375 pt measured as one string, which rounds to 72.280
    # and is within a limit of 72.281; 72.282 accumulated, which is not.
    text = "q q q q q q q q q"
    assert metrics.width(text) == 72.280
    assert lines(text, 72.280, metrics) != [text]
    assert lines(text, 72.281, metrics) == [text]


def test_a_chunk_contributes_its_own_width_rounded_once(
    metrics: Metrics,
) -> None:
    # "qq " is 13.901 rounded once and 13.902 walked.  In a box of 19.462
    # the following `q` fits only if the line kept the first figure.
    assert metrics.width("qq ") == 13.901
    assert lines("qq q", 19.462, metrics) == ["qq q"]
    assert lines("qq q", 19.461, metrics) == ["qq", "q"]


# -- overlong runs ----------------------------------------------------


def test_the_walk_always_takes_its_first_codepoint(metrics: Metrics) -> None:
    assert walk("x", 0.001, metrics) == "x"
    assert walk("xyz", 0.001, metrics) == "x"


def test_a_box_narrower_than_one_character_takes_one_anyway(
    metrics: Metrics,
) -> None:
    assert lines("xxxxx", 1, metrics) == ["x", "x", "x", "x", "x"]


def test_the_walk_is_the_test_and_not_the_measured_string(
    metrics: Metrics,
) -> None:
    # "qq" is 11.123 measured as one string and 11.124 walked.
    # In a box of 11.122, whose limit is 11.123, the walk is what says no.
    assert metrics.width("qq") == 11.123
    assert walk("qq", 11.122, metrics) == "q"
    assert lines("qq", 11.122, metrics) == ["q", "q"]


def test_a_chunk_too_wide_for_the_line_moves_to_one_of_its_own(
    metrics: Metrics,
) -> None:
    assert lines("xx xxxxxxxxxxxx", 50, metrics) == ["xx", "xxxxxxxxxx", "xx"]


def test_what_a_cut_leaves_behind_wraps_like_ordinary_text(
    metrics: Metrics,
) -> None:
    assert lines("xxxxxxxxxxxx xx", 50, metrics) == ["xxxxxxxxxx", "xx xx"]


def test_the_unit_is_the_codepoint_not_the_grapheme(metrics: Metrics) -> None:
    # A combining mark is a unit of its own, so a cut may fall
    # between a letter and the mark that follows it.
    wrapped = lines("e\u0301" * 8, 50, metrics)
    assert any(one.startswith("\u0301") for one in wrapped)


def test_an_astral_codepoint_is_one_unit(metrics: Metrics) -> None:
    # Six `.notdef` boxes to a 50 pt line, as for any other character
    # the face has not got, rather than twelve halves of surrogate pairs.
    assert lines("\U0001f600" * 6, 50, metrics) == ["\U0001f600" * 6]


# -- whitespace and trimming ------------------------------------------


def test_a_break_consumes_the_whole_run_it_falls_at(metrics: Metrics) -> None:
    assert lines("xxxxx   xxxxx", 50, metrics) == ["xxxxx", "xxxxx"]
    assert lines("xxxxx \t xxxxx", 50, metrics) == ["xxxxx", "xxxxx"]


def test_leading_whitespace_stays(metrics: Metrics) -> None:
    assert lines("  xxxxx", 50, metrics) == ["  xxxxx"]


def test_whitespace_inside_a_line_is_neither_collapsed_nor_trimmed(
    metrics: Metrics,
) -> None:
    assert lines("xxxxxxxxxxxx  xx", 50, metrics) == ["xxxxxxxxxx", "xx  xx"]


def test_the_last_line_of_a_paragraph_is_trimmed(metrics: Metrics) -> None:
    assert lines("xxxxx  ", 50, metrics) == ["xxxxx"]
    assert lines("xxxxx\t", 50, metrics) == ["xxxxx"]


def test_trimming_stops_at_the_first_character_that_is_not_whitespace() -> None:
    assert trim("a \u00a0 ") == "a \u00a0"
    assert trim("a  ") == "a"
    assert trim("  ") == ""


def test_a_line_that_was_nothing_but_a_consumed_run_is_left_empty(
    metrics: Metrics,
) -> None:
    assert lines("xxx xxx", 4, metrics) == ["x", "x", "x", "", "x", "x", "x"]


def test_a_line_ended_by_a_cut_keeps_the_whitespace_it_ends_on(
    metrics: Metrics,
) -> None:
    assert "ww " in lines("WqwMMMmMWlww  ", 17.49, metrics)


# -- a box of zero width ----------------------------------------------


def test_a_zero_width_box_is_not_wrapped(metrics: Metrics) -> None:
    assert lines("xxxxx xxxxx", 0, metrics) == ["xxxxx xxxxx"]


def test_a_mandatory_break_still_applies_in_a_zero_width_box(
    metrics: Metrics,
) -> None:
    assert lines("xxx\nxxx", 0, metrics) == ["xxx", "xxx"]


def test_any_positive_width_wraps(metrics: Metrics) -> None:
    assert lines("xxxxx", 0.001, metrics) == ["x", "x", "x", "x", "x"]


# -- how a character is named -----------------------------------------


@pytest.mark.parametrize(
    ("character", "spelled"),
    [
        ("\t", "'\\t'"),
        ("\n", "'\\n'"),
        ("\v", "'\\v'"),
        ("\f", "'\\f'"),
        ("\r", "'\\r'"),
        ("\x00", "'\\x00'"),
        ("\u0085", "'\\u0085'"),
        ("\u2003", "'\\u2003'"),
        ("\u200b", "'\\u200b'"),
        ("\u3000", "'\\u3000'"),
        ("\u4e2d", "'\u4e2d'"),
        ("\u0301", "'\u0301'"),
        ("\U0001f600", "'\U0001f600'"),
        ("a", "'a'"),
        (" ", "' '"),
        ("'", "'\\''"),
        ("\\", "'\\\\'"),
    ],
)
def test_a_character_is_named_the_way_go_quotes_a_rune(
    character: str, spelled: str
) -> None:
    assert quote_char(character) == spelled


def test_the_missing_glyph_warning_is_the_one_the_printout_carries() -> None:
    warning = missing_glyph("body", "\t", "report > layout > detail > field")
    assert warning.kind == "glyph"
    assert warning.message == (
        "the font \"body\" has no glyph for '\\t', "
        "so an empty box is drawn in its place"
    )
    assert warning.node == "report > layout > detail > field"
