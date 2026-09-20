"""Reading a printout back, and dumping it as text.

doc/cli.md#sr-inspect: one mark per line, in paint order, with a text
mark's lines quoted under it.  The printout is machine-generated JSON;
this is the same content in a form that can be read and diffed.

The reader is deliberately **untyped**.  It parses the records into
plain dictionaries rather than into :mod:`sr.printout.model`, so a
printout holding a mark this engine does not build yet still dumps,
and so does one another engine wrote.  That is what makes `inspect`
useful while the engine is being written: it reads the oracle's output
as readily as its own.  The typed reader arrives with the renderer,
which needs one.

The [invariant check](doc/printout.md#invariants) for which doc/cli.md
asks this command arrives with the rest of the printout in M13.
What is here is the dump.

"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from sr.errors import BuildError, Location
from sr.printout.write import number

__all__ = ["dump", "pages_wanted", "read_jsonl", "records_of"]

# The fields each mark kind shows, in the order doc/cli.md#sr-inspect
# writes them.  A field the mark does not carry is left out rather than
# written empty, so a rectangle with no `stroke` shows no `width`.
MARK_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("font", "color", "align", "leading"),
    "line": ("width", "dash", "color", "backslant"),
    "rectangle": ("stroke", "width", "dash", "fill", "radius"),
    "image": ("type", "data", "file"),
    "barcode": ("type", "value", "module", "vertical", "ink", "paper"),
    "outline": ("title", "level", "name", "closed"),
    "xref": ("type", "target", "caption"),
}

# The fields that are shown as a bare value rather than as `name value`,
# because the name would repeat what the value already says.
BARE = {"type", "value", "title", "target"}

# A rectangle's `width` is its stroke width and means nothing
# without an outline, so it is shown only beside one.
WITH_STROKE = {("rectangle", "width"), ("rectangle", "dash")}


def read_jsonl(path: Path) -> list[Any]:
    """Return the records an NDJSON printout holds.

    Args:
        path: The file to read.

    Raises:
        BuildError: The file is not NDJSON, or holds no header.

    """
    text = path.read_text(encoding="utf-8")
    records = []
    for index, line in enumerate(text.split("\n")):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError as refused:
            raise BuildError(
                f"not a printout: {refused}",
                Location(file=str(path), line=index + 1),
            ) from None
    if not records or not isinstance(records[0], dict):
        raise BuildError("not a printout: no header line", Location(file=str(path)))
    return records


def records_of(records: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split a printout's records into its header and its pages.

    Args:
        records: What the file held, in order.

    """
    header = records[0]
    pages = [one for one in records[1:] if isinstance(one, dict)]
    return header, pages


def pages_wanted(text: str | None, count: int) -> set[int] | None:
    """Return the page numbers ``--pages`` names, or ``None`` for all.

    ``1``, ``4-6``, ``10-`` and ``-3`` are all ranges and they
    combine with commas.  An open end means the first or the last
    page, so a bare ``-`` is every page.

    Args:
        text: The flag's value, where one was given.
        count: How many pages the printout has.

    Raises:
        BuildError: A range is not a range.

    """
    if text is None:
        return None
    wanted: set[int] = set()
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        if "-" not in piece:
            wanted.add(as_page(piece))
            continue
        low, _, high = piece.partition("-")
        first = as_page(low) if low.strip() else 1
        last = as_page(high) if high.strip() else count
        wanted.update(range(first, last + 1))
    return wanted


def as_page(text: str) -> int:
    """Return one page number a ``--pages`` range names.

    Args:
        text: The number, as it was written.

    Raises:
        BuildError: It is not a page number.

    """
    try:
        return int(text.strip())
    except ValueError:
        raise BuildError(f"--pages: not a page number: {text.strip()!r}") from None


def dump(
    path: Path, records: list[Any], pages: set[int] | None, summary: bool = False
) -> Iterator[str]:
    """Yield the dump of a printout, line by line.

    Args:
        path: The file it was read from, for the first line.
        records: What the file held.
        pages: The page numbers to show, or ``None`` for all.
        summary: Whether to show the header alone.

    """
    header, page_lines = records_of(records)
    yield from header_lines(path, header, len(page_lines))
    if summary:
        return
    for page in page_lines:
        if pages is not None and page.get("number") not in pages:
            continue
        yield from page_dump(page, header)


