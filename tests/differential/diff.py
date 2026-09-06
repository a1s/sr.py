"""Comparing two printouts, and saying what differs.

The verdict is on the bytes, because that is what the goal is stated in.
The *report* is structural, because a byte offset into a 30 kB line of JSON
says nothing a person can act on.

A printout is NDJSON with one record per line, and a page is one record
carrying every mark on it.  So the report descends: the record, then the
mark inside it, then the fields of that mark and both spellings of each.
A one-point disagreement in a band height is meant to read as the mark
it moved, not as a page that differs.

"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

__all__ = ["Comparison", "compare_printouts", "first_difference"]

# How much of a systemic disagreement to show.  A difference in an early
# coordinate moves everything after it, so the whole page differs and
# the first few marks are the only informative part.
MAX_RECORDS_SHOWN = 3
MAX_MARKS_SHOWN = 5
MAX_FIELDS_NAMED = 12
MAX_DIFF_LINES = 60
MAX_VALUE_CHARS = 140

# An xref nests marks under it, which doc/printout.md calls the only nesting
# a printout has.  One level of descent is therefore enough; the bound is
# here so that a format which grows another cannot loop.
MAX_MARK_DEPTH = 4

HEX_CONTEXT = 32


@dataclass(frozen=True)
class Comparison:
    """The verdict on two printouts, and a report when they disagree."""

    identical: bool
    report: str

    def __bool__(self) -> bool:
        """Report whether the two printouts are byte-identical."""
        return self.identical


def first_difference(left: bytes, right: bytes) -> int | None:
    """Return the offset of the first differing byte, or None if equal.

    A common prefix followed by one file ending early
    counts as a difference at the length of the shorter.

    """
    if left == right:
        return None
    for offset, (one, other) in enumerate(zip(left, right, strict=False)):
        if one != other:
            return offset
    return min(len(left), len(right))


def parse_ndjson(data: bytes) -> list[Any] | None:
    """Return one value per line, or None when this is not NDJSON.

    Returning None is how a CBOR printout, or a truncated one,
    falls back to the byte-level report.

    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    records: list[Any] = []
    # split, not splitlines: the latter also breaks on U+0085, U+2028,
    # U+2029 and the file separators, and a raw U+0085 is legal inside a
    # JSON string.  Splitting there would cut one record into two halves
    # that neither parse, and the whole report would fall back to a hexdump.
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    return records


def at(values: list[Any], index: int) -> Any:
    """Return ``values[index]``, or None past the end.

    Aligning two lists of different lengths by index is
    what lets a mark only one side has be reported as itself.

    """
    return values[index] if index < len(values) else None


