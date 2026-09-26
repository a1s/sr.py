"""Laying a band out: geometry, height, content, and the record loop.

The differential suite holds all of this to the reference over
the probe corpus.  What is here is each rule on its own, so that
a change breaks the test named after the sentence it broke.

Every case is a whole small report rather than a call into
the measurer, because a band's height is only meaningful
against a frame and the frame is what the layout builds.

"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.errors import BuildError, Unsupported
from sr.printout.model import Line, Mark, Printout, Rectangle, Text
from sr.printout.model import Xref as XrefMark

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

# One record, whose contents nothing reads, so that a detail band runs.
ONE_ROW = '{"n":1}\n'

HEAD = """
report name="Layout" {
  font "body" file="FACE" size=10
  layout width=300 height=800 leftmargin=0 rightmargin=0 topmargin=0 bottommargin=0 {
    style font="body" color="black"
BANDS
  }
}
"""


def built(
    tmp_path: Path, body: str, data: str | None = ONE_ROW, **options: Any
) -> Printout:
    """Build a one-layout report and return its printout.

    Args:
        tmp_path: Where to write the template and the data.
        body: The bands, as KDL, indented under `layout`.
        data: The records, or ``None`` for a report with none.
        **options: What to pass to the build.

    """
    template = tmp_path / "report.kdl"
    template.write_text(
        HEAD.replace("FACE", REGULAR).replace("BANDS", body), encoding="utf-8"
    )
    rows = None
    if data is not None:
        rows = tmp_path / "rows.jsonl"
        rows.write_text(data, encoding="utf-8")
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True, **options)
    return build(template, rows, asked).printout


def marks(printout: Printout) -> tuple[Mark, ...]:
    """Return the marks of the one page."""
    assert len(printout.pages) == 1
    return printout.pages[0].marks


def boxes(printout: Printout) -> list[tuple[float, float, float, float]]:
    """Return every mark's box, in paint order."""
    return [
        (one.box.x, one.box.y, one.box.width, one.box.height) for one in marks(printout)
    ]


# -- geometry ---------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("", (0.0, 300.0)),
        ("left=10", (10.0, 290.0)),
        ("width=50", (0.0, 50.0)),
        ("right=20", (0.0, 280.0)),
        ("left=10 width=50", (10.0, 50.0)),
        ("right=10 width=50", (240.0, 50.0)),
        ("left=10 right=10", (10.0, 280.0)),
        ("x=10 width=50", (10.0, 50.0)),
    ],
)
def test_two_of_three_resolves_the_third(
    tmp_path: Path, written: str, expected: tuple[float, float]
) -> None:
    band = '    detail height=20 { field text="A" ' + written + " }"
    printout = built(tmp_path, band)
    box = marks(printout)[0].box
    assert (box.x, box.width) == expected


def test_a_rectangles_width_is_its_stroke_and_not_its_extent(
    tmp_path: Path,
) -> None:
    """`width` on a `line` and a `rectangle` is the stroke width.

    So a rectangle given `left=10 width=2` is ten points in from the
    left, runs to the container's right edge, and is stroked at 2.

    """
    printout = built(tmp_path, "    detail height=20 { rectangle left=10 width=2 }")
    mark = marks(printout)[0]
    assert isinstance(mark, Rectangle)
    assert (mark.box.x, mark.box.width, mark.width) == (10, 290, 2)


def test_maxwidth_clamps_a_resolved_extent(tmp_path: Path) -> None:
    printout = built(tmp_path, '    detail height=20 { field text="A" maxwidth=50 }')
    assert marks(printout)[0].box.width == 50


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("left=10 right=10", 10.0),
        ("right=10 width=100", 240.0),
        ("right=10", 0.0),
    ],
)
def test_a_clamped_box_keeps_the_edge_that_was_not_derived(
    tmp_path: Path, written: str, expected: float
) -> None:
    """`right` alone is filled to `left=0 right=10`, so its left is kept.

    Only a start the two of three *derived* gives way to the far edge.

    """
    band = '    detail height=20 { field text="A" maxwidth=50 ' + written + " }"
    box = marks(built(tmp_path, band))[0].box
    assert (box.x, box.width) == (expected, 50)


