r"""Serializing a printout, byte for byte.

doc/printout.md#encoding is the contract, and it is a byte-level one:
two engines are compared on the bytes, so everything a JSON writer
is usually free to choose is fixed here instead.

Both encodings are written from one tree of objects, built by the
``*_object`` helpers below, so the two cannot disagree about what
a printout holds.  The tree is typed as the format is: a length is
a ``float`` and a count an ``int``, whatever the model holds them as.
NDJSON spells the two alike, and CBOR does not, which is the reason:
doc/printout.md#cbor writes a length as a double and a count as an
integer.

* **Key order is the order doc/printout.md lists the fields.**
  Each ``ordered`` helper below writes one object, and the order
  it writes its keys in is the order of that section's table.
* **Numbers are written by :func:`number`**, which is the three rules
  of doc/printout.md#units-and-number-format: no fractional part on an
  integral value, no ``-0``, and no exponent at either end of the range.
  Python's own float formatting breaks all three.
* **Strings are escaped by :func:`quoted`**, per
  doc/printout.md#strings: the six short escapes, ``\\uXXXX`` in lower
  case for the rest of C0, and ``<``, ``>``, ``&``, U+2028 and U+2029
  escaped although JSON does not require it.
* **Lines end with U+000A**, including the last, on every platform.

Serializing is not purely a re-encoding.  A path the template named
(a `font file=`, or an `image file=` with `embed=#false`) is written
[relative to the printout](doc/printout.md#paths), and the printout's
location is known here and nowhere earlier, so :func:`relative_to`
runs at this point rather than at resolution.

"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO, TextIO

import cbor2

from sr.errors import BuildWarning
from sr.printout.model import (
    VERSION,
    Barcode,
    Box,
    Crop,
    FontEntry,
    Image,
    Line,
    Mark,
    Outline,
    Page,
    Paper,
    Printout,
    Rectangle,
    Text,
    Xref,
)

__all__ = [
    "dumps",
    "header_object",
    "length",
    "number",
    "page_object",
    "quoted",
    "records",
    "relative_to",
    "write_cbor",
    "write_jsonl",
]

# Three decimals is as far as a length goes, so this is exact
# for every value that reaches here: they have all been through
# `sr.units.round_points` already.
PLACES = 3

# Escaped although JSON does not ask for it.  The first three keep
# a printout safe to paste into an HTML document, and the last two
# are legal in JSON and not in JavaScript, which reads the same text.
ESCAPED = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}

# A code point UTF-8 has no encoding for.  Only a path brings one,
# from a file name that was not valid Unicode.
LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def number(value: float | int) -> str:
    """Return a length as the printout spells it.

    Args:
        value: The number, already rounded to three decimals.

    """
    if isinstance(value, int):
        return str(value)
    if value == 0:
        # Also catches a negative zero, which rounding -0.0004 produces
        # and which would make two printouts differ over a value that
        # compares equal.
        return "0"
    text = f"{value:.{PLACES}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def length(value: float) -> float:
    """Return a length as the tree holds it: a float, and never ``-0.0``.

    The model holds a length however it was computed, which is now
    and then an ``int``.  doc/printout.md#cbor writes every length
    as a double, so the tree turns it into one here; NDJSON spells
    the two the same either way.  Adding a zero turns a negative zero
    positive and leaves everything else as it is, since doc/printout.md
    writes ``-0`` as ``0`` in both encodings.

    Args:
        value: The length, already rounded to three decimals.

    """
    return float(value) + 0.0


def quoted(text: str) -> str:
    """Return a string as a JSON string literal.

    Args:
        text: The string to write.

    """
    written = json.dumps(text, ensure_ascii=False)
    for character, escape in ESCAPED.items():
        written = written.replace(character, escape)
    return written


def dumps(value: Any) -> str:
    """Return one JSON value, written compactly and in key order.

    Objects keep the order they were built in, arrays their own,
    and nothing is indented: a printout is one object per line.

    Args:
        value: A string, number, boolean, ``None``, mapping or sequence.

    Raises:
        TypeError: The value is not one of those.

    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return number(value)
    if isinstance(value, str):
        return quoted(value)
    if isinstance(value, Mapping):
        inside = ",".join(f"{quoted(key)}:{dumps(item)}" for key, item in value.items())
        return "{" + inside + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to a printout")