def brief(value: Any) -> str:
    """Render a value on one line, cut to a length that still reads."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > MAX_VALUE_CHARS:
        text = text[: MAX_VALUE_CHARS - 3] + "..."
    return text


def differing_fields(left: Any, right: Any) -> list[str]:
    """Return the keys whose values differ, in the order they are written."""
    if not (isinstance(left, dict) and isinstance(right, dict)):
        return []
    keys = list(dict.fromkeys([*left, *right]))
    return [key for key in keys if left.get(key) != right.get(key)]


def named(fields: list[str]) -> str:
    """Render a field list, saying how many were left out."""
    shown = ", ".join(fields[:MAX_FIELDS_NAMED])
    if len(fields) > MAX_FIELDS_NAMED:
        shown += f", and {len(fields) - MAX_FIELDS_NAMED} more"
    return shown


def record_label(index: int, record: Any) -> str:
    """Name a printout record the way doc/printout.md names it."""
    if not isinstance(record, dict):
        return f"record {index}"
    kind = record.get("kind")
    if kind == "header":
        return "header"
    if kind == "page":
        return f"page {record.get('number', index)}"
    if isinstance(kind, str):
        return f"record {index} ({kind})"
    return f"record {index}"


def mark_label(index: int, mark: Any) -> str:
    """Name a mark by its position and its kind."""
    if isinstance(mark, dict) and isinstance(mark.get("kind"), str):
        return f"mark {index} ({mark['kind']})"
    return f"mark {index}"


def marks_of(record: Any) -> list[Any] | None:
    """Return a record's or a mark's nested marks, if it carries any."""
    if isinstance(record, dict) and isinstance(record.get("marks"), list):
        marks: list[Any] = record["marks"]
        return marks
    return None


def value_of(record: Any, field: str) -> Any:
    """Return one field of a record, or the record itself when it has none."""
    return record.get(field) if isinstance(record, dict) else record


def field_report(
    left: Any, right: Any, field: str, left_name: str, right_name: str, indent: str
) -> list[str]:
    """Render both spellings of one field."""
    return [
        f"{indent}{field}",
        f"{indent}  {left_name} {brief(value_of(left, field))}",
        f"{indent}  {right_name} {brief(value_of(right, field))}",
    ]


def mark_report(
    left_marks: list[Any],
    right_marks: list[Any],
    left_name: str,
    right_name: str,
    indent: str,
    depth: int = 0,
) -> list[str]:
    """Report the differing marks, descending into the ones that nest."""
    total = max(len(left_marks), len(right_marks))
    lines: list[str] = []
    if len(left_marks) != len(right_marks):
        lines.append(
            f"{indent}mark count: {left_name} {len(left_marks)}, "
            f"{right_name} {len(right_marks)}"
        )
    differing = [
        index
        for index in range(total)
        if at(left_marks, index) != at(right_marks, index)
    ]
    if not differing:
        return lines
    lines.append(f"{indent}{len(differing)} of {total} marks differ")
    for index in differing[:MAX_MARKS_SHOWN]:
        one = at(left_marks, index)
        other = at(right_marks, index)
        label = mark_label(index, one if isinstance(one, dict) else other)
        fields = differing_fields(one, other)
        nested_left = marks_of(one)
        nested_right = marks_of(other)
        if (
            "marks" in fields
            and nested_left is not None
            and nested_right is not None
            and depth < MAX_MARK_DEPTH
        ):
            # A container that moved is what moves its children, so a box
            # and a `marks` that both differ is the ordinary case rather
            # than the awkward one.  Report the container's own fields,
            # then descend -- never render `marks` as a value, which at this
            # size is a truncated blob with the interesting part cut off.
            own = [field for field in fields if field != "marks"]
            lines.append(
                f"{indent}{label} differs: {named(own)}" if own else f"{indent}{label}"
            )
            for field in own[:MAX_FIELDS_NAMED]:
                lines += field_report(
                    one, other, field, left_name, right_name, indent + "  "
                )
            lines += mark_report(
                nested_left,
                nested_right,
                left_name,
                right_name,
                indent + "  ",
                depth + 1,
            )
            continue
        if not fields:
            # One side has no mark here at all, or the two are not both
            # objects: there are no fields to line up, so show them whole.
            lines.append(f"{indent}{label} differs")
            lines += [
                f"{indent}  {left_name} {brief(one)}",
                f"{indent}  {right_name} {brief(other)}",
            ]
            continue
        lines.append(f"{indent}{label} differs: {named(fields)}")
        for field in fields[:MAX_FIELDS_NAMED]:
            lines += field_report(
                one, other, field, left_name, right_name, indent + "  "
            )
    if len(differing) > MAX_MARKS_SHOWN:
        lines.append(
            f"{indent}... and {len(differing) - MAX_MARKS_SHOWN} further marks"
        )
    return lines


def truncate(lines: list[str], limit: int) -> list[str]:
    """Cut ``lines`` to ``limit``, saying how much was left out."""
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"... {len(lines) - limit} more lines"]


def pretty(record: Any) -> list[str]:
    """Render a record as indented JSON lines, for the unified diff."""
    text = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)
    return text.splitlines()


def unified(left: Any, right: Any, left_name: str, right_name: str) -> list[str]:
    """Diff two records as indented JSON, for a record that holds no marks."""
    diff = difflib.unified_diff(
        pretty(left),
        pretty(right),
        fromfile=left_name,
        tofile=right_name,
        lineterm="",
        n=1,
    )
    return truncate(list(diff), MAX_DIFF_LINES)


def record_report(
    index: int, left: Any, right: Any, left_name: str, right_name: str
) -> list[str]:
    """Report one differing record: which fields, then what is inside them."""
    lines = [f"  {record_label(index, left if left is not None else right)} differs"]
    fields = differing_fields(left, right)
    if fields:
        lines.append(f"    fields: {named(fields)}")
    left_marks = marks_of(left)
    right_marks = marks_of(right)
    if left_marks is not None and right_marks is not None and "marks" in fields:
        # The record's own fields first -- a page that changed size explains
        # the marks that moved with it -- and then the marks, which are what
        # a person came here for.  `marks` itself is never shown as a value.
        for field in (item for item in fields if item != "marks"):
            lines += field_report(left, right, field, left_name, right_name, "      ")
        return lines + mark_report(
            left_marks, right_marks, left_name, right_name, "    "
        )
    return lines + [
        "    " + line for line in unified(left, right, left_name, right_name)
    ]


def structural_report(
    left_records: list[Any],
    right_records: list[Any],
    left_name: str,
    right_name: str,
) -> list[str]:
    """Report the differing records, most useful first."""
    total = max(len(left_records), len(right_records))
    lines: list[str] = []
    if len(left_records) != len(right_records):
        lines.append(
            f"  record count: {left_name} {len(left_records)}, "
            f"{right_name} {len(right_records)}"
        )
    differing = [
        index
        for index in range(total)
        if at(left_records, index) != at(right_records, index)
    ]
    if not differing:
        # Equal record by record, so the difference is in the framing: a
        # trailing newline, key order, the spelling of a number.
        lines.append("  records are equal; the difference is in the encoding")
        return lines
    lines.append(f"  {len(differing)} of {total} records differ")
    for index in differing[:MAX_RECORDS_SHOWN]:
        lines += record_report(
            index,
            at(left_records, index),
            at(right_records, index),
            left_name,
            right_name,
        )
    if len(differing) > MAX_RECORDS_SHOWN:
        lines.append(f"  ... and {len(differing) - MAX_RECORDS_SHOWN} further records")
    return lines


def hexdump_window(data: bytes, offset: int) -> str:
    """Return the bytes around ``offset`` as hex, with printable ASCII."""
    start = max(0, offset - HEX_CONTEXT)
    window = data[start : offset + HEX_CONTEXT]
    hexed = window.hex(" ")
    text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in window)
    return f"@{start} {hexed}\n     {text}"


def compare_printouts(
    left: bytes,
    right: bytes,
    left_name: str = "reference",
    right_name: str = "local",
    title: str = "",
) -> Comparison:
    """Compare two printouts and describe the difference.

    Args:
        left: The bytes one engine wrote.
        right: The bytes the other wrote.
        left_name: What to call the first, in the report.
        right_name: What to call the second.
        title: A case name for the report's first line.

    Returns:
        A :class:`Comparison`, whose ``report`` is empty when the two agree.

    """
    offset = first_difference(left, right)
    if offset is None:
        return Comparison(True, "")

    lines = [f"printouts differ: {title}" if title else "printouts differ"]
    lines.append(
        f"  {left_name}: {len(left)} bytes, {right_name}: {len(right)} bytes, "
        f"first difference at byte {offset}"
    )
    left_records = parse_ndjson(left)
    right_records = parse_ndjson(right)
    if left_records is None or right_records is None:
        lines.append(f"  {left_name}:")
        lines.append("  " + hexdump_window(left, offset))
        lines.append(f"  {right_name}:")
        lines.append("  " + hexdump_window(right, offset))
    else:
        lines += structural_report(left_records, right_records, left_name, right_name)
    return Comparison(False, "\n".join(lines))