def header_lines(path: Path, header: dict[str, Any], count: int) -> Iterator[str]:
    """Yield the header's own lines and the tables under it.

    Args:
        path: The file, as the caller named it.
        header: The header record.
        count: How many page lines followed it.

    """
    yield f"printout {path.as_posix()}"
    made = [
        f"format {header.get('sr', '?')}",
        f"engine {quote(str(header.get('engine', '')))}",
        f"built {header.get('built', '')}",
    ]
    if header.get("strictFonts"):
        made.append("strict fonts")
    yield "  " + ", ".join(made)
    report = header.get("report") or {}
    yield "  " + headline(report)
    if report.get("description"):
        yield f"  {report['description']}"
    page = header.get("page") or {}
    if page:
        yield (
            f"  page {number(page.get('width', 0))} x "
            f"{number(page.get('height', 0))} pt, margins "
            f"left {number(page.get('leftMargin', 0))} "
            f"right {number(page.get('rightMargin', 0))} "
            f"top {number(page.get('topMargin', 0))} "
            f"bottom {number(page.get('bottomMargin', 0))}"
        )
    yield "  " + tally(count, header)
    yield from section("groups", group_lines(header))
    yield from section("fonts", font_lines(header.get("fonts") or []))
    yield from section("data", data_lines(header.get("data") or {}))
    yield from section("warnings", warning_lines(header.get("warnings") or []))


def headline(report: dict[str, Any]) -> str:
    """Return the one line that names a report.

    Args:
        report: The header's `report` object.

    """
    parts = ["report", quote(report["name"]) if report.get("name") else "(unnamed)"]
    if report.get("version"):
        parts.append(f"version {report['version']}")
    if report.get("author"):
        parts.append(f"by {report['author']}")
    return " ".join(parts)


def tally(count: int, header: dict[str, Any]) -> str:
    """Return the line that counts what a printout holds.

    Args:
        count: How many pages it has.
        header: The header record.

    """
    counted = (
        (count, "page", "pages"),
        (len(header.get("fonts") or []), "font", "fonts"),
        (len(header.get("data") or {}), "data blob", "data blobs"),
        (len(header.get("warnings") or []), "warning", "warnings"),
    )
    return ", ".join(
        f"{value} {one if value == 1 else many}"
        for value, one, many in counted
        if value or one == "page"
    )


def group_lines(header: dict[str, Any]) -> list[str]:
    """Return one padded line per group the document opened.

    Args:
        header: The header record.

    """
    runs = header.get("groupRuns") or {}
    keys = header.get("groupKeys") or {}
    if not runs:
        return []
    width = max(len(name) for name in runs)
    return [
        f"{name:<{width}}  {runs[name]} runs  {keys.get(name, 0)} keys" for name in runs
    ]


def font_lines(fonts: list[Any]) -> list[str]:
    """Return one padded line per font, as the check's table reads.

    Args:
        fonts: The header's `fonts` table.

    """
    if not fonts:
        return []
    names = max(len(str(one.get("name", ""))) for one in fonts)
    sizes = max(len(f"{one.get('size', 0)}pt") for one in fonts)
    lines = []
    for one in fonts:
        parts = [
            f"{one.get('name', ''):<{names}}",
            f"{one.get('size', 0)}pt".ljust(sizes),
        ]
        for word in ("bold", "italic", "underline"):
            if one.get(word):
                parts.append(word)
        if one.get("requested"):
            parts.append(f"{quote(one['requested'])} wanted")
        parts.append(str(one.get("resolvedBy", "")))
        where = one.get("resolvedFile") or f"data {one.get('resolvedData', '')}"
        if one.get("resolvedIndex"):
            where = f"{where} face {one['resolvedIndex']}"
        parts.append(str(where))
        parts.append(quote(str(one.get("resolvedFace", ""))))
        lines.append("  ".join(parts))
    return lines


