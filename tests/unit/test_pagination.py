"""Pagination: frames, placing a band, ejects, groups, and keeping together.

The differential suite holds all of this to the reference over the
`pagination/` probes.  What is here is each rule of doc/layout.md
on its own, so that a change breaks the test named after the sentence
it broke, and the rules where this engine and the reference part company
are tested for this engine's answer.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.errors import BuildError
from sr.layout.frame import Frame
from sr.layout.loop import Keys
from sr.layout.measure import Extent, Measurement, Measurer
from sr.layout.place import choose, is_cut, is_legal, split
from sr.printout.model import Box, Page, Printout, Rectangle, Text

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

# A page 200 by 100 with 5pt margins, which leaves a frame 90pt tall.
# Size 5 sets a line 6pt tall, which keeps the arithmetic whole.
HEAD = """
report name="Pages" {
  font "body" file="FACE" size=5
MEMBERS
  layout width=200 height=100 leftmargin=5 rightmargin=5 topmargin=5 \\
         bottommargin=5 {
    style font="body" color="black"
BANDS
  }
}
"""

# The records the group tests read: one member, `a`, the group's key.
KEYED = """  records {
    member "a" type="int"
  }
"""

# The group the group tests use, keyed on `a`, and a total it resets.
GROUP = (
    KEYED
    + """  variable "total" expr="1" calc="sum" reset="group" resetgrp="A"
"""
)


def built(
    tmp_path: Path,
    bands: str,
    rows: list[dict[str, Any]] | None = None,
    *,
    members: str = "",
    **options: Any,
) -> Printout:
    """Build a report and return its printout.

    Args:
        tmp_path: Where to write the template and the data.
        bands: What goes under `layout`, after its style.
        rows: The records, or ``None`` for one record nothing reads.
        members: What goes between the font and the layout.
        **options: What to pass to the build.

    """
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("MEMBERS", members)
    template.write_bytes(text.replace("BANDS", bands).encode("utf-8"))
    data = tmp_path / "rows.jsonl"
    lines = [json.dumps(row) for row in ([{"n": 1}] if rows is None else rows)]
    data.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True, **options)
    return build(template, data, asked).printout


def said(expr: str, width: int = 100) -> str:
    """Return a one-line field that shows an expression.

    Args:
        expr: The expression, with no double quotes in it.
        width: The field's width.

    """
    return f'field expr="{expr}" left=0 top=0 width={width} height=6'


def shown(text: str) -> str:
    """Return a one-line field that shows fixed text."""
    return f'field text="{text}" left=0 top=0 height=6'


def band(kind: str, body: str, props: str = "") -> str:
    """Return a band, indented under `layout`.

    Args:
        kind: The band's node name.
        body: What goes inside it.
        props: Its properties.

    """
    return f"    {kind} {props} {{ {body} }}\n"


def rows_of(count: int, **fields: Any) -> list[dict[str, Any]]:
    """Return ``count`` records carrying these fields."""
    return [dict(fields) for _ in range(count)]


def texts(page: Page) -> list[str]:
    """Return the first line of every text mark on a page, in paint order."""
    return [mark.lines[0] for mark in page.marks if isinstance(mark, Text)]


def pages(printout: Printout) -> list[list[str]]:
    """Return every page's text, a list per page."""
    return [texts(page) for page in printout.pages]


def at(page: Page, first: str) -> Box:
    """Return the box of the text mark whose first line is ``first``."""
    for mark in page.marks:
        if isinstance(mark, Text) and mark.lines[0] == first:
            return mark.box
    raise AssertionError(f"no text mark says {first!r}")


ROW = band("detail", said("'r%d' % ITEM_NUMBER"), "height=20")
TOP = band("title", "rectangle left=0 top=0 width=1 height=1", "height=HH")


def title(height: int) -> str:
    """Return a report title of this height, with next to nothing in it."""
    return TOP.replace("HH", str(height))


# -- placing a band ---------------------------------------------------


def test_a_band_that_does_not_fit_starts_the_next_page(tmp_path: Path) -> None:
    printout = built(tmp_path, ROW, rows_of(5))
    assert pages(printout) == [["r1", "r2", "r3", "r4"], ["r5"]]
    assert at(printout.pages[1], "r5").y == 5


def test_the_footer_sees_the_record_that_did_not_fit(tmp_path: Path) -> None:
    """doc/layout.md#what-a-header-or-footer-sees: the outgoing context.

    The fifth record is `THIS` when the first page's footer is built,
    and its fold was rolled back first, so the page total is the page's.

    """
    footer = said("'F %d %s %d' % (ITEM_NUMBER, pt, PAGE_COUNT)")
    detail = said("'r%d %s' % (ITEM_NUMBER, pt)")
    printout = built(
        tmp_path,
        band("footer", footer, "height=6") + band("detail", detail, "height=20"),
        rows_of(5),
        members='  variable "pt" expr="1" calc="sum" reset="page"',
    )
    first, second = printout.pages
    assert texts(first)[-1] == "F 5 4 4"
    assert texts(second) == ["r5 1", "F 5 1 1"]


