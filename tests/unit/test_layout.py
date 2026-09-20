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
        ('    detail height=10 { xref type="url" target="\'u\'" }', "M13"),
        ("    detail height=10 { rectangle float=#true top=0 height=1 }", "M7"),
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
