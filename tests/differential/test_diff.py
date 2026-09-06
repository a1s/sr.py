"""Tests for the printout diff.

The diff is what makes a failure in M7 or M8 attributable,
so it is worth its own tests: a report that points at the wrong mark
is worse than no report at all.

"""

from __future__ import annotations

import json
from typing import Any

from tests.differential.diff import (
    MAX_MARKS_SHOWN,
    MAX_RECORDS_SHOWN,
    compare_printouts,
    first_difference,
)


def ndjson(*records: dict[str, Any]) -> bytes:
    """Encode records the way a printout is encoded: one JSON object a line."""
    lines = [json.dumps(record, separators=(",", ":")) for record in records]
    return ("\n".join(lines) + "\n").encode("utf-8")


def box(left: float, top: float, width: float, height: float) -> dict[str, float]:
    """Build a mark's box, spelled as doc/printout.md spells it."""
    return {"x": left, "y": top, "width": width, "height": height}


def text_mark(content: str, top: float = 42.52) -> dict[str, Any]:
    """Build a text mark, the kind a layout disagreement usually lands on."""
    return {
        "kind": "text",
        "box": box(70.866, top, 226.772, 9.6),
        "font": "body",
        "color": "#000000",
        "align": "left",
        "leading": 9.6,
        "lines": [content],
    }


def page(*marks: dict[str, Any], number: int = 1) -> dict[str, Any]:
    """Build a page record, which carries every mark on it."""
    return {"kind": "page", "number": number, "marks": list(marks)}


HEADER = {"sr": 1, "kind": "header", "engine": "sr 0.1.0", "pages": 1}


def test_first_difference_reports_none_when_equal() -> None:
    assert first_difference(b"identical", b"identical") is None


def test_first_difference_reports_the_offset() -> None:
    assert first_difference(b"abc", b"abd") == 2


def test_first_difference_treats_a_short_file_as_differing_at_its_end() -> None:
    assert first_difference(b"ab", b"abc") == 2


def test_identical_printouts_compare_equal() -> None:
    printout = ndjson(HEADER, page(text_mark("Film title")))
    comparison = compare_printouts(printout, printout)
    assert comparison
    assert comparison.report == ""


def test_a_differing_header_field_is_named() -> None:
    left = ndjson(HEADER)
    right = ndjson(dict(HEADER, pages=2))
    comparison = compare_printouts(left, right)
    assert not comparison
    assert "header differs" in comparison.report
    assert "fields: pages" in comparison.report


def test_a_header_falls_back_to_a_unified_diff() -> None:
    """A record with no marks is shown as a diff of its JSON."""
    report = compare_printouts(ndjson(HEADER), ndjson(dict(HEADER, pages=2))).report
    assert "-  " in report or '"pages"' in report


def test_a_moved_mark_is_named_by_its_kind_and_position() -> None:
    left = ndjson(HEADER, page(text_mark("one"), text_mark("two", top=52.12)))
    right = ndjson(HEADER, page(text_mark("one"), text_mark("two", top=52.13)))
    report = compare_printouts(left, right).report
    assert "page 1 differs" in report
    assert "1 of 2 marks differ" in report
    assert "mark 1 (text) differs: box" in report


def test_both_spellings_of_a_differing_field_are_shown() -> None:
    left = ndjson(HEADER, page(text_mark("Film title")))
    right = ndjson(HEADER, page(text_mark("Film  title")))
    report = compare_printouts(left, right).report
    assert 'reference ["Film title"]' in report
    assert 'local ["Film  title"]' in report


def test_a_mark_count_difference_is_reported() -> None:
    left = ndjson(HEADER, page(text_mark("one")))
    right = ndjson(HEADER, page(text_mark("one"), text_mark("two", top=52.12)))
    report = compare_printouts(left, right).report
    assert "mark count: reference 1, local 2" in report


def test_the_diff_descends_into_an_xref() -> None:
    """An xref nests marks, which doc/printout.md calls the only nesting."""
    inner = text_mark("DVD rental payments")
    xref = {
        "kind": "xref",
        "box": box(394.866, 42.52, 157.89, 28.346),
        "type": "url",
        "target": "https://example.invalid/",
        "marks": [inner],
    }
    moved = dict(xref, marks=[text_mark("DVD rental payments", top=42.53)])
    report = compare_printouts(
        ndjson(HEADER, page(xref)), ndjson(HEADER, page(moved))
    ).report
    assert "mark 0 (xref)" in report
    assert "mark 0 (text) differs: box" in report