def test_every_page_has_its_header_and_footer(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        band("header", said("'H%d' % PAGE_NUMBER"), "height=6")
        + band("footer", said("'F%d' % PAGE_NUMBER"), "height=6")
        + ROW,
        rows_of(4),
    )
    assert pages(printout) == [["H1", "r1", "r2", "r3", "F1"], ["H2", "r4", "F2"]]
    assert at(printout.pages[1], "F2").y == 89


def test_a_reservation_is_measured_again_on_every_page(tmp_path: Path) -> None:
    header = (
        "field expr=#\"'H' + '\\nH' * (PAGE_NUMBER - 1)\"#"
        " left=0 top=0 width=100 stretch=#true"
    )
    printout = built(tmp_path, band("header", header) + ROW, rows_of(8))
    assert at(printout.pages[0], "r1").y == 11
    assert at(printout.pages[1], "r5").y == 17


# -- splitting --------------------------------------------------------


def lines(count: int) -> str:
    """Return a stretch field of ``count`` lines, a1 onward."""
    text = "\\n".join(f"a{index}" for index in range(1, count + 1))
    return f'field text="{text}" stretch=#true left=0 top=0 width=100'


def test_a_band_splits_at_the_greatest_legal_point(tmp_path: Path) -> None:
    field = lines(8) + ' align="justified"'
    printout = built(tmp_path, title(60) + band("detail", field, "split=#true"))
    head = printout.pages[0].marks[-1]
    tail = printout.pages[1].marks[0]
    assert isinstance(head, Text) and isinstance(tail, Text)
    assert head.lines == ("a1", "a2", "a3", "a4", "a5")
    assert head.last_line_justified
    assert tail.lines == ("a6", "a7", "a8")
    assert not tail.last_line_justified
    assert tail.box.y == 5


def test_orphans_and_widows_can_leave_no_split_point(tmp_path: Path) -> None:
    """A field with fewer lines than both together cannot be cut.

    The band fits an empty frame, so it is ejected whole.

    """
    props = "split=#true orphans=5 widows=4"
    printout = built(tmp_path, title(60) + band("detail", lines(8), props))
    moved = printout.pages[1].marks[0]
    assert isinstance(moved, Text) and len(moved.lines) == 8


def test_a_band_too_tall_for_any_frame_is_cut_where_it_is(tmp_path: Path) -> None:
    """doc/layout.md#placing-a-band: the last branch tests the frame as it is.

    Twenty lines are 120pt against a frame of 90, and the preferences
    leave no legal split point.  The cut is on the first page, after the
    five lines that fit below the title, not on a page ejected to first.

    """
    props = "split=#true orphans=11 widows=11"
    printout = built(tmp_path, title(60) + band("detail", lines(20), props))
    head = printout.pages[0].marks[-1]
    assert isinstance(head, Text) and len(head.lines) == 5


def test_a_band_that_fits_no_frame_and_cannot_be_cut_overflows(
    tmp_path: Path,
) -> None:
    body = "rectangle left=0 top=0 width=1 height=100"
    with pytest.raises(BuildError, match="and it cannot be cut"):
        built(tmp_path, band("detail", body, "height=10 split=#true"))


def test_an_allowed_overflow_starts_a_frame_and_names_record_zero(
    tmp_path: Path,
) -> None:
    body = "rectangle left=0 top=0 width=1 height=100"
    printout = built(
        tmp_path,
        title(10) + band("detail", body, "height=10"),
        allow_overflow=True,
    )
    assert len(printout.pages) == 2
    assert printout.pages[1].marks[0].box.y == 5
    (warning,) = printout.warnings
    assert (warning.kind, warning.record) == ("overflow", 0)


FIRST_HEADER = band("header", shown("H"), 'height=50 printwhen="PAGE_NUMBER == 1"')


def test_a_band_that_fits_only_a_later_page_moves_there(tmp_path: Path) -> None:
    """doc/layout.md#placing-a-band: a band is carried once before it overflows.

    The first page's header leaves 40 of its 90, and the detail needs
    60 and cannot be cut.  The second page, with no header, holds it.

    """
    printout = built(tmp_path, FIRST_HEADER + band("detail", shown("d"), "height=60"))
    assert pages(printout) == [["H"], ["d"]]


def test_a_band_that_fits_only_a_later_page_moves_there_after_others(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        FIRST_HEADER
        + band("detail", shown("d"), "height=6")
        + band("summary", shown("S"), "height=60"),
    )
    assert pages(printout) == [["H", "d"], ["S"]]


@pytest.mark.parametrize("tall", [1, 2], ids=["empty-column", "filled-column"])
def test_a_band_that_fits_only_a_later_page_skips_the_columns_left(
    tmp_path: Path, tall: int
) -> None:
    """doc/layout.md#placing-a-band: the carry is a page eject.

    Two columns, 40 tall on the first page.  The tall row needs 60, and
    the second column offers what the first does, whether or not the
    first has a row in it: the row goes to the first column of the next
    page, where there is room, rather than overflowing in the second.

    """
    wall = (
        f'rectangle printwhen="ITEM_NUMBER == {tall}" left=50 top=0 width=1 height=60'
    )
    detail = band("detail", said("'r%d' % ITEM_NUMBER") + "; " + wall, "height=6")
    printout = built(tmp_path, FIRST_HEADER + COLUMNS + detail, rows_of(tall))
    moved = at(printout.pages[1], f"r{tall}")
    assert pages(printout)[0] == ["H"] + [f"r{row}" for row in range(1, tall)]
    assert (moved.x, moved.y) == (5, 5)


