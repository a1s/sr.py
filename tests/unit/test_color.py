"""Colours: the four spellings, and what each refuses.

The accepted values are also the `values/colors` probe, so the reference
agrees with every table below that appears there.  What is here and not
there is the refusals, which a probe cannot carry: a template with a bad
colour in it does not build.

"""

from __future__ import annotations

import pytest

from sr.color import NAMES, channels, parse_color
from sr.errors import BadValue


def test_the_sixteen_html_names() -> None:
    assert parse_color("black") == "#000000"
    assert parse_color("silver") == "#C0C0C0"
    assert parse_color("gray") == "#808080"
    assert parse_color("white") == "#FFFFFF"
    assert parse_color("maroon") == "#800000"
    assert parse_color("red") == "#FF0000"
    assert parse_color("purple") == "#800080"
    assert parse_color("fuchsia") == "#FF00FF"
    assert parse_color("green") == "#008000"
    assert parse_color("lime") == "#00FF00"
    assert parse_color("olive") == "#808000"
    assert parse_color("yellow") == "#FFFF00"
    assert parse_color("navy") == "#000080"
    assert parse_color("blue") == "#0000FF"
    assert parse_color("teal") == "#008080"
    assert parse_color("aqua") == "#00FFFF"


def test_the_six_the_format_adds() -> None:
    assert parse_color("cyan") == "#00FFFF"
    assert parse_color("darkgray") == "#A9A9A9"
    assert parse_color("lightgray") == "#D3D3D3"
    assert parse_color("magenta") == "#FF00FF"
    assert parse_color("orange") == "#FFA500"
    assert parse_color("pink") == "#FFC0CB"


def test_there_are_twenty_two_of_them_and_two_are_pairs() -> None:
    assert len(NAMES) == 22
    assert NAMES["cyan"] == NAMES["aqua"]
    assert NAMES["magenta"] == NAMES["fuchsia"]


def test_names_ignore_case_and_surrounding_space() -> None:
    assert parse_color("ReD") == "#FF0000"
    assert parse_color("DARKGRAY") == "#A9A9A9"
    assert parse_color(" red ") == "#FF0000"


def test_there_is_no_second_spelling_of_gray() -> None:
    with pytest.raises(BadValue, match='bad colour "grey"'):
        parse_color("grey")


def test_case_folding_stops_at_ascii() -> None:
    """`str.lower` would make a dotted capital I into an i.

    Turkish `PİNK` is not `pink`, and a colour that resolved under one
    Unicode fold and not another would be a very hard thing to report.

    """
    with pytest.raises(BadValue):
        parse_color("PİNK")


def test_hex_is_taken_in_either_case_and_given_back_in_one() -> None:
    assert parse_color("#aabbcc") == "#AABBCC"
    assert parse_color("#AABBCC") == "#AABBCC"
    assert parse_color("#ffffff") == "#FFFFFF"


@pytest.mark.parametrize("text", ["#abc", "#aabbccdd", "#GGGGGG", "#12345", "#"])
def test_hex_is_six_digits_and_nothing_else(text: str) -> None:
    with pytest.raises(BadValue, match="want #RRGGBB"):
        parse_color(text)


def test_three_integers_are_channels() -> None:
    assert parse_color("0,89,0") == "#005900"
    assert parse_color("255,255,255") == "#FFFFFF"
    assert parse_color("1,1,1") == "#010101"
    assert parse_color(" 1 , 2 , 3 ") == "#010203"
    assert parse_color("+1,0,0") == "#010000"
    assert parse_color("00255,0,0") == "#FF0000"


def test_anything_else_makes_all_three_fractions() -> None:
    assert parse_color("1.0,1.0,1.0") == "#FFFFFF"
    assert parse_color("0,1,0.") == "#00FF00"
    assert parse_color("1,1e-1,1") == "#FF1AFF"


def test_a_fraction_scales_with_halves_away_from_zero() -> None:
    assert parse_color("0.25,0.5,0.75") == "#4080BF"
    assert parse_color(".5,.5,.5") == "#808080"
    assert parse_color("0.1,0,0") == "#1A0000"


@pytest.mark.parametrize("text", ["256,0,0", "-1,0,0", "0,300,0"])
def test_a_channel_outside_its_range_is_refused(text: str) -> None:
    with pytest.raises(BadValue, match="out of 0-255"):
        parse_color(text)


@pytest.mark.parametrize("text", ["1.5,0,0", "-0.5,0,0", "1e2,0,0", "1_0,0,0"])
def test_a_fraction_outside_its_range_is_refused(text: str) -> None:
    with pytest.raises(BadValue, match="out of 0-1"):
        parse_color(text)


@pytest.mark.parametrize("text", ["0x10,0,0", "0,inf,0", "0,0,twelve"])
def test_a_component_that_is_not_a_number_says_that_instead(text: str) -> None:
    """Not "out of 0-1", which describes a value that was read.

    The two failures are one message in the reference, because Go's
    float parser reports them the same way.  doc/template.md#color
    names them separately, and a component that was never a number
    is the more confusing of the two to be told a range about.

    """
    with pytest.raises(BadValue, match="is not a number"):
        parse_color(text)


@pytest.mark.parametrize("text", ["1,2", "0,89", "1,2,3,4", "0,89,0,"])
def test_a_triple_is_three_components(text: str) -> None:
    with pytest.raises(BadValue, match="want three components"):
        parse_color(text)


def test_a_single_integer_packs_the_channels_by_bits() -> None:
    assert parse_color("16711935") == "#FF00FF"
    assert parse_color("0") == "#000000"
    assert parse_color("255") == "#0000FF"
    assert parse_color("16777215") == "#FFFFFF"
    assert parse_color("00255") == "#0000FF"


def test_bits_above_the_blue_and_red_are_ignored() -> None:
    assert parse_color("16777216") == "#000000"
    assert parse_color("4294967295") == "#FFFFFF"


@pytest.mark.parametrize(
    "text",
    [
        "-1",  # the single-integer form takes no sign
        "+255",
        "1_0",  # nor underscores
        "0xFF00FF",
        "99999999999999999999999",  # longer than an integer can be
        "notacolor",
    ],
)
def test_what_is_not_an_integer_is_not_a_colour(text: str) -> None:
    with pytest.raises(BadValue, match="bad colour"):
        parse_color(text)


def test_an_empty_colour_says_so_rather_than_being_black() -> None:
    with pytest.raises(BadValue, match="empty colour"):
        parse_color("")
    with pytest.raises(BadValue, match="empty colour"):
        parse_color("   ")


def test_the_canonical_form_comes_apart_again() -> None:
    assert channels("#FF00FF") == (255, 0, 255)
    assert channels("#000000") == (0, 0, 0)
    assert channels(parse_color("0.25,0.5,0.75")) == (64, 128, 191)