def test_the_diff_descends_when_the_container_moved_too() -> None:
    """A container that moved is what moved its children.

    So a mark whose own box *and* whose ``marks`` both differ is
    the ordinary case, not the corner one.  Descending only when
    nothing else differs would render the children as a truncated
    JSON blob with the interesting part cut off.

    """
    inner = text_mark("DVD rental payments")
    xref = {
        "kind": "xref",
        "box": box(394.866, 42.52, 157.89, 28.346),
        "type": "url",
        "target": "https://example.invalid/",
        "marks": [inner],
    }
    shifted = dict(
        xref,
        box=box(394.87, 42.52, 157.89, 28.346),
        marks=[text_mark("DVD rental payments", top=42.53)],
    )
    report = compare_printouts(
        ndjson(HEADER, page(xref)), ndjson(HEADER, page(shifted))
    ).report
    # The container's own field, with both spellings...
    assert "mark 0 (xref) differs: box" in report
    assert "reference {" in report
    # ...and the child, rather than `marks` rendered as a value.
    assert "mark 0 (text) differs: box" in report
    assert "  marks\n" not in report


def test_marks_are_never_shown_as_a_value() -> None:
    """The one field a report must not print is the one holding everything."""
    left = page(text_mark("one"))
    right = dict(page(text_mark("two")), number=2)
    report = compare_printouts(ndjson(left), ndjson(right)).report
    assert "fields: number, marks" in report
    assert '"kind": "text"' not in report


def test_the_mark_report_is_truncated() -> None:
    count = MAX_MARKS_SHOWN + 4
    left = ndjson(
        HEADER, page(*(text_mark("row", top=index) for index in range(count)))
    )
    right = ndjson(
        HEADER, page(*(text_mark("row", top=index + 0.5) for index in range(count)))
    )
    report = compare_printouts(left, right).report
    assert f"{count} of {count} marks differ" in report
    assert f"and {count - MAX_MARKS_SHOWN} further marks" in report


def test_the_record_report_is_truncated() -> None:
    count = MAX_RECORDS_SHOWN + 2
    left = ndjson(*(page(number=index) for index in range(count)))
    right = ndjson(*(page(text_mark("x"), number=index) for index in range(count)))
    report = compare_printouts(left, right).report
    assert f"and {count - MAX_RECORDS_SHOWN} further records" in report


def test_a_record_count_mismatch_is_reported() -> None:
    left = ndjson(HEADER, page())
    right = ndjson(HEADER, page(), page(number=2))
    report = compare_printouts(left, right).report
    assert "record count: reference 2, local 3" in report


def test_equal_records_with_different_bytes_say_so() -> None:
    left = ndjson(HEADER)
    right = (json.dumps(HEADER, indent=None) + "\n").encode("utf-8")
    comparison = compare_printouts(left, right)
    assert not comparison
    assert "the difference is in the encoding" in comparison.report


def test_a_record_is_split_only_on_newlines() -> None:
    """U+0085 is legal raw in a JSON string, and Go does not escape it.

    ``str.splitlines`` breaks on it, which would cut one record into two
    halves that neither parse and drop the whole report to a hexdump.
    Go's encoder escapes U+2028 and U+2029, so this is the live one.
    Spelled with chr() rather than written into the literal, because
    an invisible control character in a source file is its own small trap.

    """
    nel = chr(0x85)
    left = ndjson(HEADER, page(text_mark(f"first line{nel}second line")))
    right = ndjson(HEADER, page(text_mark(f"first line{nel}other line")))
    comparison = compare_printouts(left, right)
    assert not comparison
    assert "mark 0 (text) differs: lines" in comparison.report
    assert "@" not in comparison.report, "fell back to the byte-level report"


def test_a_binary_printout_falls_back_to_bytes() -> None:
    left = b"\xa2\x62sr\x01\x64kind"
    right = b"\xa2\x62sr\x02\x64kind"
    comparison = compare_printouts(left, right, title="probe/cbor")
    assert not comparison
    assert "printouts differ: probe/cbor" in comparison.report
    assert "@0" in comparison.report


def test_the_engine_names_reach_the_report() -> None:
    left = ndjson(HEADER)
    right = ndjson(dict(HEADER, pages=9))
    report = compare_printouts(left, right, "go", "python").report
    assert "go: " in report
    assert "python: " in report