def test_no_column_below_a_swapped_title_is_empty(tmp_path: Path) -> None:
    """doc/layout.md#extent-and-fill: the title is in the way, like a floor.

    The title takes 50 of the first page's 90.  Ten lines that may split,
    but at no legal point, need 60: a page without the title holds them,
    so they go to the second page whole rather than being cut on the first.

    """
    props = "split=#true orphans=11 widows=11"
    printout = built(
        tmp_path,
        band("title", shown("T"), "height=50 swapheader=#true")
        + band("detail", lines(10), props),
    )
    assert pages(printout) == [["T"], ["a1"]]
    moved = printout.pages[1].marks[0]
    assert isinstance(moved, Text) and len(moved.lines) == 10


def test_a_band_below_a_swapped_title_is_judged_against_a_page_without_it(
    tmp_path: Path,
) -> None:
    body = band("title", shown("T"), "height=50 swapheader=#true") + band(
        "detail", shown("d"), "height=95"
    )
    with pytest.raises(BuildError, match="the largest frame offers 90 pt"):
        built(tmp_path, body)


# -- eject nodes ------------------------------------------------------


def test_an_eject_node_ejects_even_from_an_empty_page(tmp_path: Path) -> None:
    body = 'eject type="page"; ' + shown("d")
    printout = built(tmp_path, band("detail", body, "height=10"))
    assert pages(printout) == [[], ["d"]]


def test_an_eject_node_with_require_ejects_only_when_short(
    tmp_path: Path,
) -> None:
    body = "eject require=35; " + said("'r%d' % ITEM_NUMBER")
    printout = built(tmp_path, band("detail", body, "height=20"), rows_of(4))
    assert pages(printout) == [["r1", "r2", "r3"], ["r4"]]


def test_a_report_title_tests_its_ejects_after_it(tmp_path: Path) -> None:
    printout = built(tmp_path, band("title", "eject; " + shown("T")) + ROW)
    assert pages(printout) == [["T"], ["r1"]]


def test_eject_require_does_not_eject_from_an_empty_column(
    tmp_path: Path,
) -> None:
    """doc/layout.md#group-minrows-and-mintailrows: no eject gives more room.

    The first row asks for more than any page has, at the top of one.
    The second asks for it below the first, and gets the next page.

    """
    body = "eject require=200; " + shown("d")
    printout = built(tmp_path, band("detail", body, "height=10"), rows_of(2))
    assert pages(printout) == [["d"], ["d"]]


HIDDEN = 'printwhen="False"'


# -- columns ----------------------------------------------------------


COLUMNS = "    columns count=2 gap=10\n"


def test_a_band_that_does_not_fit_moves_to_the_next_column(
    tmp_path: Path,
) -> None:
    detail = said("'r%d %d' % (ITEM_NUMBER, COLUMN_NUMBER)")
    printout = built(
        tmp_path, COLUMNS + band("detail", detail, "height=20"), rows_of(9)
    )
    first, second = printout.pages
    assert texts(first)[3:5] == ["r4 1", "r5 2"]
    assert (at(first, "r5 2").x, at(first, "r5 2").y) == (105, 5)
    assert texts(second) == ["r9 1"]


def test_a_band_across_columns_goes_below_the_deepest(tmp_path: Path) -> None:
    detail = 'eject type="column" when="ITEM_NUMBER == 4"; ' + said(
        "'r%d' % ITEM_NUMBER"
    )
    printout = built(
        tmp_path,
        COLUMNS
        + band("detail", detail, "height=10")
        + band("summary", shown("S"), "height=6"),
        rows_of(4),
    )
    page = printout.pages[0]
    assert at(page, "r4").x == 105
    assert (at(page, "S").y, at(page, "S").width) == (35, 190)


def test_a_column_opened_later_begins_below_a_band_across_it(
    tmp_path: Path,
) -> None:
    """doc/layout.md#extent-and-fill: the floor a band across columns leaves.

    The title spans both columns, so the second column begins below it
    rather than at the frame's top, where the title is.

    """
    printout = built(
        tmp_path,
        band("title", shown("T"), "height=10") + COLUMNS + ROW,
        rows_of(6),
    )
    page = printout.pages[0]
    assert (at(page, "r5").x, at(page, "r5").y) == (105, 15)


def test_a_band_under_a_band_across_the_columns_moves_to_the_next_page(
    tmp_path: Path,
) -> None:
    """doc/layout.md#placing-a-band: a column below its floor is not empty.

    The first column has no room below the title for the row, which an
    empty column would have, so the row ejects rather than overflowing.
    The second column begins as low as the first, so the eject passes
    over it to the next page, and it never opens on the first.

    """
    footer = band("footer", said("'CF%d' % COLUMN_NUMBER"), "height=6")
    printout = built(
        tmp_path,
        band("title", shown("T"), "height=40")
        + "    columns count=2 gap=10 {\n  "
        + footer
        + "    }\n"
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=60"),
    )
    first, second = printout.pages
    assert texts(first) == ["T", "CF1"]
    assert (at(second, "r1").x, at(second, "r1").y) == (5, 5)


