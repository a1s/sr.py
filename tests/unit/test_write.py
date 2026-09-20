"""The NDJSON encoding, byte for byte.

doc/printout.md#encoding fixes what a JSON writer usually chooses
for itself, so each of those choices is a test here: the number format,
the escapes, the key order, the path rule and the line ending.

"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from sr.errors import BuildWarning
from sr.printout.model import (
    Box,
    FontEntry,
    Line,
    Page,
    Paper,
    Printout,
    Rectangle,
    Report,
    Text,
)
from sr.printout.write import (
    dumps,
    header_object,
    number,
    page_object,
    quoted,
    relative_to,
    write_jsonl,
)

A4 = Paper(595.276, 841.89, 0, 0, 0, 0)


def printout(**changes: object) -> Printout:
    """Return a printout with one font and no pages."""
    made = Printout(
        report=Report("Minimal"),
        built="2026-08-04T09:12:44Z",
        engine="sr 0.1.0",
        strict_fonts=True,
        paper=A4,
        fonts=(
            FontEntry(
                name="body",
                size=9,
                bold=False,
                italic=False,
                underline=False,
                face="Go",
                step="explicit",
                file=Path("C:/project/fonts/Go-Regular.ttf"),
            ),
        ),
    )
    for name, value in changes.items():
        setattr(made, name, value)
    return made


# -- numbers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (72.0, "72"),
        (0.0, "0"),
        (-0.0, "0"),
        (0.001, "0.001"),
        (510.236, "510.236"),
        (123456.789, "123456.789"),
        (-42.5, "-42.5"),
        (9, "9"),
    ],
)
def test_a_number_is_written_as_the_format_spells_it(
    value: float, written: str
) -> None:
    assert number(value) == written


def test_no_number_reaches_exponent_notation() -> None:
    for value in (0.001, 1e-3, 1234567.0, 100000000.0):
        assert "e" not in number(value)


# -- strings ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ('q"q', '"q' + chr(92) + '"q"'),
        (chr(92), '"' + chr(92) * 2 + '"'),
        ("/", '"/"'),
        ("<", '"' + chr(92) + 'u003c"'),
        (">", '"' + chr(92) + 'u003e"'),
        ("&", '"' + chr(92) + 'u0026"'),
        (chr(8), '"' + chr(92) + 'b"'),
        (chr(9), '"' + chr(92) + 't"'),
        (chr(10), '"' + chr(92) + 'n"'),
        (chr(12), '"' + chr(92) + 'f"'),
        (chr(13), '"' + chr(92) + 'r"'),
        (chr(11), '"' + chr(92) + 'u000b"'),
        (chr(0x1F), '"' + chr(92) + 'u001f"'),
        (chr(0x2028), '"' + chr(92) + 'u2028"'),
        (chr(0x2029), '"' + chr(92) + 'u2029"'),
        (chr(0x7F), '"' + chr(0x7F) + '"'),
        (chr(0xA0), '"' + chr(0xA0) + '"'),
        ("\u4e2d", '"\u4e2d"'),
    ],
)
def test_a_string_is_escaped_as_the_format_asks(text: str, written: str) -> None:
    assert quoted(text) == written


def test_every_escape_still_parses_as_the_string_it_came_from() -> None:
    others = (0x7F, 0x85, 0xA0, 0x2028, 0x2029, 0x1F600)
    for code in (*range(0x30), *others):
        text = "a" + chr(code) + "b"
        assert json.loads(quoted(text)) == text


# -- objects ----------------------------------------------------------


def test_an_object_keeps_the_order_it_was_built_in() -> None:
    assert dumps({"b": 1, "a": 2}) == '{"b":1,"a":2}'


def test_a_value_the_format_has_no_spelling_for_is_refused() -> None:
    with pytest.raises(TypeError):
        dumps(object())


def test_the_header_writes_its_keys_in_order() -> None:
    keys = list(header_object(printout(), None))
    assert keys == [
        "sr",
        "kind",
        "report",
        "built",
        "engine",
        "strictFonts",
        "pages",
        "page",
        "fonts",
        "data",
    ]


def test_the_group_tables_appear_only_with_groups() -> None:
    made = printout()
    made.group_runs = {"customer": 4}
    made.group_keys = {"customer": 4}
    keys = list(header_object(made, None))
    assert keys.index("groupRuns") == keys.index("pages") + 1
    assert keys.index("groupKeys") == keys.index("groupRuns") + 1


def test_warnings_are_last_and_only_when_there_are_any() -> None:
    made = printout()
    made.warnings = (BuildWarning("glyph", "no glyph", node="report > layout"),)
    written = header_object(made, None)
    assert list(written)[-1] == "warnings"
    assert list(written["warnings"][0]) == ["kind", "node", "message"]


def test_a_report_field_that_is_absent_is_left_out() -> None:
    assert header_object(printout(), None)["report"] == {"name": "Minimal"}


def test_a_font_the_template_named_is_written_relative_to_the_printout() -> None:
    entry = header_object(printout(), Path("C:/project/out"))["fonts"][0]
    assert entry["resolvedFile"] == "../fonts/Go-Regular.ttf"
    assert list(entry) == [
        "name",
        "size",
        "bold",
        "italic",
        "underline",
        "resolvedFile",
        "resolvedFace",
        "resolvedBy",
    ]


def test_a_font_found_on_the_host_stays_absolute() -> None:
    made = printout()
    made.fonts = (
        FontEntry(
            name="body",
            size=9,
            bold=False,
            italic=False,
            underline=False,
            face="Arial",
            step="alias",
            requested="Helvetica",
            file=Path("C:/Windows/Fonts/arial.ttf"),
        ),
    )
    entry = header_object(made, Path("C:/project/out"))["fonts"][0]
    assert entry["resolvedFile"] == "C:/Windows/Fonts/arial.ttf"
    assert entry["requested"] == "Helvetica"


def test_the_font_table_is_sorted_by_name() -> None:
    made = printout()
    made.fonts = tuple(
        FontEntry(name, 9, False, False, False, "Go", "explicit")
        for name in ("title", "body")
    )
    assert [one["name"] for one in header_object(made, None)["fonts"]] == [
        "body",
        "title",
    ]


def test_the_data_table_is_sorted_by_name() -> None:
    made = printout()
    made.data = {"zebra": b"\x00\x01", "alpha": "text"}
    written = header_object(made, None)["data"]
    assert list(written) == ["alpha", "zebra"]
    assert written["zebra"] == {"encoding": "base64", "content": "AAE="}
    assert written["alpha"] == {"content": "text"}


# -- marks ------------------------------------------------------------


def test_a_text_mark_writes_its_fields_in_order() -> None:
    mark = Text(Box(1, 2, 3, 4), "body", "#000000", "left", 10.8, ("a",))
    page = page_object(Page(1, (mark,)), A4)
    assert list(page["marks"][0]) == [
        "kind",
        "box",
        "font",
        "color",
        "align",
        "leading",
        "lines",
    ]
    assert list(page["marks"][0]["box"]) == ["x", "y", "width", "height"]


def test_a_continued_line_says_so_and_a_finished_one_does_not() -> None:
    plain = Text(Box(0, 0, 1, 1), "body", "#000000", "justified", 10, ("a",))
    carried = Text(Box(0, 0, 1, 1), "body", "#000000", "justified", 10, ("a",), True)
    assert "lastLineJustified" not in page_object(Page(1, (plain,)), A4)["marks"][0]
    assert page_object(Page(1, (carried,)), A4)["marks"][0]["lastLineJustified"] is True


def test_a_rectangle_leaves_out_what_it_does_not_carry() -> None:
    mark = Rectangle(Box(0, 0, 10, 10), 0, "solid", None, None, 0)
    written = page_object(Page(1, (mark,)), A4)["marks"][0]
    assert list(written) == ["kind", "box", "width", "dash"]


def test_a_rectangle_writes_stroke_fill_and_radius_where_it_has_them() -> None:
    mark = Rectangle(Box(0, 0, 10, 10), 1, "dot", "#FF0000", "#00FF00", 2)
    written = page_object(Page(1, (mark,)), A4)["marks"][0]
    assert list(written) == ["kind", "box", "width", "dash", "stroke", "fill", "radius"]


def test_a_line_writes_its_fields_in_order() -> None:
    mark = Line(Box(0, 0, 10, 0), 0.5, "solid", "#000000", False)
    written = page_object(Page(1, (mark,)), A4)["marks"][0]
    assert list(written) == ["kind", "box", "width", "dash", "color", "backslant"]


def test_a_page_names_only_the_geometry_it_overrides() -> None:
    other = Paper(A4.width, A4.height, 0, 0, 20, 0)
    written = page_object(Page(2, (), other), A4)
    assert list(written) == ["kind", "number", "topMargin", "marks"]


def test_a_page_that_matches_the_document_names_no_geometry() -> None:
    assert list(page_object(Page(1, (), A4), A4)) == ["kind", "number", "marks"]


# -- the file ---------------------------------------------------------


def test_every_line_ends_with_one_newline() -> None:
    made = printout()
    made.pages = (Page(1), Page(2))
    out = io.StringIO()
    write_jsonl(made, out, None)
    text = out.getvalue()
    assert text.endswith("\n")
    assert chr(13) not in text
    assert len(text.splitlines()) == 3


def test_the_page_count_is_the_pages_that_follow() -> None:
    made = printout()
    made.pages = (Page(1), Page(2), Page(3))
    assert header_object(made, None)["pages"] == 3


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="only Windows has two paths with no relative form between them",
)
def test_a_path_with_no_relative_form_is_written_absolute() -> None:
    """Two Windows drives are the case doc/printout.md#paths names.

    Everywhere else every pair of paths has a relative form, however
    many `..` it takes, so this is the one rule that cannot be stated
    on another platform rather than one that differs between them.

    """
    assert relative_to(Path("Z:/elsewhere/font.ttf"), Path("C:/project")) == (
        "Z:/elsewhere/font.ttf"
    )