def relative_to(path: Path, base: Path | None) -> str:
    """Return a path as the printout writes it.

    Separators are ``/`` on every platform, so a printout written
    on Windows renders on Linux.  A path with no relative form
    at all, such as one on another Windows drive, is written
    absolute; there is nothing else to say about it.

    Args:
        path: The file, as the engine resolved it.
        base: The directory the printout is being written to,
            or ``None`` for a destination that has none.

    """
    if base is None:
        base = Path.cwd()
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.as_posix()


def paper_object(paper: Paper) -> dict[str, Any]:
    """Return the header's `page` object.

    Args:
        paper: The default page geometry.

    """
    return {
        "width": length(paper.width),
        "height": length(paper.height),
        "leftMargin": length(paper.left),
        "rightMargin": length(paper.right),
        "topMargin": length(paper.top),
        "bottomMargin": length(paper.bottom),
    }


def report_object(printout: Printout) -> dict[str, Any]:
    """Return the header's `report` object.

    Omitted fields are absent rather than null, which is why
    this is built by appending rather than by writing four keys.

    Args:
        printout: The document.

    """
    report = printout.report
    found: dict[str, Any] = {}
    for key, value in (
        ("name", report.name),
        ("description", report.description),
        ("version", report.version),
        ("author", report.author),
    ):
        if value is not None:
            found[key] = value
    return found


def font_object(entry: FontEntry, base: Path | None) -> dict[str, Any]:
    """Return one entry of the header's `fonts` table.

    ``requested`` is present only where a `typeface` went through
    the resolution chain, and ``resolvedIndex`` only where the face
    sits inside a collection: a file holding one face has nothing to
    disambiguate, and a renderer that saw `0` everywhere would learn
    nothing from it.

    Args:
        entry: The resolved font.
        base: The directory the printout is being written to.

    """
    found: dict[str, Any] = {
        "name": entry.name,
        "size": int(entry.size),
        "bold": entry.bold,
        "italic": entry.italic,
        "underline": entry.underline,
    }
    if entry.requested is not None:
        found["requested"] = entry.requested
    if entry.data is not None:
        found["resolvedData"] = entry.data
    elif entry.file is not None:
        # doc/printout.md#paths: a font the template named is a project
        # asset and travels with the printout; one found on the host is
        # a system resource, and `resolvedFile` doubles as the record
        # of which file was measured, so it stays as it was opened.
        found["resolvedFile"] = (
            relative_to(entry.file, base)
            if entry.step == "explicit"
            else entry.file.as_posix()
        )
    if entry.index:
        found["resolvedIndex"] = int(entry.index)
    found["resolvedFace"] = entry.face
    found["resolvedBy"] = entry.step
    return found


def warning_object(warning: BuildWarning) -> dict[str, Any]:
    """Return one entry of the header's `warnings` array.

    Args:
        warning: What the build had to say.

    """
    found: dict[str, Any] = {"kind": warning.kind}
    if warning.node is not None:
        found["node"] = warning.node
    if warning.prop is not None:
        found["prop"] = warning.prop
    if warning.record is not None:
        found["record"] = int(warning.record)
    found["message"] = warning.message
    return found


def data_object(blobs: Mapping[str, bytes | str]) -> dict[str, Any]:
    """Return the header's `data` table.

    Keys are sorted by name, as `fonts` is and for the same reason:
    the table is a lookup and its order carries nothing, so an order
    that has to be chosen may as well be the one a reader can predict.

    Args:
        blobs: The blobs something in the document refers to.

    """
    found: dict[str, Any] = {}
    for name in sorted(blobs):
        content = blobs[name]
        if isinstance(content, bytes):
            found[name] = {
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            }
        else:
            found[name] = {"content": content}
    return found