@pytest.mark.parametrize(
    ("require", "where"),
    [("", (0, 105, 45)), (" require=60", (1, 5, 5))],
)
def test_an_eject_node_passes_over_a_column_only_to_find_room(
    tmp_path: Path, require: str, where: tuple[int, int, int]
) -> None:
    """An `eject` node without `require` is an instruction, not a search.

    Below a title across both columns, one moves the row to the second
    column, although it begins as low as the first.  One that requires
    more room than the first column has left goes to the next page,
    since the second has no more.

    """
    eject = f'eject type="column"{require} when="ITEM_NUMBER == 1"; '
    printout = built(
        tmp_path,
        band("title", shown("T"), "height=40")
        + COLUMNS
        + band("detail", eject + said("'r%d' % ITEM_NUMBER"), "height=20"),
    )
    page, left, down = where
    box = at(printout.pages[page], "r1")
    assert (box.x, box.y) == (left, down)


def test_an_empty_frame_is_measured_below_the_column_headers(
    tmp_path: Path,
) -> None:
    """A band across the columns never gets the room their headers take.

    The summary is taller than the 80pt a page leaves below the column
    header, though not than the page frame's 90, so it overflows rather
    than ejecting from page to page in search of the 90.

    """
    header = band("header", shown("CH"), "height=10")
    bands = (
        "    columns count=1 {\n  "
        + header
        + "    }\n"
        + ROW
        + band("summary", shown("S"), "height=85")
    )
    with pytest.raises(BuildError, match="the largest frame offers 80 pt"):
        built(tmp_path, bands)
    printout = built(tmp_path, bands, allow_overflow=True)
    second = printout.pages[1]
    assert texts(second) == ["CH", "S"]
    assert at(second, "S").y == 15


def test_an_empty_frame_begins_below_the_headers_as_drawn(
    tmp_path: Path,
) -> None:
    """doc/layout.md#extent-and-fill: a header can draw more than it reserved.

    The column header is reserved against the whole column, where
    it does not print, and built against the column less its footer,
    where it does: it reserves nothing and draws 20.  A summary across
    the columns goes below it, and has 60 between it and the footer.
    Measured from the reservation, an 80pt summary would fit an empty page,
    and would be ejected from page to page for ever.

    """
    header = band("header", shown("CH"), 'height=20 printwhen="VERTICAL_SPACE < 85"')
    footer = band("footer", shown("CF"), "height=10")
    columns = "    columns count=2 gap=10 {\n  " + header + "  " + footer + "    }\n"
    quiet = band("detail", shown("d"), "height=6 " + HIDDEN)
    printout = built(
        tmp_path, columns + quiet + band("summary", shown("S"), "height=60")
    )
    assert at(printout.pages[0], "S").y == 25
    with pytest.raises(BuildError, match="the largest frame offers 60 pt"):
        built(tmp_path, columns + quiet + band("summary", shown("S"), "height=80"))


def test_a_band_across_columns_stops_above_their_footers(tmp_path: Path) -> None:
    """doc/layout.md#extent-and-fill: the footers are drawn under it.

    Seven rows fill the column to 75, and its footer takes the last 10.
    The summary needs 15, which the page frame has below the rows but not
    above the footer, so it starts the next page, and `VERTICAL_SPACE`
    there is measured to the footer.

    """
    footer = band("footer", shown("CF"), "height=10")
    columns = "    columns count=2 gap=10 {\n  " + footer + "    }\n"
    summary = band("summary", said("'S %s' % VERTICAL_SPACE"), "height=15")
    detail = band("detail", said("'r%d' % ITEM_NUMBER"), "height=10")
    first, second = pages(built(tmp_path, columns + detail + summary, rows_of(7)))
    assert first[-2:] == ["r7", "CF"]
    assert second == ["S 80.0", "CF"]


def test_column_counters_reset_at_each_column(tmp_path: Path) -> None:
    footer = band("footer", said("'CF %d %d' % (COLUMN_NUMBER, COLUMN_COUNT)"))
    columns = "    columns count=2 gap=10 {\n  " + footer + "    }\n"
    printout = built(tmp_path, columns + ROW, rows_of(6))
    assert "CF 1 4" in texts(printout.pages[0])
    assert "CF 2 2" in texts(printout.pages[0])


def test_the_first_page_and_column_start_from_init(tmp_path: Path) -> None:
    """doc/expressions.md#iter-and-reset: the report's start is a reset.

    A page total and a column total seeded by `init` read it on the
    first page and in the first column, as they do on every later one.

    """
    members = (
        '  variable "pt" expr="1" init="100" calc="sum" reset="page"\n'
        '  variable "ct" expr="1" init="100" calc="sum" reset="column"\n'
    )
    detail = said("'r%d %d %d' % (ITEM_NUMBER, pt, ct)")
    printout = built(
        tmp_path,
        band("header", said("'H %d %d' % (pt, ct)"), "height=6")
        + COLUMNS
        + band("detail", detail, "height=20"),
        rows_of(9),
        members=members,
    )
    first, second = pages(printout)
    assert first[:2] == ["H 100 100", "r1 101 101"]
    assert first[5] == "r5 105 101"
    assert second == ["H 100 100", "r9 101 101"]


