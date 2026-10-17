"""Each mark's operators, against doc/render.md's own arithmetic.

The four things a renderer decides are where these tests spend their
time: where a baseline sits, how a justified line spends its slack,
what a dash measures, and where an underline goes.  The expected
numbers are worked from the specification's formulas and the Go
face's tables, so a test reads as the sum it checks.

Go Regular has 2048 units to the em, an `hhea` ascender of 1935 and
descender of -432, and a `post` underline 275 units down and 50 thick.
At 10 points that is an ascender of 9.4482421875, a descender of
2.109375, an underline 1.3427734375 down and 0.244140625 thick.

"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sr.fonts.face import open_bytes
from sr.fonts.text import Metrics
from sr.pdf import draw
from sr.pdf.draw import Setting
from sr.pdf.images import Picture
from sr.pdf.objects import Stream, number
from sr.pdf.subset import Embedded
from sr.printout.model import Barcode, Box, Crop, Image, Line, Rectangle, Text
from tests.unit.madefonts import made_font

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").read_bytes()

# Every page here is this tall, so a flipped Y is 800 less the printout's.
HEIGHT = 800.0

TM = re.compile(r"1 0 0 1 (\S+) (\S+) Tm")


def setting(
    *lines: str,
    data: bytes = REGULAR,
    size: int = 10,
    underline: bool = False,
) -> Setting:
    """Return a font that has been given these lines, and frozen.

    Args:
        *lines: What the font sets.
        data: The face's bytes.
        size: The size.
        underline: Whether it draws an underline.

    """
    face = open_bytes(data, "face")
    font = Embedded(face, data, 0, "F1")
    for one in lines:
        font.take(one)
    font.freeze()
    return Setting(font, size, Metrics(face, size), underline)


def text_mark(
    *lines: str,
    align: str = "left",
    box: Box | None = None,
    leading: float = 12,
    last: bool = False,
) -> Text:
    """Return a text mark holding these lines.

    Args:
        *lines: Its lines.
        align: Its alignment.
        box: Its box; 200 wide at (100, 100) by default.
        leading: Its leading.
        last: Whether its last line is justified.

    """
    return Text(
        box or Box(100, 100, 200, 12 * len(lines)),
        font="body",
        color="#000000",
        align=align,
        leading=leading,
        lines=lines,
        last_line_justified=last,
    )


def positions(ops: list[str]) -> list[tuple[float, float]]:
    """Return where each run of text was put, from its `Tm`.

    Args:
        ops: A text mark's operators.

    """
    return [
        (float(found[1]), float(found[2]))
        for found in (TM.fullmatch(one) for one in ops)
        if found
    ]


def shown(ops: list[str], font: Embedded) -> list[str]:
    """Return the text of each run, decoded from its codes.

    Args:
        ops: A text mark's operators.
        font: The font the codes belong to.

    """
    back = {code: character for character, code in font.codes.items()}
    runs = []
    for one in ops:
        if one.endswith(" Tj"):
            hexed = one[1 : one.index(">")]
            codes = (hexed[at : at + 4] for at in range(0, len(hexed), 4))
            runs.append("".join(back[int(code, 16)] for code in codes))
    return runs


# -- the baseline -----------------------------------------------------


def test_the_em_is_centred_in_the_leading_and_lines_follow_it() -> None:
    """The top, plus (12 - 11.5576171875) / 2, plus 9.4482421875.

    Each line after the first is 12 further down.

    """
    ops = draw.text(text_mark("Ab", "cd"), setting("Ab", "cd"), HEIGHT)
    assert positions(ops) == [(100, 690.331), (100, 678.331)]


def test_a_face_taller_than_its_leading_overhangs_evenly() -> None:
    font = setting("A")
    mark = text_mark("A", leading=10)
    base = draw.baseline(mark, font, 0)
    above = mark.box.y - (base - font.font.ascender(10))
    below = (base + font.font.descender(10)) - (mark.box.y + 10)
    assert above == pytest.approx(below)
    assert above == pytest.approx((11.5576171875 - 10) / 2)


def test_a_descender_stored_as_a_magnitude_means_the_same_distance() -> None:
    negative = setting("A", data=made_font(descender=-200))
    positive = setting("A", data=made_font(descender=200))
    mark = text_mark("A")
    assert draw.baseline(mark, negative, 0) == draw.baseline(mark, positive, 0)


# -- where a line starts ----------------------------------------------


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_a_line_is_placed_by_its_measured_width(align: str) -> None:
    font = setting("Ab")
    width = font.metrics.width("Ab")
    start = {
        "left": 100,
        "center": 100 + (200 - width) / 2,
        "right": 100 + 200 - width,
    }[align]
    ops = draw.text(text_mark("Ab", align=align), font, HEIGHT)
    assert positions(ops)[0][0] == pytest.approx(start, abs=0.0005)


def test_a_justified_line_ends_on_the_far_edge() -> None:
    line = "aa bb cc"
    font = setting(line, "next")
    ops = draw.text(text_mark(line, "next", align="justified"), font, HEIGHT)
    segments = ["aa ", "bb ", "cc"]
    widths = [font.metrics.width(one) for one in segments]
    gap = (200 - sum(widths)) / 2
    assert shown(ops, font.font)[:3] == segments
    starts = [one for one, _ in positions(ops)[:3]]
    assert starts == pytest.approx(
        [100, 100 + widths[0] + gap, 100 + widths[0] + widths[1] + 2 * gap],
        abs=0.0005,
    )
    assert starts[2] + widths[2] == pytest.approx(300, abs=0.0005)


def test_leading_whitespace_stays_with_the_first_segment() -> None:
    assert draw.segments_of("  aa bb") == ["  aa ", "bb"]
    assert draw.segments_of("aa\t bb  cc") == ["aa\t ", "bb  ", "cc"]
    assert draw.segments_of("   ") == ["   "]


def test_the_last_line_of_a_paragraph_is_set_flush_left() -> None:
    font = setting("aa bb", "cc dd")
    mark = text_mark("aa bb", "cc dd", align="justified")
    ops = draw.text(mark, font, HEIGHT)
    assert len(positions(ops)) == 3
    assert shown(ops, font.font)[2] == "cc dd"


def test_a_paragraph_a_split_band_continues_justifies_its_last_line() -> None:
    font = setting("aa bb")
    mark = text_mark("aa bb", align="justified", last=True)
    ops = draw.text(mark, font, HEIGHT)
    assert shown(ops, font.font) == ["aa ", "bb"]


def test_a_line_with_no_join_or_no_room_is_set_flush_left() -> None:
    font = setting("word", "aa bb", "next")
    single = text_mark("word", "next", align="justified")
    alone = draw.text(single, font, HEIGHT)
    assert shown(alone, font.font)[0] == "word"
    narrow = text_mark(
        "aa bb",
        "next",
        align="justified",
        box=Box(100, 100, 5, 24),
    )
    full = draw.text(narrow, font, HEIGHT)
    assert shown(full, font.font)[0] == "aa bb"
    assert positions(full)[0][0] == 100


def test_an_empty_line_draws_nothing_and_keeps_its_place() -> None:
    font = setting("a")
    ops = draw.text(text_mark("", "a"), font, HEIGHT)
    assert positions(ops) == [(100, 678.331)]


# -- underline --------------------------------------------------------


def test_an_underline_hangs_from_the_post_table() -> None:
    """Its top 1.3427734375 below the baseline, 0.244140625 tall."""
    font = setting("Ab", underline=True)
    ops = draw.text(text_mark("Ab"), font, HEIGHT)
    width = number(font.metrics.width("Ab"))
    assert ops[-2:] == [f"100 688.744 {width} 0.244 re", "f"]


def test_a_justified_underline_spans_the_box() -> None:
    font = setting("aa bb", "c", underline=True)
    ops = draw.text(text_mark("aa bb", "c", align="justified"), font, HEIGHT)
    rules = [one for one in ops if one.endswith(" re")]
    assert rules[0].split()[2] == "200"
    assert rules[1].split()[2] == number(font.metrics.width("c"))


def test_a_face_that_states_no_underline_gets_render_md_s() -> None:
    """A tenth of an em down, a twentieth of an em thick."""
    font = setting("A", data=made_font(underline=(0, 0)), underline=True)
    assert font.font.underline(10) == (1, 0.5)
    assert setting("A", underline=True).font.underline(10) == pytest.approx(
        (1.3427734375, 0.244140625),
    )


def test_a_line_of_whitespace_gets_no_underline() -> None:
    font = setting("  ", "a", underline=True)
    ops = draw.text(text_mark("  ", "a"), font, HEIGHT)
    assert len([one for one in ops if one.endswith(" re")]) == 1


# -- lines and rectangles ---------------------------------------------


def test_a_line_runs_from_its_top_left_corner_to_its_bottom_right() -> None:
    mark = Line(
        Box(10, 20, 100, 50),
        width=0.5,
        dash="dot",
        color="#808080",
        backslant=False,
    )
    assert draw.line(mark, HEIGHT) == [
        "0.5 w",
        "[1 2] 0 d",
        "0.502 0.502 0.502 RG",
        "10 780 m",
        "110 730 l",
        "S",
    ]


def test_a_backslant_runs_from_bottom_left_to_top_right() -> None:
    mark = Line(
        Box(10, 20, 100, 50),
        width=0,
        dash="solid",
        color="#000000",
        backslant=True,
    )
    assert draw.line(mark, HEIGHT)[:4] == [
        "0 w",
        "[] 0 d",
        "0 0 0 RG",
        "10 730 m",
    ]
    assert draw.line(mark, HEIGHT)[4] == "110 780 l"


@pytest.mark.parametrize(
    ("dash", "pattern"),
    [
        ("solid", "[] 0 d"),
        ("dot", "[1 2] 0 d"),
        ("dash", "[3 2] 0 d"),
        ("dashdot", "[3 2 1 2] 0 d"),
    ],
)
def test_a_dash_is_in_points_whatever_the_width(
    dash: str,
    pattern: str,
) -> None:
    for width in (0, 2):
        mark = Line(
            Box(0, 0, 10, 0),
            width=width,
            dash=dash,
            color="#000000",
            backslant=False,
        )
        assert draw.line(mark, HEIGHT)[1] == pattern


def rectangle(
    stroke: str | None,
    fill: str | None,
    radius: float = 0,
    box: Box | None = None,
) -> Rectangle:
    """Return a rectangle mark.

    Args:
        stroke: Its outline colour.
        fill: Its interior colour.
        radius: Its corner radius.
        box: Its box; 100 by 10 at the origin by default.

    """
    return Rectangle(
        box or Box(0, 0, 100, 10),
        width=1,
        dash="solid",
        stroke=stroke,
        fill=fill,
        radius=radius,
    )


def test_a_rectangle_with_neither_stroke_nor_fill_draws_nothing() -> None:
    assert draw.rectangle(rectangle(None, None), HEIGHT) == []


def test_a_stroke_and_a_fill_are_independent() -> None:
    assert draw.rectangle(rectangle("#000000", None), HEIGHT) == [
        "1 w",
        "[] 0 d",
        "0 0 0 RG",
        "0 790 100 10 re",
        "S",
    ]
    assert draw.rectangle(rectangle(None, "#FF0000"), HEIGHT) == [
        "1 0 0 rg",
        "0 790 100 10 re",
        "f",
    ]
    assert draw.rectangle(rectangle("#000000", "#FF0000"), HEIGHT) == [
        "1 0 0 rg",
        "1 w",
        "[] 0 d",
        "0 0 0 RG",
        "0 790 100 10 re",
        "B",
    ]


def test_a_radius_is_clamped_to_half_the_shorter_side() -> None:
    path = draw.rectangle(rectangle(None, "#000000", radius=20), HEIGHT)
    assert path[1] == "5 790 m"
    assert path[2] == "95 790 l"
    assert len([one for one in path if one.endswith(" c")]) == 4
    assert path[-2:] == ["h", "f"]


# -- images -----------------------------------------------------------


def picture(width: int, height: int) -> Picture:
    """Return an image XObject of a size, holding nothing that matters.

    Args:
        width: In pixels.
        height: In pixels.

    """
    return Picture("Im1", width, height, Stream({}, b""))


def test_an_image_with_no_crop_is_mapped_onto_its_box() -> None:
    mark = Image(Box(10, 20, 30, 40), data="x")
    assert draw.image(mark, picture(60, 80), HEIGHT) == [
        "q",
        "30 0 0 40 10 740 cm",
        "/Im1 Do",
        "Q",
    ]


def test_a_crop_places_the_whole_image_and_clips_to_the_box() -> None:
    mark = Image(Box(10, 20, 30, 40), data="x", crop=Crop(5, 10, 30, 40))
    assert draw.image(mark, picture(60, 80), HEIGHT) == [
        "q",
        "10 740 30 40 re",
        "W n",
        "60 0 0 80 5 710 cm",
        "/Im1 Do",
        "Q",
    ]


def test_a_crop_s_scale_is_the_box_over_the_crop() -> None:
    mark = Image(Box(0, 0, 20, 20), data="x", crop=Crop(2, 2, 10, 10))
    placed = draw.image(mark, picture(40, 40), HEIGHT)
    assert placed[3] == "80 0 0 80 -4 724 cm"


def test_an_image_with_no_area_draws_nothing() -> None:
    mark = Image(Box(0, 0, 0, 10), data="x")
    assert draw.image(mark, picture(4, 4), HEIGHT) == []


# -- barcodes ---------------------------------------------------------


def barcode(
    box: Box,
    *,
    stripes: tuple[int, ...] = (),
    rows: tuple[tuple[int, ...], ...] = (),
    module: float = 1,
    vertical: bool = False,
    paper: str | None = None,
) -> Barcode:
    """Return a barcode mark.

    Args:
        box: Its box.
        stripes: A 1-D symbol's runs.
        rows: A 2-D symbol's rows.
        module: Its module.
        vertical: Whether it is turned.
        paper: Its background.

    """
    return Barcode(
        box,
        symbology="Code128",
        value="x",
        module=module,
        vertical=vertical,
        ink="#000000",
        paper=paper,
        stripes=stripes,
        rows=rows,
    )


def test_a_1d_symbol_s_bars_are_its_odd_runs_across_the_whole_box() -> None:
    mark = barcode(Box(10, 20, 9, 30), stripes=(2, 1, 1, 3, 2))
    assert draw.barcode(mark, HEIGHT) == [
        "0 0 0 rg",
        "12 750 1 30 re",
        "14 750 3 30 re",
        "f",
    ]


def test_a_vertical_1d_symbol_codes_downward() -> None:
    mark = barcode(Box(10, 20, 30, 9), stripes=(2, 1, 1, 3, 2), vertical=True)
    assert draw.barcode(mark, HEIGHT)[1:3] == [
        "10 777 30 1 re",
        "10 773 30 3 re",
    ]


def test_the_paper_is_filled_over_the_whole_box_first() -> None:
    mark = barcode(
        Box(10, 20, 9, 30),
        stripes=(2, 1, 1, 3, 2),
        paper="#FFFF00",
    )
    assert draw.barcode(mark, HEIGHT)[:4] == [
        "1 1 0 rg",
        "10 750 9 30 re",
        "f",
        "0 0 0 rg",
    ]


def test_a_2d_symbol_s_rows_go_down_and_its_runs_rightward() -> None:
    mark = barcode(Box(0, 0, 8, 4), rows=((1, 2, 1), (0, 1, 3)), module=2)
    assert draw.barcode(mark, HEIGHT) == [
        "0 0 0 rg",
        "2 798 4 2 re",
        "0 796 2 2 re",
        "f",
    ]


def test_a_vertical_2d_symbol_is_turned_a_quarter_turn_clockwise() -> None:
    """The top row lands on the right edge, its left end at the top."""
    mark = barcode(
        Box(0, 0, 4, 8), rows=((1, 2, 1), (0, 1, 3)), module=2, vertical=True
    )
    assert draw.barcode(mark, HEIGHT)[1:3] == ["2 794 2 4 re", "0 798 2 2 re"]


def test_bars_that_meet_share_their_edge_to_the_thousandth() -> None:
    """Edges are rounded once, so a module of a third leaves no gap."""
    mark = barcode(Box(0, 0, 1, 1), rows=((0, 1, 0, 1, 0, 1),), module=1 / 3)
    bars = [one.split() for one in draw.barcode(mark, HEIGHT)[1:-1]]
    assert [one[0] for one in bars] == ["0", "0.333", "0.667"]
    assert [one[2] for one in bars] == ["0.333", "0.334", "0.333"]


def test_a_colour_s_levels_survive_three_decimals() -> None:
    for level in range(256):
        spelled = draw.colour(f"#{level:02X}0000").split()[0]
        assert round(float(spelled) * 255) == level
