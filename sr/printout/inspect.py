"""Reading a printout back, and dumping it as text.

doc/cli.md#sr-inspect: one mark per line, in paint order, with a text
mark's lines quoted under it.  The printout is machine-generated;
this is the same content in a form that can be read and diffed.

Either encoding is read, and the extension says which, as for
doc/cli.md#sr-render: ``.cbor`` is CBOR and anything else NDJSON.
Both give the same records, so everything after reading is one path.

The reader is deliberately **untyped**.  It parses the records into
plain dictionaries rather than into :mod:`sr.printout.model`, so a
printout another engine wrote still dumps, and so does one holding
something this engine would not have built.  That is also what
:mod:`sr.printout.verify` checks the
[invariants](doc/printout.md#invariants) on, which doc/cli.md asks
this command to do after the dump.  The typed reader is
:mod:`sr.printout.read`, which the renderer draws from.

So the dump reads every field as the check does, defensively:
an object where a number belongs is shown as it is, and an
array where an object belongs is passed over.  What is wrong
with the printout is the check's to say, after the dump.

"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from sr.errors import BuildError, Location
from sr.printout.verify import listed, mapped
from sr.printout.write import number

__all__ = [
    "cbor_item",
    "cbor_records",
    "dump",
    "pages_wanted",
    "read_cbor",
    "read_jsonl",
    "read_printout",
    "records_of",
]

# How deep the items of a CBOR printout may nest.  A page record,
# its marks, an xref, its marks, a mark, and its box come to six;
# this is far more than that, and far less than the interpreter's
# recursion runs out at, so a file nested past it is refused
# rather than read into a recursion that would fail.
DEPTH = 64

# The simple values a printout holds, by their additional information.
SIMPLE = {20: False, 21: True, 22: None}

# The floats, by their additional information: half, single, and double.
FLOATS = {25: ">e", 26: ">f", 27: ">d"}

# The fields each mark kind shows, in the order doc/cli.md#sr-inspect
# writes them.  A field the mark does not carry is left out rather than
# written empty, so a rectangle with no `stroke` shows no `width`.
MARK_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("font", "color", "align", "leading", "lastLineJustified"),
    "line": ("width", "dash", "color", "backslant"),
    "rectangle": ("stroke", "width", "dash", "fill", "radius"),
    "image": ("data", "file"),
    "barcode": ("type", "value", "module", "vertical", "ink", "paper"),
    "outline": ("title", "level", "name", "closed"),
    "xref": ("type", "target", "caption"),
}

# The fields that are shown as a bare value rather than as `name value`,
# because the name would repeat what the value already says.
BARE = {"type", "value", "title", "target"}

# The fields that are text a template wrote or computed, and are quoted
# so that a space or an empty string inside one cannot be misread.
QUOTED = {"value", "target", "title", "caption", "name"}

# A true boolean is shown as its name, and these read better spelled out.
LABELS = {"lastLineJustified": "last line justified"}

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


def read_cbor(path: Path) -> list[Any]:
    """Return the records a CBOR printout holds.

    Args:
        path: The file to read.

    Raises:
        BuildError: The file is not a CBOR printout, or holds no header.

    """
    try:
        records = cbor_records(path.read_bytes())
    except ValueError as refused:
        raise BuildError(
            f"not a printout: {refused}", Location(file=str(path))
        ) from None
    if not records or not isinstance(records[0], dict):
        raise BuildError("not a printout: no header record", Location(file=str(path)))
    return records


def cbor_records(data: bytes) -> list[Any]:
    """Return the data items of a CBOR sequence, one per record.

    doc/printout.md#cbor writes a printout in a small part of CBOR:
    definite-length maps keyed by text, arrays, text strings,
    integers, floats, and the two booleans, one item after another
    with nothing between them.  That part is read here, and the rest
    is refused at the byte it starts at.  A general decoder reads
    a tag as whatever type it stands for, and a byte string as bytes,
    and the dump would show either as if a printout could hold it.

    Args:
        data: The bytes.

    Raises:
        ValueError: They are not such a sequence.

    """
    records = []
    at = 0
    while at < len(data):
        value, at = cbor_item(data, at, 0)
        records.append(value)
    return records


def cbor_item(data: bytes, at: int, depth: int) -> tuple[Any, int]:
    """Return one data item, and the offset of the byte after it.

    Args:
        data: The bytes.
        at: Where the item begins.
        depth: How many arrays and maps it is inside.

    Raises:
        ValueError: The item is not one a printout holds.

    """
    if depth > DEPTH:
        raise ValueError(f"at byte {at}: nested deeper than any printout")
    initial = cbor_slice(data, at, 1)[0]
    major, info = initial >> 5, initial & 0x1F
    if major == 7:
        if info in SIMPLE:
            return SIMPLE[info], at + 1
        if info not in FLOATS:
            raise ValueError(f"at byte {at}: a simple value no printout holds")
        size = 1 << (info - 24)
        (value,) = struct.unpack(FLOATS[info], cbor_slice(data, at + 1, size))
        return value, at + 1 + size
    if major == 2:
        raise ValueError(f"at byte {at}: a byte string, where text belongs")
    if major == 6:
        raise ValueError(f"at byte {at}: a tag, which no printout carries")
    argument, after = cbor_argument(data, at, info)
    if major == 0:
        return argument, after
    if major == 1:
        return -1 - argument, after
    if major == 3:
        raw = cbor_slice(data, after, argument)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            said = f"at byte {at}: a text string not in UTF-8"
            raise ValueError(said) from None
        return text, after + argument
    if major == 4:
        items = []
        for _ in range(argument):
            value, after = cbor_item(data, after, depth + 1)
            items.append(value)
        return items, after
    found: dict[str, Any] = {}
    for _ in range(argument):
        key, inside = cbor_item(data, after, depth + 1)
        if not isinstance(key, str):
            raise ValueError(f"at byte {after}: a map key that is not text")
        value, after = cbor_item(data, inside, depth + 1)
        found[key] = value
    return found, after


def cbor_argument(data: bytes, at: int, info: int) -> tuple[int, int]:
    """Return an item's argument, and the offset of the byte after it.

    Args:
        data: The bytes.
        at: Where the item begins.
        info: The additional information of its initial byte.

    Raises:
        ValueError: The length is indefinite, or the bytes run out.

    """
    if info < 24:
        return info, at + 1
    if info == 31:
        raise ValueError(f"at byte {at}: an indefinite length")
    if info > 27:
        raise ValueError(f"at byte {at}: a reserved additional information")
    size = 1 << (info - 24)
    return int.from_bytes(cbor_slice(data, at + 1, size), "big"), at + 1 + size


def cbor_slice(data: bytes, at: int, size: int) -> bytes:
    """Return ``size`` bytes from ``at``, which have to be there.

    Args:
        data: The bytes.
        at: Where the slice begins.
        size: How long it is.

    Raises:
        ValueError: The bytes end first.

    """
    if at + size > len(data):
        raise ValueError(f"at byte {len(data)}: the bytes end inside an item")
    return data[at : at + size]


def read_printout(path: Path) -> list[Any]:
    """Return the records a printout holds, in whichever encoding it is.

    Args:
        path: The file to read; ``.cbor`` is CBOR, anything else NDJSON.

    Raises:
        BuildError: The file is not a printout in that encoding.

    """
    if path.suffix.lower() == ".cbor":
        return read_cbor(path)
    return read_jsonl(path)


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
        number_of = page.get("number")
        if pages is not None and not (
            isinstance(number_of, int) and number_of in pages
        ):
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
    report = mapped(header.get("report"))
    yield "  " + headline(report)
    if report.get("description"):
        yield f"  {report['description']}"
    page = mapped(header.get("page"))
    if page:
        yield (
            f"  page {spelled(page.get('width', 0))} x "
            f"{spelled(page.get('height', 0))} pt, margins "
            f"left {spelled(page.get('leftMargin', 0))} "
            f"right {spelled(page.get('rightMargin', 0))} "
            f"top {spelled(page.get('topMargin', 0))} "
            f"bottom {spelled(page.get('bottomMargin', 0))}"
        )
    yield "  " + tally(count, header)
    yield from section("groups", group_lines(header))
    yield from section("fonts", font_lines(listed(header.get("fonts"))))
    yield from section("data", data_lines(mapped(header.get("data"))))
    warnings = warning_lines(listed(header.get("warnings")))
    yield from section("warnings", warnings)


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
        (len(listed(header.get("fonts"))), "font", "fonts"),
        (len(mapped(header.get("data"))), "data blob", "data blobs"),
        (len(listed(header.get("warnings"))), "warning", "warnings"),
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
    runs = mapped(header.get("groupRuns"))
    keys = mapped(header.get("groupKeys"))
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
    entries = [one for one in fonts if isinstance(one, dict)]
    if not entries:
        return []
    names = max(len(str(one.get("name", ""))) for one in entries)
    sizes = max(len(f"{one.get('size', 0)}pt") for one in entries)
    lines = []
    for one in entries:
        parts = [
            f"{one.get('name', '')!s:<{names}}",
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
        entry = mapped(blob)
        content = str(entry.get("content", ""))
        encoding = entry.get("encoding")
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
        if not isinstance(one, dict):
            continue
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
    default = mapped(header.get("page"))
    width = page.get("width", default.get("width", 0))
    height = page.get("height", default.get("height", 0))
    marks = listed(page.get("marks"))
    heading = (
        f"page {page.get('number', '?')}  "
        f"{spelled(width)} x {spelled(height)}  "
        f"{len(marks)} mark{'' if len(marks) == 1 else 's'}"
    )
    overrides = [
        f"{word} {spelled(page[key])}"
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
        if not isinstance(mark, dict):
            continue
        yield pad + mark_line(mark)
        for line in listed(mark.get("lines")):
            yield pad + "  " + quote(line)
        yield from marks_dump(listed(mark.get("marks")), depth + 1)


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
                parts.append(LABELS.get(key, key))
            continue
        shown = quote(value) if key in QUOTED else spelled(value)
        parts.append(shown if key in BARE else f"{key} {shown}")
    if kind == "barcode" and listed(mark.get("stripes")):
        parts.append(f"{len(mark['stripes'])} stripes")
    if kind == "barcode" and listed(mark.get("rows")):
        parts.append(f"{len(mark['rows'])} rows")
    crop = mark.get("crop")
    if kind == "image" and isinstance(crop, dict):
        parts.append(box_of({"box": crop}).replace("box", "crop", 1))
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