def test_balancing_spreads_a_page_over_its_columns(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    columns count=2 gap=10 balance=#true\n"
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=10")
        + band("summary", shown("S"), "height=6"),
        rows_of(5),
    )
    page = printout.pages[0]
    lefts = [at(page, f"r{index}").x for index in range(1, 6)]
    assert lefts == [5, 5, 5, 105, 105]
    assert at(page, "r4").y == 5
    assert at(page, "S").y == 35


def test_a_page_is_balanced_once_when_the_summary_moves_on(
    tmp_path: Path,
) -> None:
    """The pass before the summary is the page's, even where the summary ejects.

    Twelve rows balance into two columns of six, and the summary is
    too tall for what is left, so it takes the next page.  The page it
    leaves is not balanced a second time, which would move the rows again.

    """
    printout = built(
        tmp_path,
        "    columns count=2 gap=10 balance=#true\n"
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=10")
        + band("summary", shown("S"), "height=60"),
        rows_of(12),
    )
    first, second = printout.pages
    assert (at(first, "r6").x, at(first, "r6").y) == (5, 55)
    assert (at(first, "r7").x, at(first, "r7").y) == (105, 5)
    assert texts(second) == ["S"]


# -- groups -----------------------------------------------------------


def group(*bands: str, props: str = "") -> str:
    """Return group "A", keyed on `a`, holding these bands."""
    inner = "".join("  " + one for one in bands)
    return f'    group "A" expr="a" {props} {{\n{inner}    }}\n'


def keyed(*keys: int) -> list[dict[str, Any]]:
    """Return one record per key."""
    return [{"a": key} for key in keys]


@pytest.mark.parametrize(
    "bands",
    [
        band("title", "eject; " + shown("T"), "swapheader=#true " + HIDDEN) + ROW,
        band("title", "eject; " + shown("T"), HIDDEN) + ROW,
        group(band("title", "eject; " + shown("T"), HIDDEN), ROW),
        band("title", shown("T")) + band("detail", "eject; " + shown("d"), HIDDEN),
        ROW + band("summary", "eject; " + shown("S"), HIDDEN),
    ],
    ids=["swapped-title", "title", "group-title", "detail", "summary"],
)
def test_a_band_that_does_not_print_tests_no_eject_nodes(
    tmp_path: Path, bands: str
) -> None:
    """doc/layout.md#eject-nodes: `printwhen` suppresses them with the band."""
    printout = built(tmp_path, bands, keyed(1), members=KEYED)
    assert len(printout.pages) == 1


def test_a_group_summary_is_built_against_the_previous_record(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        group(
            band("title", said("'T%d %d' % (a, A_COUNT)"), "height=6"),
            band("summary", said("'S%d %d %d' % (a, ITEM_NUMBER, total)")),
            band("detail", shown("d"), "height=6"),
        ),
        keyed(1, 1, 2),
        members=GROUP,
    )
    assert texts(printout.pages[0]) == [
        "T1 0",
        "d",
        "d",
        "S1 2 2",
        "T2 0",
        "d",
        "S2 3 1",
    ]


def test_the_header_counts_group_runs_and_keys(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        group(band("detail", shown("d"), "height=6")),
        keyed(1, 2, 1),
        members=GROUP,
    )
    assert (printout.group_runs, printout.group_keys) == ({"A": 3}, {"A": 2})


def test_no_group_is_listed_before_it_opens(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        group(band("detail", shown("d"), "height=6")),
        [],
        members=GROUP,
    )
    assert printout.group_runs == {}


def test_a_groups_names_hold_their_start_before_its_first_run(
    tmp_path: Path,
) -> None:
    """doc/expressions.md#between-a-groups-runs: before the first run.

    The report's title is built before any record, and reads the group's
    counters at 0 and 1, and its total at the `init` it was seeded with.

    """
    title_face = said("'T %d %d %s' % (A_COUNT, A_PAGE_NUMBER, total)")
    printout = built(
        tmp_path,
        band("title", title_face, "height=6")
        + group(band("detail", said("'d %s' % total"), "height=6")),
        keyed(1, 1),
        members=GROUP.replace('calc="sum"', 'init="100" calc="sum"'),
    )
    assert pages(printout)[0][:2] == ["T 0 1 100", "d 101"]


def test_a_group_page_number_counts_from_its_title(tmp_path: Path) -> None:
    """doc/layout.md#sequence: a group still opening is not advanced.

    The second group's title does not fit the first page,
    so the group begins on the second, and reads 1 there.

    """
    printout = built(
        tmp_path,
        group(
            band("title", said("'T%d %d' % (a, A_PAGE_NUMBER)"), "height=20"),
            band("detail", said("'d %d' % A_PAGE_NUMBER"), "height=20"),
        ),
        keyed(1, 1, 1, 2, 2),
        members=GROUP,
    )
    assert pages(printout) == [["T1 1", "d 1", "d 1", "d 1"], ["T2 1", "d 1", "d 1"]]


def test_a_title_that_moves_takes_its_fold_with_it(tmp_path: Path) -> None:
    seen = '  variable "seen" expr="a" calc="list" iter="group" itergrp="A"'
    printout = built(
        tmp_path,
        group(
            band("title", said("'T %s' % seen"), "height=20"),
            band("detail", shown("d"), "height=20"),
        ),
        keyed(1, 1, 1, 2, 2),
        members=GROUP + seen + ' reset="page"\n',
    )
    assert texts(printout.pages[1])[0] == "T [2]"


