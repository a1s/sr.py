"""The twelve invariants of doc/printout.md#invariants, one at a time.

Each test starts from a printout that keeps all twelve, breaks one,
and reads what the check says about it.  The records are built by hand
rather than by the engine, since the engine refuses to produce a printout
that breaks any of them, which is the point of the check.

"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from sr.printout.model import (
    Barcode,
    Box,
    FontEntry,
    Outline,
    Page,
    Paper,
    Printout,
    Report,
    Text,
)
from sr.printout.verify import Area, Crossing, check, crossing, described, violations

PAGE = {
    "width": 100,
    "height": 60,
    "leftMargin": 5,
    "rightMargin": 5,
    "topMargin": 5,
    "bottomMargin": 5,
}

TEXT = {
    "kind": "text",
    "box": {"x": 5, "y": 5, "width": 40, "height": 6},
    "font": "body",
    "color": "#000000",
    "align": "left",
    "leading": 6,
    "lines": ["a"],
}

IMAGE = {
    "kind": "image",
    "box": {"x": 5, "y": 11, "width": 10, "height": 5},
    "data": "pic.png",
}

STRIPES = {
    "kind": "barcode",
    "box": {"x": 5, "y": 16, "width": 30, "height": 18},
    "type": "Code128",
    "value": "A",
    "module": 0.5,
    "vertical": False,
    "ink": "#000000",
    "stripes": [10, 20, 30],
}

ROWS = {
    "kind": "barcode",
    "box": {"x": 40, "y": 16, "width": 6, "height": 4},
    "type": "Aztec",
    "value": "A",
    "module": 2,
    "vertical": False,
    "ink": "#000000",
    "rows": [[1, 2], [0, 3]],
}

OUTLINE = {
    "kind": "outline",
    "title": "top",
    "level": 1,
    "name": "top",
    "closed": False,
    "x": 5,
    "y": 5,
}

XREF = {
    "kind": "xref",
    "box": {"x": 50, "y": 5, "width": 40, "height": 6},
    "type": "outline",
    "target": "top",
    "marks": [dict(TEXT, box={"x": 50, "y": 5, "width": 40, "height": 6})],
}


def printout() -> list[Any]:
    """Return the records of a printout that keeps every invariant."""
    header = {
        "sr": 1,
        "kind": "header",
        "report": {"name": "Verify"},
        "built": "2026-08-04T09:12:44Z",
        "engine": "sr 0.1.0",
        "strictFonts": True,
        "pages": 1,
        "page": dict(PAGE),
        "fonts": [
            {
                "name": "body",
                "size": 5,
                "bold": False,
                "italic": False,
                "underline": False,
                "resolvedFile": "Go-Regular.ttf",
                "resolvedFace": "Go",
                "resolvedBy": "explicit",
            }
        ],
        "data": {"pic.png": {"encoding": "base64", "content": "AA=="}},
    }
    page = {
        "kind": "page",
        "number": 1,
        "marks": [
            copy.deepcopy(one) for one in (OUTLINE, TEXT, IMAGE, STRIPES, ROWS, XREF)
        ],
    }
    return [header, page]


def marks(records: list[Any]) -> list[dict[str, Any]]:
    """Return the first page's marks, to change one."""
    found: list[dict[str, Any]] = records[1]["marks"]
    return found


def test_a_printout_that_keeps_them_all_breaks_none() -> None:
    assert violations(printout()) == []


def test_1_a_version_the_reader_does_not_understand() -> None:
    records = printout()
    records[0]["sr"] = 2
    assert violations(records) == [
        "the header's format version is 2, and this reader understands 1"
    ]


def test_2_a_page_count_that_is_not_the_lines_that_follow() -> None:
    records = printout()
    records[0]["pages"] = 3
    assert violations(records) == ["the header counts 3 pages, and 1 page lines follow"]


def test_3_a_text_marks_font_the_header_does_not_hold() -> None:
    records = printout()
    marks(records)[1]["font"] = "bold"
    assert violations(records) == [
        'page 1, mark 2: its font "bold" is not in the header\'s font table'
    ]


def test_4_data_the_header_does_not_hold() -> None:
    records = printout()
    marks(records)[2]["data"] = "logo.png"
    records[0]["fonts"][0]["resolvedData"] = "face"
    assert violations(records) == [
        'font "body": its data "face" is not in the header\'s data table',
        'page 1, mark 3: its data "logo.png" is not in the header\'s data table',
    ]


def test_5_an_xref_to_an_outline_entry_nobody_is_named() -> None:
    records = printout()
    marks(records)[5]["target"] = "bottom"
    assert violations(records) == [
        'page 1, mark 6: the xref targets the outline entry "bottom", '
        "and no entry is named so"
    ]


def test_5_an_outline_xref_with_no_target() -> None:
    """An entry with no name is not one an absent target finds."""
    records = printout()
    del marks(records)[0]["name"]
    del marks(records)[5]["target"]
    assert violations(records) == [
        "page 1, mark 6: the xref to an outline entry has no target"
    ]