def header_object(printout: Printout, base: Path | None) -> dict[str, Any]:
    """Return the header line's object.

    Args:
        printout: The document.
        base: The directory it is being written to.

    """
    found: dict[str, Any] = {
        "sr": VERSION,
        "kind": "header",
        "report": report_object(printout),
        "built": printout.built,
        "engine": printout.engine,
        "strictFonts": printout.strict_fonts,
        "pages": len(printout.pages),
    }
    if printout.group_runs:
        found["groupRuns"] = dict(printout.group_runs)
        found["groupKeys"] = dict(printout.group_keys)
    found["page"] = paper_object(printout.paper)
    found["fonts"] = [
        font_object(entry, base)
        for entry in sorted(printout.fonts, key=lambda one: one.name)
    ]
    found["data"] = data_object(printout.data)
    if printout.warnings:
        found["warnings"] = [warning_object(one) for one in printout.warnings]
    return found


def box_object(box: Box) -> dict[str, Any]:
    """Return a mark's `box`.

    Args:
        box: The rectangle.

    """
    return {
        "x": length(box.x),
        "y": length(box.y),
        "width": length(box.width),
        "height": length(box.height),
    }


def crop_object(crop: Crop) -> dict[str, Any]:
    """Return an image mark's `crop`.

    Args:
        crop: The retained region, in source pixels.

    """
    return {
        "x": length(crop.x),
        "y": length(crop.y),
        "width": length(crop.width),
        "height": length(crop.height),
    }


def mark_object(mark: Mark, base: Path | None = None) -> dict[str, Any]:
    """Return one mark.

    Args:
        mark: The mark to write.
        base: The directory the printout is being written to,
            which an image's `file` is written relative to.

    Raises:
        TypeError: The mark is of a kind this writer does not know.

    """
    if isinstance(mark, Outline):
        return outline_object(mark)
    found: dict[str, Any] = {"kind": mark.kind, "box": box_object(mark.box)}
    if isinstance(mark, Text):
        found["font"] = mark.font
        found["color"] = mark.color
        found["align"] = mark.align
        found["leading"] = length(mark.leading)
        found["lines"] = list(mark.lines)
        if mark.last_line_justified:
            found["lastLineJustified"] = True
        return found
    if isinstance(mark, Line):
        found["width"] = length(mark.width)
        found["dash"] = mark.dash
        found["color"] = mark.color
        found["backslant"] = mark.backslant
        return found
    if isinstance(mark, Rectangle):
        found["width"] = length(mark.width)
        found["dash"] = mark.dash
        if mark.stroke is not None:
            found["stroke"] = mark.stroke
        if mark.fill is not None:
            found["fill"] = mark.fill
        if mark.radius:
            found["radius"] = length(mark.radius)
        return found
    if isinstance(mark, Image):
        if mark.data is not None:
            found["data"] = mark.data
        elif mark.file is not None:
            # doc/printout.md#paths: the template named the file,
            # so it is a project asset and travels with the printout.
            found["file"] = relative_to(mark.file, base)
        if mark.crop is not None:
            found["crop"] = crop_object(mark.crop)
        return found
    if isinstance(mark, Barcode):
        found["type"] = mark.symbology
        found["value"] = mark.value
        found["module"] = length(mark.module)
        found["vertical"] = mark.vertical
        found["ink"] = mark.ink
        if mark.paper is not None:
            found["paper"] = mark.paper
        if mark.rows:
            found["rows"] = [[int(run) for run in row] for row in mark.rows]
        else:
            found["stripes"] = [int(run) for run in mark.stripes]
        return found
    if isinstance(mark, Xref):
        found["type"] = mark.link
        found["target"] = mark.target
        if mark.caption is not None:
            found["caption"] = mark.caption
        found["marks"] = [mark_object(one, base) for one in mark.marks]
        return found
    raise TypeError(f"cannot write a {type(mark).__name__} mark")