def edge(kind: str) -> str:
    """Return a header or footer that shows what it sees of group A.

    The record's number and key, the group's count, and the total
    the group resets, after the band's initial.

    Args:
        kind: ``header`` or ``footer``.

    """
    face = (
        f"'{kind[0].upper()} %d %s %d %s' % "
        "(ITEM_NUMBER, THIS.a if THIS else None, A_COUNT, total)"
    )
    return band(kind, said(face), "height=6")


def test_a_page_ending_at_a_group_break_shows_both_sides_of_it(
    tmp_path: Path,
) -> None:
    """doc/layout.md#what-a-header-or-footer-sees: at a group break.

    The second group does not fit what the first leaves, and
    `keeptogether` moves it on.  The first page's footer reads the group
    that ended there, with its last record, its count, and its total;
    the second page's header reads the group that begins on it.

    """
    printout = built(
        tmp_path,
        edge("header")
        + edge("footer")
        + group(
            band("title", said("'T%d' % a"), "height=10"),
            band("detail", shown("d"), "height=10"),
            props="keeptogether=#true",
        ),
        keyed(1, 1, 1, 1, 1, 2, 2, 2),
        members=GROUP,
    )
    first, second = pages(printout)
    assert first[-1] == "F 5 1 5 5"
    assert second[0] == "H 6 2 0 None"


def test_a_column_ending_at_a_group_break_shows_both_sides_of_it(
    tmp_path: Path,
) -> None:
    columns = (
        "    columns count=2 gap=10 {\n  "
        + edge("header")
        + "  "
        + edge("footer")
        + "    }\n"
    )
    printout = built(
        tmp_path,
        columns
        + group(
            band("title", said("'T%d' % a"), "height=10"),
            band("detail", shown("d"), "height=10"),
            props="keeptogether=#true",
        ),
        keyed(1, 1, 1, 1, 1, 2, 2, 2),
        members=GROUP,
    )
    page = texts(printout.pages[0])
    moved = page.index("T2")
    assert page[moved - 2 : moved] == ["F 5 1 5 5", "H 6 2 0 None"]


def test_a_run_without_a_title_begins_where_its_first_row_lands(
    tmp_path: Path,
) -> None:
    """The first row of a new run is the band of it the break waits for.

    The group has no title, and the second run's first row does not fit
    the first page.  The footer reads the run that ended, and the run
    that begins reads a page number of 1 on the page its row lands on.

    """
    detail = said("'d%d %d' % (a, A_PAGE_NUMBER)")
    printout = built(
        tmp_path,
        edge("footer") + group(band("detail", detail, "height=20")),
        keyed(1, 1, 1, 1, 2),
        members=GROUP,
    )
    first, second = pages(printout)
    assert first[-1] == "F 4 1 4 4"
    assert second[0] == "d2 1"


def test_a_page_the_first_record_never_reaches_ends_before_it(
    tmp_path: Path,
) -> None:
    """Before the first record's first band, a footer's `THIS` is `None`.

    The report's title leaves too little of the first page for the first
    group's title, which moves on, so that page carries no record.

    """
    footer = said("'F %d %s' % (ITEM_NUMBER, THIS.a if THIS else None)")
    printout = built(
        tmp_path,
        band("footer", footer, "height=6")
        + title(80)
        + group(band("title", shown("T"), "height=10"), ROW),
        keyed(1),
        members=GROUP,
    )
    assert pages(printout)[0][-1] == "F 0 None"


NESTED = """  records {
    member "a" type="int"
    member "b" type="int"
  }
"""


def test_an_eject_after_an_outer_title_is_an_ordinary_one(
    tmp_path: Path,
) -> None:
    """Once a band of the new record is placed, the break is behind it.

    The third record breaks both groups.  A's title goes on the first
    page, and B's title ejects, so the footer reads the new record, A's
    run began on the first page, and B's begins on the second.

    """
    eject = 'eject type="page" when="ITEM_NUMBER == 3"; '
    inner = (
        '      group "B" expr="b" {\n'
        + "    "
        + band(
            "title",
            eject + said("'B%d %d %d' % (b, A_PAGE_NUMBER, B_PAGE_NUMBER)"),
            "height=10",
        )
        + "    "
        + band("detail", shown("d"), "height=10")
        + "      }\n"
    )
    printout = built(
        tmp_path,
        band("footer", said("'F %d' % ITEM_NUMBER"), "height=6")
        + group(band("title", said("'A%d' % a"), "height=10"), inner),
        [{"a": 1, "b": 1}, {"a": 1, "b": 1}, {"a": 2, "b": 2}],
        members=NESTED,
    )
    assert pages(printout) == [
        ["A1", "B1 1 1", "d", "d", "A2", "F 3"],
        ["B2 2 1", "d", "F 3"],
    ]


def test_keeptogether_moves_a_group_that_would_straddle(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        title(50)
        + group(
            band("title", said("'T%d' % a"), "height=10"),
            band("detail", shown("d"), "height=10"),
            props="keeptogether=#true",
        ),
        keyed(1, 2, 2, 2),
        members=GROUP,
    )
    assert pages(printout) == [["T1", "d"], ["T2", "d", "d", "d"]]