@pytest.mark.parametrize(
    ("index", "key", "said"),
    [
        (1, "font", "page 1, mark 2: its font ['x'] is not in the header's"),
        (2, "data", "page 1, mark 3: its data ['x'] is not in the header's"),
        (5, "target", "page 1, mark 6: the xref targets the outline entry"),
    ],
)
def test_a_name_that_is_not_a_string_is_in_no_table(
    index: int, key: str, said: str
) -> None:
    """An array cannot be hashed, and is reported rather than looked up."""
    records = printout()
    marks(records)[index][key] = ["x"]
    (found,) = violations(records)
    assert found.startswith(said)


def test_an_entry_or_a_font_named_by_an_array_names_nothing() -> None:
    records = printout()
    marks(records)[0]["name"] = ["top"]
    records[0]["fonts"][0]["name"] = ["body"]
    assert violations(records) == [
        'page 1, mark 2: its font "body" is not in the header\'s font table',
        'page 1, mark 6.1: its font "body" is not in the header\'s font table',
        'page 1, mark 6: the xref targets the outline entry "top", '
        "and no entry is named so",
    ]


def test_6_a_negative_extent() -> None:
    records = printout()
    marks(records)[2]["box"]["height"] = -1
    assert violations(records) == [
        "page 1, mark 3: the image's box has a negative height, -1"
    ]


@pytest.mark.parametrize(
    ("box", "said"),
    [
        (
            {"x": 4, "y": 5},
            "the text's left edge is at 4 pt, past the left margin at 5 pt",
        ),
        (
            {"x": 60},
            "the text's right edge is at 100 pt, past the right margin at 95 pt",
        ),
        (
            {"y": 4.998},
            "the text's top edge is at 4.998 pt, past the top margin at 5 pt",
        ),
        (
            {"y": 50},
            "the text's bottom edge is at 56 pt, past the bottom margin at 55 pt",
        ),
    ],
)
def test_7_a_mark_outside_the_printable_area(box: dict[str, float], said: str) -> None:
    records = printout()
    marks(records)[1]["box"].update(box)
    assert violations(records) == [f"page 1, mark 2: {said}"]


def test_7_an_xrefs_marks_and_an_outline_entrys_point_are_judged_too() -> None:
    records = printout()
    marks(records)[5]["marks"][0]["box"]["x"] = 0
    marks(records)[0]["y"] = 56
    assert violations(records) == [
        "page 1, mark 1: the outline entry is at 56 pt, "
        "past the bottom margin at 55 pt",
        "page 1, mark 6.1: the text's left edge is at 0 pt, "
        "past the left margin at 5 pt",
    ]


def test_7_the_tolerance_admits_a_thousandth() -> None:
    records = printout()
    marks(records)[1]["box"]["x"] = 4.999
    assert violations(records) == []


def test_7_an_overflow_warning_waives_it() -> None:
    records = printout()
    marks(records)[1]["box"]["x"] = 0
    records[0]["warnings"] = [{"kind": "overflow", "message": "it overflowed"}]
    assert violations(records) == []


def test_7_a_page_is_judged_by_its_own_margins() -> None:
    records = printout()
    records[1]["leftMargin"] = 0
    marks(records)[1]["box"]["x"] = 0
    assert violations(records) == []


@pytest.mark.parametrize(
    ("page", "said"),
    [
        (None, "the header has no page object"),
        (
            {**PAGE, "width": "wide"},
            'the header\'s page width is "wide", not a number',
        ),
        (
            {**PAGE, "leftMargin": None},
            "the header's page has no leftMargin",
        ),
        (
            {**PAGE, "height": 1e400},
            "the header's page height is inf, not a number",
        ),
    ],
)
def test_7_a_header_whose_geometry_cannot_be_read(
    page: dict[str, Any] | None, said: str
) -> None:
    """The margins cannot be measured, and that is said, not passed over."""
    records = printout()
    if page is None:
        del records[0]["page"]
    else:
        records[0]["page"] = page
    marks(records)[1]["box"]["x"] = -500
    assert violations(records) == [said]


def test_7_a_page_whose_override_cannot_be_read() -> None:
    records = printout()
    records[1]["width"] = [100]
    marks(records)[1]["box"]["x"] = -500
    assert violations(records) == ["page 1: its width is [100], not a number"]


def test_8_a_text_mark_with_no_lines() -> None:
    records = printout()
    marks(records)[1]["lines"] = []
    assert violations(records) == ["page 1, mark 2: the text has no lines"]


def test_8_lines_that_need_more_than_the_box() -> None:
    records = printout()
    marks(records)[1]["lines"] = ["a", "b"]
    assert violations(records) == [
        "page 1, mark 2: 2 lines of 6 pt need 12 pt, in a box 6 pt tall"
    ]


def test_9_stripes_that_are_not_the_boxs_length() -> None:
    records = printout()
    marks(records)[3]["stripes"] = [10, 20, 31]
    assert violations(records) == [
        "page 1, mark 4: the stripes make 61 modules of 0.5 pt, 30.5 pt, "
        "and the box is 30 pt long"
    ]