def outline_object(mark: Outline) -> dict[str, Any]:
    """Return an outline entry, which has a point rather than a box.

    ``name`` is present only where the build kept it,
    which is where some `xref` targets it: doc/printout.md#outline.

    Args:
        mark: The entry.

    """
    found: dict[str, Any] = {
        "kind": mark.kind,
        "title": mark.title,
        "level": int(mark.level),
    }
    if mark.name is not None:
        found["name"] = mark.name
    found["closed"] = mark.closed
    found["x"] = length(mark.box.x)
    found["y"] = length(mark.box.y)
    return found


def page_object(page: Page, default: Paper, base: Path | None = None) -> dict[str, Any]:
    """Return one page line's object.

    The size and the four margins are written only where they differ
    from the document's, and independently so: a page flush to the
    paper edge under a header that insets is an override, so a zero
    is written rather than omitted.

    Args:
        page: The page.
        default: The document's own geometry, to compare against.
        base: The directory the printout is being written to.

    """
    found: dict[str, Any] = {"kind": "page", "number": page.number}
    paper = page.paper
    if paper is not None:
        for key, value, standard in (
            ("width", paper.width, default.width),
            ("height", paper.height, default.height),
            ("leftMargin", paper.left, default.left),
            ("rightMargin", paper.right, default.right),
            ("topMargin", paper.top, default.top),
            ("bottomMargin", paper.bottom, default.bottom),
        ):
            if value != standard:
                found[key] = length(value)
    found["marks"] = [mark_object(mark, base) for mark in page.marks]
    return found


def records(printout: Printout, base: Path | None) -> Iterator[dict[str, Any]]:
    """Yield the printout's records as objects, header first.

    This is the tree both encodings write, and the one
    the [invariants](doc/printout.md#invariants) are checked on.

    Args:
        printout: The document.
        base: The directory it is being written to.

    """
    yield header_object(printout, base)
    for page in printout.pages:
        yield page_object(page, printout.paper, base)


def lines_of(printout: Printout, base: Path | None) -> Iterator[str]:
    """Yield the printout's records as NDJSON lines, header first.

    Args:
        printout: The document.
        base: The directory it is being written to.

    """
    for record in records(printout, base):
        yield dumps(record)


def write_jsonl(printout: Printout, out: TextIO, base: Path | None = None) -> None:
    r"""Write a printout as NDJSON.

    The stream is written with ``\\n`` and nothing else, so it has
    to be opened with ``newline=""`` on a platform whose text mode
    would translate that.

    Args:
        printout: The document.
        out: Where to write it.
        base: The directory the printout is landing in, which its paths
            are written relative to.  The working directory stands in
            where there is none, as for a pipe.

    """
    for line in lines_of(printout, base):
        out.write(line)
        out.write("\n")


def as_cbor_text(value: Any) -> Any:
    """Return a record with every string encodable as a CBOR text string.

    A CBOR text string is UTF-8 and has no escapes, so the one thing
    JSON writes as an escape, a lone surrogate, has no spelling here.
    doc/printout.md#cbor writes each as U+FFFD instead, which is what
    the reference writes in either encoding.  The path it came from
    no longer finds its file; NDJSON is the encoding that keeps it.

    Args:
        value: A record, or anything inside one.

    """
    if isinstance(value, str):
        return LONE_SURROGATE.sub("\ufffd", value)
    if isinstance(value, dict):
        return {as_cbor_text(key): as_cbor_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [as_cbor_text(item) for item in value]
    return value


def write_cbor(printout: Printout, out: BinaryIO, base: Path | None = None) -> None:
    """Write a printout as a CBOR sequence.

    doc/printout.md#cbor: the same records as NDJSON, in the same order,
    each a definite-length map with its keys in the order NDJSON writes
    them.  A count is an integer in its shortest form, a length is always
    a double, and a string is a text string.  Nothing frames the records
    and nothing tags them, so the stream is RFC 8742's sequence and
    nothing more.  ``cbor2`` does all of that by default for the types
    the tree holds, which is why the tree is typed.

    Args:
        printout: The document.
        out: Where to write it, opened for bytes.
        base: The directory the printout is landing in,
            which its paths are written relative to, as for NDJSON.

    """
    for record in records(printout, base):
        out.write(cbor2.dumps(as_cbor_text(record)))