def test_a_keep_rule_passes_over_a_column_that_begins_as_low(
    tmp_path: Path,
) -> None:
    """doc/layout.md#keeping-content-together: one eject, to more room.

    The report title leaves 50 of the first column's 90, and the group
    needs 70.  The second column begins below the title too, so the eject
    `keeptogether` asks for passes over it, and the group starts the next
    page whole.

    """
    printout = built(
        tmp_path,
        band("title", shown("T"), "height=40")
        + COLUMNS
        + group(
            band("title", said("'T%d' % a"), "height=10"),
            ROW,
            props="keeptogether=#true",
        ),
        keyed(1, 1, 1),
        members=GROUP,
    )
    first, second = printout.pages
    assert texts(first) == ["T"]
    assert texts(second) == ["T1", "r1", "r2", "r3"]
    assert (at(second, "T1").x, at(second, "T1").y) == (5, 5)


def test_minrows_moves_a_title_without_its_rows(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        title(65)
        + group(
            band("title", shown("T"), "height=10"),
            band("detail", shown("d"), "height=10"),
            props="minrows=2",
        ),
        keyed(1, 1, 1),
        members=GROUP,
    )
    assert pages(printout) == [[], ["T", "d", "d", "d"]]


def test_mintailrows_moves_rows_onto_the_summarys_frame(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        group(
            band("summary", shown("S"), "height=10"),
            band("detail", said("'r%d' % ITEM_NUMBER"), "height=10"),
            props="mintailrows=2",
        ),
        keyed(*[1] * 9),
        members=GROUP,
    )
    assert pages(printout) == [
        [f"r{index}" for index in range(1, 8)],
        ["r8", "r9", "S"],
    ]


def two_levels(props: str) -> str:
    """Return group A, with a title and these properties, around group B.

    B has a title, a summary, and the detail, and A a summary.
    Every band is one line and says what it is.

    """
    inner = (
        '      group "B" expr="b" {\n'
        + "    "
        + band("title", said("'BT%d' % b"), "height=6")
        + "    "
        + band("summary", said("'BS%d' % b"), "height=6")
        + "    "
        + band("detail", said("'d%d' % ITEM_NUMBER"), "height=6")
        + "      }\n"
    )
    return group(
        band("title", said("'AT%d' % a"), "height=6"),
        band("summary", said("'AS%d' % a"), "height=6"),
        inner,
        props=props,
    )


def test_keeptogether_counts_the_groups_inside_it(tmp_path: Path) -> None:
    """doc/layout.md#group-keeptogether: nested titles and summaries count.

    The second run of A holds three runs of B, 66 points with its own
    summary, and 42 are left.  Measured without B's breaks, it would be
    42 and would stay.

    """
    rows = [{"a": 1, "b": 1}, {"a": 2, "b": 1}, {"a": 2, "b": 2}, {"a": 2, "b": 3}]
    printout = built(
        tmp_path,
        title(18) + two_levels("keeptogether=#true"),
        rows,
        members=NESTED,
    )
    first, second = pages(printout)
    assert first == ["AT1", "BT1", "d1", "BS1", "AS1"]
    assert second[:2] == ["AT2", "BT1"] and second[-2:] == ["BS3", "AS2"]


def test_minrows_counts_the_groups_inside_it(tmp_path: Path) -> None:
    """doc/layout.md#group-minrows-and-mintailrows: nested bands count.

    The title and the first two rows, with B's summary and title between
    the rows, need 36 points, and 30 are left.

    """
    rows = [{"a": 1, "b": 1}, {"a": 1, "b": 2}]
    printout = built(
        tmp_path, title(60) + two_levels("minrows=2"), rows, members=NESTED
    )
    assert pages(printout)[1][:3] == ["AT1", "BT1", "d1"]


def test_mintailrows_counts_the_groups_inside_it(tmp_path: Path) -> None:
    """doc/layout.md#group-minrows-and-mintailrows: nested bands count.

    The last two rows fall in two runs of B.  With B's summaries and its
    title between them, and A's summary after, they need 36 points, and
    30 are left when the first of them comes.

    """
    rows = [{"a": 1, "b": 1}] * 4 + [{"a": 1, "b": 2}]
    printout = built(
        tmp_path, title(30) + two_levels("mintailrows=2"), rows, members=NESTED
    )
    first, second = pages(printout)
    assert first == ["AT1", "BT1", "d1", "d2", "d3"]
    assert second == ["d4", "BS1", "BT2", "d5", "BS2", "AS1"]


def test_an_item_variable_folds_after_the_titles(tmp_path: Path) -> None:
    seen = '  variable "seen" expr="ITEM_NUMBER" calc="list" iter="item"\n'
    printout = built(
        tmp_path,
        group(
            band("title", said("'T %s' % seen"), "height=6"),
            band("detail", shown("d"), "height=6"),
        ),
        keyed(1, 2),
        members=GROUP + seen,
    )
    assert texts(printout.pages[0]) == ["T []", "d", "T [1]", "d"]


# -- swapped bands ----------------------------------------------------