def test_9_a_vertical_symbol_runs_down_its_box() -> None:
    records = printout()
    marks(records)[3].update(vertical=True)
    marks(records)[3]["box"].update(width=18, height=30)
    assert violations(records) == []


def test_9_rows_of_different_lengths() -> None:
    records = printout()
    marks(records)[4]["rows"] = [[1, 2], [0, 4]]
    assert violations(records) == [
        "page 1, mark 5: the barcode's rows are not all one length"
    ]


def test_9_rows_that_are_not_the_boxs_depth() -> None:
    records = printout()
    marks(records)[4]["rows"] = [[1, 2], [0, 3], [3]]
    assert violations(records) == [
        "page 1, mark 5: the rows make 3 modules of 2 pt, 6 pt, "
        "and the box is 4 pt across"
    ]


def test_9_a_symbol_with_both_kinds_of_runs() -> None:
    records = printout()
    marks(records)[4]["stripes"] = [3]
    assert violations(records) == [
        "page 1, mark 5: the barcode carries both stripes and rows"
    ]


def test_10_a_level_that_jumps() -> None:
    records = printout()
    marks(records).append(dict(OUTLINE, level=3, y=20))
    assert violations(records) == [
        "page 1, mark 7: the outline entry is at level 3, "
        "and the one before it at level 1"
    ]


def test_10_the_first_entry_is_at_level_1() -> None:
    records = printout()
    marks(records)[0]["level"] = 2
    assert violations(records) == [
        "page 1, mark 1: the outline entry is at level 2, "
        "and the one before it at level 0"
    ]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"file": "pic.png"}, "both data and file"),
        ({"data": None}, "neither data nor file"),
    ],
)
def test_11_an_image_with_both_sources_or_neither(
    change: dict[str, Any], said: str
) -> None:
    records = printout()
    marks(records)[2].update(change)
    if change.get("data", "") is None:
        del marks(records)[2]["data"]
    assert violations(records) == [f"page 1, mark 3: the image carries {said}"]


def test_12_a_barcode_with_no_ink() -> None:
    records = printout()
    del marks(records)[3]["ink"]
    assert violations(records) == ["page 1, mark 4: the barcode carries no ink"]


def test_a_record_that_is_not_a_printout_is_said_to_be_one() -> None:
    assert violations([]) == ["the first record is not a header"]
    records = printout()
    records[1]["marks"] = None
    assert violations(records) == ["page 1: it has no marks array"]


# -- the model --------------------------------------------------------


def test_a_printout_in_memory_is_checked_as_written(tmp_path: Path) -> None:
    made = Printout(
        report=Report("Verify"),
        built="2026-08-04T09:12:44Z",
        engine="sr 0.1.0",
        strict_fonts=True,
        paper=Paper(100, 60, 5, 5, 5, 5),
        fonts=(
            FontEntry(
                "body",
                5,
                False,
                False,
                False,
                "Go",
                "explicit",
                file=tmp_path / "f.ttf",
            ),
        ),
        pages=(
            Page(
                1,
                (
                    Outline(Box(5, 5, 0, 0), "top", 2, None, False),
                    Text(Box(5, 5, 40, 6), "body", "#000000", "left", 6, ("a",)),
                    Barcode(
                        Box(5, 11, 3, 18),
                        "Code128",
                        "A",
                        1,
                        False,
                        "#000000",
                        None,
                        stripes=(1, 1, 1),
                    ),
                ),
            ),
        ),
    )
    assert check(made) == [
        "page 1, mark 1: the outline entry is at level 2, "
        "and the one before it at level 0"
    ]


# -- the margins ------------------------------------------------------


AREA = Area.of(100, 60, 5, 5, 5, 5)


def test_an_area_is_inside_the_margins() -> None:
    assert AREA == Area(5, 5, 95, 55)


def test_a_box_flush_with_every_margin_lies_inside() -> None:
    assert crossing(AREA, 5, 5, 90, 50) is None


def test_the_first_side_crossed_is_the_answer() -> None:
    """Left, right, top, and bottom, in that order."""
    assert crossing(AREA, 0, 0, 100, 60) == Crossing("left", 0, 5)
    assert crossing(AREA, 5, 0, 100, 60) == Crossing("right", 105, 95)


def test_the_tolerance_is_added_as_for_a_frame() -> None:
    """doc/layout.md#coordinates-and-rounding: 1.001 + 0.001 falls short."""
    narrow = Area(0, 0, 1.001, 100)
    assert crossing(narrow, 0, 0, 1.002) == Crossing("right", 1.002, 1.001)
    wide = Area(0, 0, 20.003, 100)
    assert crossing(wide, 0, 0, 20.004) is None


def test_a_crossing_is_described_with_its_measured_values() -> None:
    assert described("barcode", Crossing("top", 2.5, 5)) == (
        "the barcode's top edge is at 2.5 pt, past the top margin at 5 pt"
    )
    assert described("outline", Crossing("left", 0, 5)) == (
        "the outline entry is at 0 pt, past the left margin at 5 pt"
    )
