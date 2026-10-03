"""Deferred evaluation: placeholders, snapshots, scopes, and re-measurement.

The differential suite holds all of this to the reference over
the `deferred/` probes.  What is here is each rule of
doc/layout.md#deferred-evaluation on its own, so that a change breaks
the test named after the sentence it broke, and the rules where this
engine and the reference part company are tested for this engine's answer.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.errors import BuildError
from sr.expr import Namespace, compile_expression
from sr.layout.context import Context
from sr.layout.defer import Deferral, Register, final, find, scope_of, snapshot, swap
from sr.layout.measure import Extent, Measurement
from sr.layout.place import split
from sr.printout.model import Box, Mark, Page, Printout, Rectangle, Text
from sr.printout.model import Xref as XrefMark
from sr.template.load import load_text
from sr.template.model import Field

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

# A page 200 by 100 with 5pt margins, which leaves a frame 90pt tall.
# Size 5 sets a line 6pt tall, which keeps the arithmetic whole.
HEAD = """
report name="Deferred" {
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


def built(
    tmp_path: Path,
    bands: str,
    rows: list[dict[str, Any]] | None = None,
    *,
    members: str = "",
) -> Printout:
    """Build a report and return its printout.

    Args:
        tmp_path: Where to write the template and the data.
        bands: What goes under `layout`, after its style.
        rows: The records, or ``None`` for one record nothing reads.
        members: What goes between the font and the layout.

    """
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("MEMBERS", members)
    template.write_bytes(text.replace("BANDS", bands).encode("utf-8"))
    data = tmp_path / "rows.jsonl"
    lines = [json.dumps(row) for row in ([{"n": 1}] if rows is None else rows)]
    data.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(template, data, asked).printout


def band(kind: str, body: str, props: str = "") -> str:
    """Return a band, indented under `layout`.

    Args:
        kind: The band's node name.
        body: What goes inside it.
        props: Its properties.

    """
    return f"    {kind} {props} {{\n      {body}\n    }}\n"


def said(expr: str, rest: str = "left=0 top=0 width=100 height=6") -> str:
    """Return a one-line field that shows an expression right away.

    Args:
        expr: The expression, with no double quotes in it.
        rest: Its geometry.

    """
    return f'field expr="{expr}" {rest}'


def deferred(
    expr: str, evaltime: str, rest: str = "left=0 top=0 width=100 height=6"
) -> str:
    """Return a field whose expression waits for a scope.

    The expression is a raw KDL string, so a backslash in it
    reaches the expression language as written.

    Args:
        expr: The expression, with no double quotes in it.
        evaltime: The scope.
        rest: Its placeholder and geometry.

    """
    return f'field expr=#"{expr}"# evaltime="{evaltime}" {rest}'


def texts(page: Page) -> list[str]:
    """Return every text mark's lines on a page, joined, in paint order."""
    return ["|".join(mark.lines) for mark in page.marks if isinstance(mark, Text)]


def pages(printout: Printout) -> list[list[str]]:
    """Return every page's text, a list per page."""
    return [texts(page) for page in printout.pages]


def linked(mark: Mark) -> list[str]:
    """Return the lines of every text mark in an xref, at any depth."""
    if isinstance(mark, Text):
        return ["|".join(mark.lines)]
    if isinstance(mark, XrefMark):
        return [lines for one in mark.marks for lines in linked(one)]
    return []


def at(page: Page, first: str) -> Box:
    """Return the box of the text mark whose first line is ``first``."""
    for mark in page.marks:
        if isinstance(mark, Text) and mark.lines[0] == first:
            return mark.box
    raise AssertionError(f"no text mark says {first!r}")


def rows_of(count: int) -> list[dict[str, Any]]:
    """Return ``count`` records that nothing reads."""
    return [{"n": number} for number in range(count)]


ROW = band("detail", said("'r%d' % ITEM_NUMBER"), "height=20")


# -- what a deferred expression sees ----------------------------------


def test_final_reads_the_scope_end_and_the_rest_reads_where_it_sits(
    tmp_path: Path,
) -> None:
    footer = deferred("'Page %d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)", "report")
    printout = built(tmp_path, band("footer", footer, "height=6") + ROW, rows_of(9))
    assert [texts(page)[-1] for page in printout.pages] == [
        "Page 1 of 3",
        "Page 2 of 3",
        "Page 3 of 3",
    ]


def test_a_band_measured_again_after_an_eject_registers_what_it_read_last(
    tmp_path: Path,
) -> None:
    """The fifth row is measured on page 1, ejects, and is measured on page 2."""
    row = deferred(
        "'r%d at %d of %d' % (ITEM_NUMBER, PAGE_COUNT, FINAL.PAGE_COUNT)", "page"
    )
    printout = built(tmp_path, band("detail", row, "height=20"), rows_of(5))
    assert pages(printout) == [
        ["r1 at 0 of 4", "r2 at 1 of 4", "r3 at 2 of 4", "r4 at 3 of 4"],
        ["r5 at 0 of 1"],
    ]


def test_the_placeholder_is_measured_as_written(tmp_path: Path) -> None:
    """``format`` is applied to the value, which `%05d` could not take as text."""
    row = deferred(
        "FINAL.PAGE_COUNT",
        "page",
        'format="rows %05d" text="rows 99999" left=0 top=0 width=100 height=6',
    )
    assert pages(built(tmp_path, band("detail", row, "height=6"))) == [["rows 00001"]]


# -- re-measurement ---------------------------------------------------


@pytest.mark.parametrize(
    ("valign", "down"), [("top", 5), ("center", 11), ("bottom", 17)]
)
def test_a_shorter_value_sits_in_the_room_by_valign(
    tmp_path: Path, valign: str, down: float
) -> None:
    field = deferred(
        "'x%d' % FINAL.PAGE_NUMBER",
        "report",
        f'stretch=#true text="a\\nb\\nc" left=0 top=0 width=100 valign="{valign}"',
    )
    printout = built(tmp_path, band("detail", field))
    assert at(printout.pages[0], "x1") == Box(5, down, 100, 6)


def test_the_room_is_the_placeholders_text_and_not_the_box(tmp_path: Path) -> None:
    """A field that does not stretch keeps what its room holds, one line here."""
    field = deferred(
        "'x\\ny\\nz' if FINAL.PAGE_NUMBER else ''",
        "report",
        "left=0 top=0 width=100 height=30",
    )
    printout = built(tmp_path, band("detail", field))
    assert pages(printout) == [["x"]]
    assert at(printout.pages[0], "x") == Box(5, 5, 100, 6)


def test_a_stretch_field_that_outgrows_its_placeholder_is_an_error(
    tmp_path: Path,
) -> None:
    field = deferred(
        "'x\\ny' if FINAL.PAGE_NUMBER else ''",
        "report",
        'stretch=#true text="a" left=0 top=0 width=100 height=30',
    )
    with pytest.raises(BuildError) as refused:
        built(tmp_path, band("detail", field))
    said = str(refused.value)
    assert 'the deferred value "x\\ny" needs 12 pt' in said
    assert 'its placeholder "a" reserved 6 pt' in said
    assert "report > layout > detail > field" in said
    assert "record 0" in said


def test_a_clamp_does_not_let_a_stretch_field_drop_lines(tmp_path: Path) -> None:
    """A ``maxheight`` bounds the box, and the box was the placeholder's."""
    field = deferred(
        "'x\\ny\\nz' if FINAL.PAGE_NUMBER else ''",
        "report",
        'stretch=#true text="a\\nb\\nc" maxheight=12 left=0 top=0 width=100',
    )
    with pytest.raises(BuildError) as refused:
        built(tmp_path, band("detail", field))
    assert "needs 18 pt and its placeholder" in str(refused.value)
    assert "reserved 12 pt" in str(refused.value)


def test_a_failing_expression_names_the_record_it_was_built_for(
    tmp_path: Path,
) -> None:
    row = deferred("FINAL.PAGE_NUMBER + (0 if ITEM_NUMBER < 2 else 'x')", "report")
    with pytest.raises(BuildError) as refused:
        built(tmp_path, band("detail", row, "height=6"), rows_of(3))
    assert "expr=, record 1" in str(refused.value)


# -- when a scope ends ------------------------------------------------


def test_a_group_resolves_after_its_summary_against_its_last_record(
    tmp_path: Path,
) -> None:
    title = deferred("'%d rows, last %d' % (FINAL.A_COUNT, FINAL.ITEM_NUMBER)", "A")
    summary = deferred("'end %d' % FINAL.A_COUNT", "A")
    group = (
        '    group "A" expr="a" {\n'
        + band("title", title, "height=6")
        + band("summary", summary, "height=6")
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=6")
        + "    }\n"
    )
    printout = built(tmp_path, group, [{"a": 1}, {"a": 1}, {"a": 2}], members=KEYED)
    assert pages(printout) == [
        ["2 rows, last 2", "r1", "r2", "end 2", "1 rows, last 3", "r3", "end 1"]
    ]


def test_a_column_ends_after_its_footer_and_before_the_advance(
    tmp_path: Path,
) -> None:
    columns = (
        "    columns count=2 gap=10 {\n"
        + band(
            "footer", deferred("'%d rows' % FINAL.COLUMN_COUNT", "column"), "height=6"
        )
        + "    }\n"
    )
    printout = built(tmp_path, columns + ROW, rows_of(6))
    assert pages(printout) == [["r1", "r2", "r3", "r4", "4 rows", "r5", "r6", "2 rows"]]


def test_a_page_ending_at_a_group_break_resolves_against_the_ended_run(
    tmp_path: Path,
) -> None:
    """The same context its footers are built in, doc/layout.md#when-a-scope-ends."""
    header = deferred("'last a%d' % FINAL.THIS.a", "page")
    group = (
        '    group "A" expr="a" keeptogether=#true {\n'
        + band("title", said("'A%d' % a"), "height=6")
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=20")
        + "    }\n"
    )
    rows = [{"a": 1}] * 3 + [{"a": 2}] * 3
    printout = built(
        tmp_path, band("header", header, "height=6") + group, rows, members=KEYED
    )
    assert [texts(page)[0] for page in printout.pages] == ["last a1", "last a2"]
    assert pages(printout)[0][1:] == ["A1", "r1", "r2", "r3"]


def test_a_lookahead_resolves_nothing(tmp_path: Path) -> None:
    """A keep-together lookahead closes the run it measures without resolving it.

    B's summary registers a deferral for A after A has resolved, so it waits
    for A's next run, which `keeptogether` measures ahead on page 1 and then
    moves to page 2.  Resolved by the lookahead it would read page 1.

    """
    note = deferred("'next A ends on page %d' % FINAL.PAGE_NUMBER", "A")
    nested = (
        '    group "B" expr="b" {\n'
        + band("summary", note, "height=6")
        + '      group "A" expr="a" keeptogether=#true {\n'
        + band("title", said("'A%d' % a"), "height=6")
        + band("detail", said("'r%d' % ITEM_NUMBER"), "height=20")
        + "      }\n    }\n"
    )
    members = """  records {
    member "a" type="int"
    member "b" type="int"
  }
"""
    rows = [{"a": 1, "b": 1}] + [{"a": 2, "b": 2}] * 3
    printout = built(tmp_path, nested, rows, members=members)
    assert pages(printout) == [
        ["A1", "r1", "next A ends on page 2"],
        ["A2", "r2", "r3", "r4", "next A ends on page 2"],
    ]


# -- balancing, splitting, warnings -----------------------------------


@pytest.mark.parametrize(
    ("evaltime", "left"),
    [("column", [5] * 6), ("page", [5, 5, 5, 105, 105, 105])],
    ids=["column", "page"],
)
def test_only_a_column_deferral_leaves_a_balanced_page_alone(
    tmp_path: Path, evaltime: str, left: list[float]
) -> None:
    columns = "    columns count=2 gap=10 balance=#true\n"
    row = deferred(
        "'r%d %d' % (ITEM_NUMBER, FINAL.COLUMN_COUNT)",
        evaltime,
        "left=0 top=0 width=90 height=6",
    )
    printout = built(tmp_path, columns + band("detail", row, "height=6"), rows_of(6))
    marks = [mark for mark in printout.pages[0].marks if isinstance(mark, Text)]
    assert [mark.box.x for mark in marks] == left


@pytest.mark.parametrize(
    ("printwhen", "last"),
    [("COLUMN_NUMBER == 2", "CF 2 rows 2"), ("False", "r15")],
    ids=["last-column", "never"],
)
def test_a_column_deferral_in_a_footer_leaves_every_page_alone(
    tmp_path: Path, printwhen: str, last: str
) -> None:
    """The footer is judged from the template, whatever it prints."""
    header = said("'CH %d' % COLUMN_NUMBER", "left=0 top=0 width=90 height=6")
    footer = deferred(
        "'CF %d rows %d' % (COLUMN_NUMBER, FINAL.COLUMN_COUNT)",
        "column",
        f'printwhen="{printwhen}" left=0 top=0 width=90 height=6',
    )
    columns = (
        "    columns count=2 gap=10 balance=#true {\n"
        + band("header", header, "height=6")
        + band("footer", footer, "height=6")
        + "    }\n"
    )
    row = said("'r%d' % ITEM_NUMBER", "left=0 top=0 width=90 height=6")
    printout = built(tmp_path, columns + band("detail", row), rows_of(15))
    page = printout.pages[0]
    rows = [mark for mark in page.marks if isinstance(mark, Text)]
    rows = [mark for mark in rows if mark.lines[0].startswith("r")]
    assert [mark.box.x for mark in rows] == [5] * 13 + [105] * 2
    assert texts(page)[-1] == last


def test_a_column_deferral_in_a_header_leaves_the_balance_to_the_page(
    tmp_path: Path,
) -> None:
    """A header is placed before the page balances, and judged as placed."""
    header = deferred(
        "'CH %d' % FINAL.COLUMN_COUNT",
        "column",
        'printwhen="False" left=0 top=0 width=90 height=6',
    )
    columns = (
        "    columns count=2 gap=10 balance=#true {\n"
        + band("header", header, "height=6")
        + "    }\n"
    )
    row = said("'r%d' % ITEM_NUMBER", "left=0 top=0 width=90 height=6")
    printout = built(tmp_path, columns + band("detail", row), rows_of(15))
    marks = [mark for mark in printout.pages[0].marks if isinstance(mark, Text)]
    assert [mark.box.x for mark in marks] == [5] * 8 + [105] * 7


def test_a_deferred_stretch_field_is_not_cut_between_its_lines(
    tmp_path: Path,
) -> None:
    """The band moves whole, where without the deferral it would split."""
    title = band("title", "rectangle left=0 top=0 width=1 height=1", "height=80")
    lines = 'stretch=#true text="1\\n2\\n3\\n4" left=0 top=0 width=40'
    row = deferred("'x%d' % FINAL.PAGE_NUMBER", "report", lines)
    other = 'field text="a\\nb\\nc\\nd" stretch=#true left=50 top=0 width=40'
    detail = band("detail", f"{row}\n      {other}", "split=#true orphans=1 widows=1")
    printout = built(tmp_path, title + detail)
    assert pages(printout) == [[], ["x2", "a|b|c|d"]]


def test_a_deferral_beside_a_split_field_is_set_in_its_own_half(
    tmp_path: Path,
) -> None:
    """The cut moves one deferral to the next page and leaves the other."""
    title = band("title", "rectangle left=0 top=0 width=1 height=1", "height=78")
    after = deferred(
        "'t%d/%d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)",
        "report",
        'text="t0/0" left=100 top=24 width=40 height=6',
    )
    lines = 'field text="a\\nb\\nc\\nd" stretch=#true left=0 top=0 width=40'
    beside = deferred(
        "'h%d/%d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)",
        "report",
        'text="h0/0" left=50 top=0 width=40 height=6',
    )
    body = f"{after}\n      {lines}\n      {beside}"
    detail = band("detail", body, "split=#true orphans=1 widows=1")
    printout = built(tmp_path, title + detail)
    assert pages(printout) == [["a|b", "h1/2"], ["t1/2", "c|d"]]
    assert at(printout.pages[0], "h1/2") == Box(55, 83, 40, 6)
    assert at(printout.pages[1], "t1/2") == Box(105, 17, 40, 6)


def test_the_value_raises_a_glyph_warning_and_the_placeholder_none(
    tmp_path: Path,
) -> None:
    field = deferred(
        "'p%d \\u4e2d' % FINAL.PAGE_NUMBER",
        "report",
        'text="\\u{4e00}" left=0 top=0 width=100 height=6',
    )
    printout = built(tmp_path, band("detail", field))
    assert [warning.message for warning in printout.warnings] == [
        "the font \"body\" has no glyph for '中', so an empty box is drawn in its place"
    ]


def test_a_header_reservation_registers_nothing(tmp_path: Path) -> None:
    """Each page's header resolves once, from the one that was placed."""
    header = deferred(
        "'H%d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)",
        "report",
        'stretch=#true text="1\\n2" left=0 top=0 width=100 valign="bottom"',
    )
    printout = built(tmp_path, band("header", header) + ROW, rows_of(5))
    assert [texts(page)[0] for page in printout.pages] == ["H1 of 2", "H2 of 2"]
    assert at(printout.pages[0], "r1").y == 17


def test_a_deferral_inside_nested_xrefs_is_set_in_place(
    tmp_path: Path,
) -> None:
    """Each row's value replaces its placeholder two xrefs down."""
    first = said("'r%d' % ITEM_NUMBER", "left=0 top=0 width=40 height=6")
    inner = deferred(
        "'n%d/%d' % (ITEM_NUMBER, FINAL.REPORT_COUNT)",
        "report",
        'text="n0/0" left=0 top=0 width=50 height=6',
    )
    body = f"""{first}
      xref type="url" target="'u'" left=50 top=0 width=100 height=6 {{
        field text="before" left=0 top=0 width=40 height=6
        xref type="url" target="'v'" left=50 top=0 width=50 height=6 {{
          {inner}
        }}
      }}"""
    printout = built(tmp_path, band("detail", body, "height=6"), rows_of(3))
    page = printout.pages[0]
    assert texts(page) == ["r1", "r2", "r3"]
    held = [linked(mark) for mark in page.marks if isinstance(mark, XrefMark)]
    assert held == [["before", "n1/3"], ["before", "n2/3"], ["before", "n3/3"]]


# -- the parts --------------------------------------------------------


def node() -> Field:
    """Return a deferred field node, loaded from a template."""
    template = HEAD.replace("FACE", REGULAR).replace("MEMBERS", "")
    body = deferred("FINAL.PAGE_COUNT", "page")
    report = load_text(template.replace("BANDS", band("detail", body))).require()
    assert report.layout is not None and report.layout.detail is not None
    found = report.layout.detail.elements[0]
    assert isinstance(found, Field)
    return found


def test_a_scope_named_like_a_builtin_one_is_not_a_group() -> None:
    assert scope_of("page") == ("page", None)
    assert scope_of("report") == ("report", None)
    assert scope_of("customer") == ("group", "customer")


def test_a_snapshot_keeps_every_name_but_final() -> None:
    expression = compile_expression("(PAGE_NUMBER, a, FINAL.PAGE_NUMBER, missing)")
    names = {"PAGE_NUMBER": 3, "a": 7, "b": 9}
    assert snapshot(expression, names) == {"PAGE_NUMBER": 3, "a": 7}


def test_final_holds_the_counters_and_the_variables() -> None:
    context = Context(variables={"total": 12}, page_number=4)
    context.group_counts["A"] = 2
    ending = final(context)
    assert isinstance(ending, Namespace)
    assert ending.members["PAGE_NUMBER"] == 4
    assert ending.members["A_COUNT"] == 2
    assert ending.members["total"] == 12
    assert "VERTICAL_POSITION" not in ending.members
    assert "FINAL" not in ending.members


def test_the_register_hands_a_scope_back_in_the_order_placed() -> None:
    field = node()
    register = Register()
    page = Deferral(field, ("page", None), {}, 0, (0,))
    group = Deferral(field, ("group", "A"), {}, 0, (1,))
    register.add(page, 0, 4)
    register.add(group, 0, 4)
    register.add(page.at((2, 1)), 1, 0)
    taken = register.due([("page", None)])
    assert [(one.page, one.path) for one in taken] == [(0, (4,)), (1, (2, 1))]
    assert len(register) == 1
    assert [one.deferral for one in register.due(None)] == [group]
    assert len(register) == 0


def test_scopes_that_end_together_come_back_in_the_order_placed() -> None:
    field = node()
    register = Register()
    column = Deferral(field, ("column", None), {}, 0, (0,))
    page = Deferral(field, ("page", None), {}, 0, (0,))
    group = Deferral(field, ("group", "A"), {}, 0, (0,))
    placed = [page, column, group, column.at((1,)), page.at((1,))]
    for start, deferral in enumerate(placed):
        register.add(deferral, 0, start)
    taken = register.due([("column", None), ("page", None)])
    assert [one.path for one in taken] == [(0,), (1,), (4,), (5,)]
    assert [one.deferral for one in register.due(None)] == [group]


def test_two_placements_of_one_element_are_two_deferrals() -> None:
    field = node()
    first = Deferral(field, ("page", None), {"n": 1}, 0)
    second = Deferral(field, ("page", None), {"n": 1}, 0)
    assert first != second
    assert len({first, second}) == 2


def test_a_mark_inside_an_xref_is_found_and_replaced() -> None:
    inner = Text(Box(1, 2, 3, 4), "body", "#000000", "left", 4.0, ("a",))
    xref = XrefMark(Box(0, 0, 10, 10), "url", "x", None, (inner,))
    marks: list[Any] = [Rectangle(Box(0, 0, 1, 1), 0, "solid", None, None, 0), xref]
    assert find(marks, (1, 0)) is inner
    swap(marks, (1, 0), Text(Box(1, 2, 3, 4), "body", "#000000", "left", 4.0, ("b",)))
    assert isinstance(marks[1], XrefMark)
    replaced = marks[1].marks[0]
    assert isinstance(replaced, Text) and replaced.lines == ("b",)


def test_a_split_carries_each_deferral_to_its_half() -> None:
    field = node()
    text = Text(Box(0, 0, 10, 6), "body", "#000000", "left", 6.0, ("a",))
    marks = (text, text.moved(0, 10), text.moved(0, 20))
    extents = (Extent(0, 6), Extent(10, 16), Extent(20, 26))
    deferred_ = (
        Deferral(field, ("page", None), {}, 0, (0,)),
        Deferral(field, ("page", None), {}, 0, (2,)),
    )
    head, tail = split(Measurement(26, marks, extents, deferred_), 8)
    assert [one.path for one in head.deferred] == [(0,)]
    assert [one.path for one in tail.deferred] == [(1,)]