def test_swapped_bands_sit_outside_the_page_header_and_footer(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        band("header", shown("H"), "height=6")
        + band("footer", shown("F"), "height=6")
        + band("title", shown("T"), "height=6 swapheader=#true")
        + band("summary", shown("S"), "height=6 swapfooter=#true")
        + ROW,
    )
    page = printout.pages[0]
    tops = [at(page, one).y for one in ("T", "H", "r1", "S", "F")]
    assert tops == [5, 11, 17, 89, 83]


def test_a_swapped_summary_needs_room_above_the_column_footers(
    tmp_path: Path,
) -> None:
    """doc/layout.md#swapheader-and-swapfooter: the footers move up with it.

    Eight rows fill the column to its footer.  The summary would push
    the footer up onto the last row, so it starts the next page.

    """
    footer = band("footer", shown("CF"), "height=10")
    columns = "    columns count=2 gap=10 {\n  " + footer + "    }\n"
    printout = built(
        tmp_path,
        columns
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=10")
        + band("summary", shown("S"), "height=10 swapfooter=#true"),
        rows_of(8),
    )
    first, second = printout.pages
    assert texts(first)[-2:] == ["r8", "CF"]
    assert (at(second, "S").y, at(second, "CF").y) == (85, 75)


@pytest.mark.parametrize(
    "bands",
    [
        band("title", shown("X"), "height=120 swapheader=#true") + ROW,
        ROW + band("summary", shown("X"), "height=120 swapfooter=#true"),
    ],
    ids=["title", "summary"],
)
def test_a_swapped_band_taller_than_the_page_overflows(
    tmp_path: Path, bands: str
) -> None:
    """doc/layout.md#swapheader-and-swapfooter: and goes where it would unswapped.

    Allowed, it is placed at the top of the page frame, and nothing is
    drawn above the top margin.

    """
    with pytest.raises(BuildError, match="measures 120 pt and the largest frame"):
        built(tmp_path, bands)
    printout = built(tmp_path, bands, allow_overflow=True)
    assert [warning.kind for warning in printout.warnings] == ["overflow"]
    placed = [page for page in printout.pages if "X" in texts(page)]
    assert [at(page, "X").y for page in placed] == [5]


# -- the pieces -------------------------------------------------------


def test_a_band_in_a_column_brings_its_parents_fill_down() -> None:
    page = Frame(top=0.0, bottom=100.0, fill=0.0)
    column = Frame(count=2, parent=page, top=0.0, bottom=100.0, fill=0.0)
    page.child = column
    column.advance(40.0)
    assert (page.fill, column.fill) == (40.0, 40.0)
    page.advance(50.0)
    assert (column.fill, column.floor) == (50.0, 50.0)


def test_frames_are_equal_only_to_themselves() -> None:
    one, other = Frame(), Frame()
    assert one == one and one != other
    assert len({one, other}) == 2


def test_keys_are_counted_once_and_taken_back_in_order() -> None:
    keys = Keys()
    for key in (1, [2], 1.0, [2], "x"):
        keys.add(key)
    mark = keys.mark()
    for key in ([3], "y", "x", 1):
        keys.add(key)
    assert len(keys) == 5
    keys.rewind(mark)
    assert len(keys) == 3
    keys.add("y")
    assert len(keys) == 4


def test_mintailrows_measures_only_the_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doc/layout.md#group-minrows-and-mintailrows: the rows left are counted.

    Every row but the last is too far from the summary for the test to
    apply, and finding that out measures nothing: each row is measured
    once, where it is placed, and the tail and the ejects add a few more.

    """
    measured = 0
    original = Measurer.band

    def counting(self: Measurer, *args: Any, **kwargs: Any) -> Measurement | None:
        nonlocal measured
        measured += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Measurer, "band", counting)
    built(
        tmp_path,
        group(band("summary", shown("S"), "height=6"), band("detail", shown("d"))),
        keyed(*[1] * 40),
        members=GROUP,
    )
    assert measured < 50


def spans(*extents: Extent) -> Measurement:
    """Return a measured band of rectangles covering these spans."""
    marks = tuple(
        Rectangle(
            Box(0.0, one.top, 1.0, one.bottom - one.top),
            1.0,
            "solid",
            None,
            None,
            0.0,
        )
        for one in extents
    )
    return Measurement(max(one.bottom for one in extents), marks, extents)


def test_a_cut_may_fall_between_a_stretch_fields_lines_only() -> None:
    measured = spans(Extent(0.0, 30.0, 5, 6.0))
    assert is_cut(measured, 12.0)
    assert not is_cut(measured, 13.0)


def test_a_cut_must_divide_content() -> None:
    measured = spans(Extent(0.0, 10.0), Extent(0.0, 5.0))
    assert is_cut(measured, 10.0)
    assert not is_legal(measured, 10.0, 1, 1)


def test_the_greatest_legal_cut_within_the_space_is_chosen() -> None:
    measured = spans(Extent(0.0, 30.0, 5, 6.0), Extent(20.0, 25.0))
    assert choose(measured, 28.0) == 18.0
    assert choose(measured, 28.0, legal=False) == 18.0


def test_a_split_moves_the_tail_up_by_the_cut() -> None:
    head, tail = split(spans(Extent(0.0, 10.0), Extent(20.0, 25.0)), 15.0)
    assert [one.box.y for one in head.marks] == [0.0]
    assert [one.box.y for one in tail.marks] == [5.0]
    assert (head.height, tail.height) == (15.0, 10.0)