def data_lines(blobs: dict[str, Any]) -> list[str]:
    """Return one padded line per blob.

    Args:
        blobs: The header's `data` table.

    """
    if not blobs:
        return []
    width = max(len(name) for name in blobs)
    lines = []
    for name, blob in blobs.items():
        content = str(blob.get("content", ""))
        encoding = blob.get("encoding")
        size = len(content) if encoding is None else (len(content) * 3) // 4
        parts = [f"{name:<{width}}"]
        if encoding:
            parts.append(str(encoding))
        parts.append(f"{size} bytes")
        lines.append("  ".join(parts))
    return lines


def warning_lines(warnings: list[Any]) -> list[str]:
    """Return one line per warning the header carries.

    Args:
        warnings: The header's `warnings` array.

    """
    lines = []
    for one in warnings:
        where = [str(one[key]) for key in ("node", "prop") if one.get(key)]
        if one.get("record") is not None:
            where.append(f"record {one['record']}")
        tail = f"  ({', '.join(where)})" if where else ""
        lines.append(f"{one.get('kind', '?')}  {one.get('message', '')}{tail}")
    return lines


def section(name: str, lines: list[str]) -> Iterator[str]:
    """Yield one heading and its lines, or nothing where it has none.

    Args:
        name: The heading.
        lines: What goes under it, already padded.

    """
    if not lines:
        return
    yield name
    for line in lines:
        yield f"  {line}"


def page_dump(page: dict[str, Any], header: dict[str, Any]) -> Iterator[str]:
    """Yield one page's heading and its marks.

    Args:
        page: The page record.
        header: The header, for the geometry a page does not override.

    """
    default = header.get("page") or {}
    width = page.get("width", default.get("width", 0))
    height = page.get("height", default.get("height", 0))
    marks = page.get("marks") or []
    heading = (
        f"page {page.get('number', '?')}  {number(width)} x {number(height)}  "
        f"{len(marks)} mark{'' if len(marks) == 1 else 's'}"
    )
    overrides = [
        f"{word} {number(page[key])}"
        for key, word in (
            ("leftMargin", "left"),
            ("rightMargin", "right"),
            ("topMargin", "top"),
            ("bottomMargin", "bottom"),
        )
        if key in page
    ]
    if overrides:
        heading += "  margins " + " ".join(overrides)
    yield heading
    yield from marks_dump(marks, 1)


def marks_dump(marks: Iterable[Any], depth: int) -> Iterator[str]:
    """Yield the marks of one page, nested marks under their xref.

    Args:
        marks: The marks, in paint order.
        depth: How far to indent them.

    """
    pad = "  " * depth
    for mark in marks:
        yield pad + mark_line(mark)
        for line in mark.get("lines") or []:
            yield pad + "  " + quote(line)
        if mark.get("marks"):
            yield from marks_dump(mark["marks"], depth + 1)


def mark_line(mark: dict[str, Any]) -> str:
    """Return one mark as a line.

    Args:
        mark: The mark record.

    """
    kind = str(mark.get("kind", "?"))
    parts = [kind, box_of(mark)]
    for key in MARK_FIELDS.get(kind, ()):
        if key not in mark:
            continue
        if (kind, key) in WITH_STROKE and "stroke" not in mark:
            continue
        value = mark[key]
        if isinstance(value, bool):
            if value:
                parts.append(key)
            continue
        shown = quote(value) if key in ("value", "target", "title") else spelled(value)
        parts.append(shown if key in BARE else f"{key} {shown}")
    if kind == "barcode" and mark.get("stripes"):
        parts.append(f"{len(mark['stripes'])} stripes")
    return "  ".join(parts)


def box_of(mark: dict[str, Any]) -> str:
    """Return a mark's box, or its point for an outline entry.

    Args:
        mark: The mark record.

    """
    box = mark.get("box")
    if not isinstance(box, dict):
        return f"at {spelled(mark.get('x', 0))},{spelled(mark.get('y', 0))}"
    return (
        f"box {spelled(box.get('x', 0))},{spelled(box.get('y', 0))} "
        f"{spelled(box.get('width', 0))}x{spelled(box.get('height', 0))}"
    )


def spelled(value: Any) -> str:
    """Return one value as the dump writes it.

    Args:
        value: What the record held.

    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return number(value)
    return str(value)


def quote(text: Any) -> str:
    """Return a string in the quotes the dump puts round one.

    Args:
        text: The string.

    """
    return json.dumps(str(text), ensure_ascii=False)