def test_maxheight_clamps_a_height_taken_from_the_band(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=100 {\n"
        "      line left=0 top=0 maxheight=30\n"
        "      line left=10 bottom=0 height=80 maxheight=30\n"
        "    }",
    )
    assert [(box[1], box[3]) for box in boxes(printout)] == [(0, 30), (70, 30)]


def test_a_negative_offset_reaches_past_the_container(tmp_path: Path) -> None:
    printout = built(
        tmp_path, '    detail height=20 { field text="A" left=0 right=-20 }'
    )
    assert marks(printout)[0].box.width == 320


# -- the band's height ------------------------------------------------


def test_a_declared_height_is_a_minimum_not_a_cap(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=10 { rectangle top=0 height=40 left=0 width=5 }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    assert boxes(printout)[1][1] == 40


def test_a_container_dependent_element_takes_the_first_height(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="A" left=0 top=0 width=50 height=6\n'
        "             line left=100 top=0 width=1 }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    text, rule, after = marks(printout)
    assert rule.box.height == 6, "the rule spans the declared boxes"
    assert text.box.height == 12, "the line of text overflows its box"
    assert after.box.y == 12, "and the band is as tall as the mark"


def test_a_stretch_field_with_no_vertical_geometry_has_a_height_of_its_own(
    tmp_path: Path,
) -> None:
    """Its bottom was filled in, and its text is a content height.

    So it takes part in the first maximum, and a rule beside it
    spans the text rather than collapsing.

    """
    printout = built(
        tmp_path,
        '    detail { field text="1\\n2\\n3" left=0 width=50 stretch=#true\n'
        "             line left=100 }",
    )
    text, rule = marks(printout)
    assert (text.box.height, rule.box.height) == (36, 36)


def test_a_declared_bottom_is_container_dependent_whatever_the_content(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="C" left=0 top=3 bottom=0 width=50 stretch=#true\n'
        "             line left=100 top=0 }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    text, rule, after = marks(printout)
    assert rule.box.height == 0, "the field takes no part in the first height"
    assert (text.box.y, text.box.height) == (3, 12)
    assert after.box.y == 15, "but its mark reaches the second"


def test_an_anchored_stretch_field_keeps_the_bands_box(tmp_path: Path) -> None:
    """A declared `bottom` sizes the box from the band; the text overflows.

    The band gives it no room at all here, and `valign="bottom"` puts
    all three lines above the box, as it does any text taller than its box.

    """
    printout = built(
        tmp_path,
        '    detail { field text="1\\n2\\n3" left=0 width=60 top=14 bottom=0 \\\n'
        '                   stretch=#true valign="bottom" }',
    )
    (text,) = marks(printout)
    assert isinstance(text, Text)
    assert (text.box.y, text.box.height, len(text.lines)) == (-22, 36, 3)


@pytest.mark.parametrize(
    ("clamp", "kept"),
    [("", 5), ("maxheight=30", 1)],
)
def test_a_clamp_is_what_lets_a_stretched_field_be_cut(
    tmp_path: Path, clamp: str, kept: int
) -> None:
    """The band gives the field 10; only a `maxheight` makes it cut to that."""
    printout = built(
        tmp_path,
        "    detail height=40 {\n"
        '      field text="1\\n2\\n3\\n4\\n5" left=0 width=40 top=0 bottom=30'
        " stretch=#true " + clamp + "\n"
        "    }",
    )
    (text,) = marks(printout)
    assert isinstance(text, Text)
    assert len(text.lines) == kept


def test_a_mark_past_the_bottom_edge_grows_the_band(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=20 { rectangle left=0 right=200 top=0 bottom=-5 }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    assert boxes(printout) == [(0, 0, 100, 25), (0, 25, 300, 5)]


def test_a_band_of_container_dependent_elements_alone_collapses(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        "    detail { line left=0 top=0 width=1 }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    rule, after = marks(printout)
    assert rule.box.height == 0
    assert after.box.y == 0


# -- fields -----------------------------------------------------------


def test_a_stretch_field_grows_to_its_text(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="one two three" left=0 top=0 width=30 stretch=#true }',
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Text)
    assert mark.lines == ("one", "two", "three")
    assert mark.box.height == 36


def test_a_field_without_stretch_truncates_at_a_line_boundary(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="one two three" left=0 top=0 width=30 height=24 }',
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Text)
    assert mark.lines == ("one", "two")


def test_a_box_too_short_for_one_line_still_draws_one(tmp_path: Path) -> None:
    printout = built(
        tmp_path, '    detail { field text="one" left=0 top=0 width=30 height=2 }'
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Text)
    assert mark.lines == ("one",)
    assert mark.box.height == 12


@pytest.mark.parametrize(
    ("valign", "top"), [("top", 0.0), ("center", 9.0), ("bottom", 18.0)]
)
def test_valign_places_the_content_inside_the_box(
    tmp_path: Path, valign: str, top: float
) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="A" left=0 top=0 width=50 height=30 '
        'valign="' + valign + '" }',
    )
    assert marks(printout)[0].box.y == top


def test_halign_supplies_the_alignment_and_align_wins(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=30 {\n"
        '      field text="A" left=0 top=0 width=50 halign="center"\n'
        '      field text="B" left=0 top=14 width=50 halign="center" align="left"\n'
        "    }",
    )
    first, second = marks(printout)
    assert isinstance(first, Text) and isinstance(second, Text)
    assert (first.align, second.align) == ("center", "left")


def test_a_text_mark_keeps_the_boxs_width(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    detail { field text="A" left=0 top=0 width=50 align="right" }',
    )
    assert marks(printout)[0].box.width == 50


def test_a_field_takes_its_text_from_a_data_node(tmp_path: Path) -> None:
    template = tmp_path / "blob.kdl"
    template.write_text(
        'report name="Blob" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        '  data "note" { content "from a blob" }\n'
        "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        '    detail { field data="note" left=0 top=0 width=200 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text(ONE_ROW, encoding="utf-8")
    printout = build(
        template, rows, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    mark = printout.pages[0].marks[0]
    assert isinstance(mark, Text)
    assert mark.lines == ("from a blob",)


# -- styles and printwhen ---------------------------------------------


def test_the_first_matching_style_supplies_the_formatting(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=20 {\n"
        '      style when="False" color="red"\n'
        '      style color="#123456"\n'
        '      style color="#999999"\n'
        '      field text="A" left=0 top=0 width=50\n'
        "    }",
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Text)
    assert mark.color == "#123456"


def test_an_unset_property_falls_through_to_the_next_match(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=20 {\n"
        '      style bgcolor="lime"\n'
        "      rectangle left=0 top=0 width=10 height=10\n"
        "    }",
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Rectangle)
    assert (mark.stroke, mark.fill) == ("#000000", "#00FF00")


def test_the_next_match_may_be_in_the_same_scope(tmp_path: Path) -> None:
    """A scope is not a unit of the walk: each property is first-win.

    The first style here sets no `bgcolor`, so the second supplies it,
    while its `color` is too late to replace the first one's.

    """
    printout = built(
        tmp_path,
        "    detail height=20 {\n"
        '      style color="#123456"\n'
        '      style bgcolor="lime" color="#999999"\n'
        "      rectangle left=0 top=0 height=10\n"
        "    }",
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Rectangle)
    assert (mark.stroke, mark.fill) == ("#123456", "#00FF00")


def test_an_elements_own_style_comes_first(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=20 {\n"
        '      style bgcolor="lime"\n'
        '      rectangle left=0 top=0 height=10 { style color="#123456"; }\n'
        "    }",
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Rectangle)
    assert (mark.stroke, mark.fill) == ("#123456", "#00FF00")


def test_a_rectangle_with_no_colour_at_all_draws_no_outline(tmp_path: Path) -> None:
    template = tmp_path / "plain.kdl"
    template.write_text(
        'report name="Plain" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        "  layout width=300 height=800 {\n"
        '    style font="body"\n'
        "    detail height=20 { rectangle left=0 top=0 width=10 height=10 }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text(ONE_ROW, encoding="utf-8")
    printout = build(
        template, rows, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    mark = printout.pages[0].marks[0]
    assert isinstance(mark, Rectangle)
    assert mark.stroke is None


def test_a_suppressed_element_neither_shows_nor_pushes(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail {\n"
        '      field text="gone" left=0 top=0 width=50 height=99 printwhen="False"\n'
        '      field text="here" left=0 top=0 width=50\n'
        "    }",
    )
    assert len(marks(printout)) == 1


def test_a_suppressed_band_contributes_nothing(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    title height=40 printwhen="False" { rectangle left=0 top=0 width=5 }\n'
        "    detail height=10 { rectangle left=0 top=0 width=5 height=5 }",
    )
    assert marks(printout)[0].box.y == 0


# -- floating elements ------------------------------------------------

# A field whose declared box is 0..12 and whose three lines make it 36.
GROWING = '      field text="1\\n2\\n3" left=0 top=0 height=12 width=40 stretch=#true\n'


def floated(tmp_path: Path, *elements: str) -> list[float]:
    """Return the tops of a detail band's marks after the floating pass.

    Args:
        tmp_path: Where to write the report.
        *elements: The band's elements after :data:`GROWING`, as KDL.

    """
    body = "    detail {\n" + GROWING + "".join(f"      {one}\n" for one in elements)
    return [box[1] for box in boxes(built(tmp_path, body + "    }"))]


def test_a_float_keeps_its_declared_gap_below_what_grew(tmp_path: Path) -> None:
    tops = floated(
        tmp_path,
        'field text="B" left=0 top=14 height=12 width=40 float=#true',
        'field text="C" left=0 top=30 height=12 width=40 float=#true',
        'field text="D" left=50 top=14 height=12 width=40',
    )
    assert tops == [0, 38, 54, 14], "D does not float and stays"


def test_the_horizontal_axis_plays_no_part(tmp_path: Path) -> None:
    tops = floated(
        tmp_path, 'field text="E" left=200 top=14 height=12 width=40 float=#true'
    )
    assert tops == [0, 38]


def test_the_gap_is_to_the_nearest_predecessor(tmp_path: Path) -> None:
    """Below two fields, the gap is to the lower declared bottom.

    The one above at 0..20 is nearer than the one at 0..12,
    so the gap is 10, added to the lowest edge either reached, 36.

    """
    tops = floated(
        tmp_path,
        'field text="1" left=50 top=0 height=20 width=40 stretch=#true',
        'field text="F" left=0 top=30 height=12 width=40 float=#true',
    )
    assert tops == [0, 0, 46]


def test_a_suppressed_element_is_not_a_predecessor(tmp_path: Path) -> None:
    tops = floated(
        tmp_path,
        'field text="S" left=0 top=14 height=12 width=40 float=#true printwhen="False"',
        'field text="T" left=0 top=30 height=12 width=40 float=#true',
    )
    assert tops == [0, 54]


def test_an_element_sized_from_the_band_is_not_a_predecessor(
    tmp_path: Path,
) -> None:
    """The rule's declared box is above F, but its height is the band's."""
    body = (
        "    detail height=30 {\n"
        '      field text="1" left=0 top=0 height=2 width=40 stretch=#true\n'
        "      line left=100 right=100 top=0 bottom=22\n"
        '      field text="F" left=0 top=10 height=12 width=40 float=#true\n'
        "    }"
    )
    rule, field = boxes(built(tmp_path, body))[1:]
    assert field[1] == 20, "a gap of 8 to the field, not 2 to the rule"
    assert rule[3] == 10, "and the rule spans the band the float made 32"


def test_an_earlier_float_precedes_one_it_overlaps(tmp_path: Path) -> None:
    """Floats are ordered by where they start, not by wholly above.

    T's gap is still measured to what is wholly above it, the field,
    and added to the edge U reached.

    """
    tops = floated(
        tmp_path,
        'field text="U" left=100 top=20 height=12 width=40 float=#true',
        'field text="T" left=0 top=30 height=12 width=40 float=#true',
    )
    assert tops == [0, 44, 74]


def test_a_float_of_no_height_keeps_its_gap(tmp_path: Path) -> None:
    """doc/'s rule, which the reference does not follow; see divergences.toml."""
    tops = floated(tmp_path, "line left=100 right=100 top=14 height=0 float=#true")
    assert tops == [0, 38]


def test_a_clamp_shortens_the_element_but_not_the_gap(tmp_path: Path) -> None:
    """Declared 6 and clamped to 5: the float keeps its 8 and adds it to 5."""
    body = (
        "    detail height=40 {\n"
        "      rectangle left=50 right=100 height=6 maxheight=5\n"
        "      rectangle left=50 right=100 top=14 height=20 float=#true\n"
        "    }"
    )
    assert [box[1] for box in boxes(built(tmp_path, body))] == [0, 13]


def test_a_level_float_of_no_declared_height_makes_the_gap_nothing(
    tmp_path: Path,
) -> None:
    """P2's declared box is 30..30: wholly above P1, but not before it."""
    body = (
        "    detail {\n"
        '      field text="x\\ny" left=0 right=30 top=30 height=6 stretch=#true'
        " float=#true\n"
        '      field text="1\\n2\\n3" right=10 width=60 top=30 stretch=#true'
        " float=#true\n"
        '      field text="z" left=120 width=40 height=12\n'
        "    }"
    )
    assert [box[1] for box in boxes(built(tmp_path, body))] == [12, 30, 0]


def test_with_nothing_above_the_gap_runs_to_the_highest_top(
    tmp_path: Path,
) -> None:
    """The rectangle starts at -5, so F's gap is 5 rather than 0."""
    body = (
        "    detail height=100 {\n"
        "      rectangle left=0 right=200 top=-5 height=45\n"
        '      field text="P" left=100 width=40 top=-1 height=6 float=#true\n'
        '      field text="F" left=200 width=40 top=0 height=12 float=#true\n'
        "    }"
    )
    assert [box[1] for box in boxes(built(tmp_path, body))] == [-5, -1, 10]


def test_a_float_with_no_predecessor_stays_where_it_was_declared(
    tmp_path: Path,
) -> None:
    body = (
        "    detail {\n"
        '      field text="U" left=100 top=20 height=12 width=40 float=#true\n'
        "    }"
    )
    assert boxes(built(tmp_path, body))[0][1] == 20


# -- xref -------------------------------------------------------------


def test_an_xref_holds_its_childrens_marks_in_page_coordinates(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        "    detail {\n"
        '      xref type="url" target="\'https://example.com\'" caption="\'A\'" \\\n'
        "           left=10 top=5 width=200 height=30 {\n"
        '        field text="inside" left=5 top=5 width=50\n'
        "        line left=100\n"
        "      }\n"
        "    }",
    )
    (link,) = marks(printout)
    assert isinstance(link, XrefMark)
    assert (link.link, link.target, link.caption) == (
        "url",
        "https://example.com",
        "A",
    )
    box = link.box
    assert (box.x, box.y, box.width, box.height) == (10, 5, 200, 30)
    text, rule = link.marks
    assert (text.box.x, text.box.y) == (15, 10)
    assert (rule.box.x, rule.box.y, rule.box.height) == (110, 5, 30)


def test_an_xref_does_not_grow_but_its_contents_grow_the_band(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        "    detail {\n"
        '      xref type="url" target="\'u\'" {\n'
        '        field text="1\\n2\\n3" left=0 width=50 stretch=#true\n'
        "      }\n"
        "      line left=100\n"
        "    }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    link, rule, after = marks(printout)
    assert link.box.height == 0, "an xref has no height of its own"
    assert rule.box.height == 0, "so the band's first height is zero"
    assert after.box.y == 36, "and its field's mark still reaches the second"


def test_an_xrefs_alignment_moves_nothing(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail {\n"
        '      xref type="url" target="\'u\'" right=0 width=200 height=20 \\\n'
        '           halign="right" valign="bottom" {\n'
        '        field text="end" width=50\n'
        "      }\n"
        "    }",
    )
    (link,) = marks(printout)
    assert isinstance(link, XrefMark)
    assert (link.marks[0].box.x, link.marks[0].box.y) == (100, 0)


@pytest.mark.parametrize(
    ("child", "reached"),
    [
        ("top=0 height=30", 30),
        ("top=0 bottom=-20", 12),
    ],
)
def test_an_xref_child_reaches_as_far_as_its_own_box(
    tmp_path: Path, child: str, reached: float
) -> None:
    """A box of its own counts in full; one from the xref, only its line."""
    printout = built(
        tmp_path,
        "    detail {\n"
        '      xref type="url" target="\'u\'" left=0 width=100 top=0 height=10 {\n'
        f'        field text="x" left=0 width=60 {child}\n'
        "      }\n"
        "      line left=200\n"
        "    }\n"
        "    summary height=5 { rectangle top=0 height=5 left=0 width=5 }",
    )
    _, rule, after = marks(printout)
    assert rule.box.height == 10, "the first maximum sees only the xref"
    assert after.box.y == reached


def test_an_xrefs_target_must_be_a_string(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="target must be a string"):
        built(tmp_path, '    detail { xref type="url" target="1" height=5 }')


# -- the font table ---------------------------------------------------


def test_the_font_table_lists_the_fonts_a_walk_resolved_to(
    tmp_path: Path,
) -> None:
    """`body` is shadowed and `never` never matches, so neither is used."""
    template = tmp_path / "fonts.kdl"
    template.write_text(
        'report name="Fonts" {\n'
        + "".join(
            f'  font "{name}" file="{REGULAR}" size={size}\n'
            for name, size in (("body", 10), ("big", 14), ("boxed", 12), ("never", 9))
        )
        + "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        "    detail height=40 {\n"
        '      style when="False" font="never"\n'
        '      field text="a" top=0 height=12 { style font="big"; }\n'
        '      rectangle top=20 height=5 { style font="boxed"; }\n'
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text(ONE_ROW, encoding="utf-8")
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    printout = build(template, rows, asked).printout
    assert sorted(one.name for one in printout.fonts) == ["big", "boxed"]


# -- the frame --------------------------------------------------------


def test_a_header_and_a_footer_are_reserved_out_of_the_frame(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        '    header height=20 { field text="H" left=0 top=0 width=50 }\n'
        '    footer height=20 { field text="F" left=0 top=0 width=50 }\n'
        '    detail height=10 { field text="D" left=0 top=0 width=50 }',
    )
    header, detail, footer = marks(printout)
    assert header.box.y == 0
    assert detail.box.y == 20
    assert footer.box.y == 780


def test_the_bands_come_out_in_reading_order(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    header height=12 { field text="header" left=0 top=0 width=90 }\n'
        '    title height=12 { field text="title" left=0 top=0 width=90 }\n'
        '    footer height=12 { field text="footer" left=0 top=0 width=90 }\n'
        '    detail height=12 { field text="detail" left=0 top=0 width=90 }\n'
        '    summary height=12 { field text="summary" left=0 top=0 width=90 }',
    )
    said = [one.lines[0] for one in marks(printout) if isinstance(one, Text)]
    assert said == ["header", "title", "detail", "summary", "footer"]


def test_a_report_over_no_records_prints_its_other_bands(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    title height=12 { field text="title" left=0 top=0 width=90 }\n'
        '    detail height=12 { field text="detail" left=0 top=0 width=90 }',
        data=None,
    )
    said = [one.lines[0] for one in marks(printout) if isinstance(one, Text)]
    assert said == ["title"]


# -- the record loop --------------------------------------------------


def test_the_counters_count_what_has_been_printed(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=12 {\n"
        "      field expr=\"'%d %d %d' % (ITEM_NUMBER, DATA_COUNT, REPORT_COUNT)\""
        " left=0 top=0 width=200\n"
        "    }",
        data=ONE_ROW + '{"n":2}\n',
    )
    said = [one.lines[0] for one in marks(printout) if isinstance(one, Text)]
    assert said == ["1 2 0", "2 2 1"]


def test_a_variable_folds_per_record_and_the_summary_reads_the_total(
    tmp_path: Path,
) -> None:
    template = tmp_path / "total.kdl"
    template.write_text(
        'report name="Total" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        '  records { member "n" type="int" }\n'
        '  variable "total" expr="n" calc="sum"\n'
        "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        "    detail height=12 {\n"
        "      field expr=\"'%d' % total\" left=0 top=0 width=200\n"
        "    }\n"
        "    summary height=12 {\n"
        "      field expr=\"'total %d' % total\" left=0 top=0 width=200\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text('{"n":2}\n{"n":3}\n', encoding="utf-8")
    printout = build(
        template, rows, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    said = [one.lines[0] for one in printout.pages[0].marks if isinstance(one, Text)]
    assert said == ["2", "5", "total 5"]


# -- what this milestone does not do ----------------------------------


@pytest.mark.parametrize(
    ("body", "milestone"),
    [
        ('    detail height=10 { barcode type="Code128" text="1" }', "M10"),
        ('    detail height=10 { image file="x.png" }', "M11"),
        ('    detail height=10 { eject type="page" }', "M8"),
        ("    columns count=2\n    detail height=10", "M8"),
        ('    group "g" expr="1" { detail height=10 }', "M8"),
    ],
)
def test_what_a_later_milestone_brings_says_so(
    tmp_path: Path, body: str, milestone: str
) -> None:
    with pytest.raises(Unsupported) as refused:
        built(tmp_path, body)
    assert milestone in str(refused.value)


def test_a_band_that_does_not_fit_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(BuildError) as refused:
        built(tmp_path, "    detail height=900 { rectangle left=0 top=0 width=5 }")
    assert "900" in str(refused.value)


def test_a_line_mark_carries_the_style_colour(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    detail height=10 { line left=0 top=0 width=2 dash="dot" backslant=#true }',
    )
    mark = marks(printout)[0]
    assert isinstance(mark, Line)
    assert (mark.width, mark.dash, mark.color, mark.backslant) == (
        2,
        "dot",
        "#000000",
        True,
    )


def test_a_suppressed_detail_does_not_fold_but_advances_the_item(
    tmp_path: Path,
) -> None:
    """doc/expressions.md#ordering-against-section-printing, both halves.

    The middle record's detail is suppressed, so its value is not folded
    into the total; `ITEM_NUMBER` counts it all the same.

    """
    template = tmp_path / "skip.kdl"
    template.write_text(
        'report name="Skip" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        '  records { member "n" type="int" }\n'
        '  variable "total" expr="n" calc="sum"\n'
        "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        '    detail height=12 printwhen="n != 2" {\n'
        "      field expr=\"'%d of %d' % (ITEM_NUMBER, total)\" left=0 top=0 "
        "width=200\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text('{"n":1}\n{"n":2}\n{"n":4}\n', encoding="utf-8")
    printout = build(
        template, rows, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    said = [one.lines[0] for one in printout.pages[0].marks if isinstance(one, Text)]
    assert said == ["1 of 1", "3 of 5"]


# -- reserved bands ---------------------------------------------------


def test_a_header_may_read_a_variable(tmp_path: Path) -> None:
    """Reserving space measures the band, so the names must be there.

    doc/expressions.md#the-report-boundary fires `iter="report"`
    before the title is built; a variable a header reads has to
    exist by then, seeded or not.

    """
    template = tmp_path / "early.kdl"
    template.write_text(
        'report name="Early" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        '  records { member "n" type="int" }\n'
        '  variable "total" expr="n" calc="sum"\n'
        "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        "    header height=12 {\n"
        "      field expr=\"'seen %s' % total\" left=0 top=0 width=200\n"
        "    }\n"
        "    footer height=12 {\n"
        "      field expr=\"'total %s' % total\" left=0 top=0 width=200\n"
        "    }\n"
        '    detail height=12 { field expr="n" left=0 top=0 width=200 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    data = tmp_path / "rows.jsonl"
    data.write_text('{"n":2}\n{"n":3}\n', encoding="utf-8")
    printout = build(
        template, data, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    said = [one.lines[0] for one in printout.pages[0].marks if isinstance(one, Text)]
    assert said == ["seen None", "2", "3", "total 5"]


def test_a_footer_guarded_on_a_record_still_lands_on_the_page(
    tmp_path: Path,
) -> None:
    """The spec's own guard reserves nothing and prints anyway.

    `printwhen="THIS != None"` is false when the frame reserves space,
    because no record has been read, and true when the footer is built
    at the end of the page.  Its bottom edge is the page frame's,
    so it is on the page either way.

    """
    printout = built(
        tmp_path,
        '    footer height=14 printwhen="THIS != None" {\n'
        '      field text="foot" left=0 top=0 width=90\n'
        "    }\n"
        '    detail height=12 { field text="row" left=0 top=0 width=90 }',
    )
    foot = marks(printout)[-1]
    assert isinstance(foot, Text)
    assert foot.lines == ("foot",)
    # The band is 14 tall and ends at the page frame's bottom, so it
    # starts at 786; its one line of 12 is drawn at the band's top.
    assert foot.box.y == 786


def test_a_footer_reads_the_position_the_content_reached(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    footer height=20 {\n"
        "      field expr=\"'vp %s vs %s' % (VERTICAL_POSITION, VERTICAL_SPACE)\""
        " left=0 top=0 width=200\n"
        "    }\n"
        '    detail height=26 { field text="row" left=0 top=0 width=90 }',
    )
    foot = marks(printout)[-1]
    assert isinstance(foot, Text)
    assert foot.lines == ("vp 26.0 vs 20.0",)


# -- variable scopes --------------------------------------------------


def test_a_detail_scoped_reset_clears_the_accumulator(tmp_path: Path) -> None:
    template = tmp_path / "perrow.kdl"
    template.write_text(
        'report name="PerRow" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        '  records { member "n" type="int" }\n'
        '  variable "row" expr="n" calc="sum" reset="detail"\n'
        '  variable "whole" expr="n" calc="sum"\n'
        "  layout width=300 height=800 {\n"
        '    style font="body" color="black"\n'
        "    detail height=12 {\n"
        "      field expr=\"'%s of %s' % (row, whole)\" left=0 top=0 width=200\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    data = tmp_path / "rows.jsonl"
    data.write_text('{"n":2}\n{"n":3}\n{"n":4}\n', encoding="utf-8")
    printout = build(
        template, data, Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    ).printout
    said = [one.lines[0] for one in printout.pages[0].marks if isinstance(one, Text)]
    assert said == ["2 of 2", "3 of 5", "4 of 9"]


# -- overflow ---------------------------------------------------------


def test_a_band_taller_than_the_page_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BuildError) as refused:
        built(tmp_path, '    detail height=900 { field text="A" left=0 top=0 width=5 }')
    assert "900" in str(refused.value)


def test_allow_overflow_makes_that_a_warning_and_places_the_band(
    tmp_path: Path,
) -> None:
    """doc/layout.md#errors: the marks go down and the header says so.

    The warning travels in the printout, which is what makes
    an overflowing document identifiable from the artifact
    rather than from whoever watched the build.

    """
    printout = built(
        tmp_path,
        '    detail height=900 { field text="A" left=0 top=0 width=5 }',
        allow_overflow=True,
    )
    assert len(marks(printout)) == 1
    kinds = [one.kind for one in printout.warnings]
    assert kinds == ["overflow"]
    assert printout.warnings[0].record == 0
