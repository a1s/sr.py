r"""Serializing a printout, byte for byte.

doc/printout.md#encoding is the contract, and it is a byte-level one:
two engines are compared on the bytes, so everything a JSON writer
is usually free to choose is fixed here instead.

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
is written [relative to the printout](doc/printout.md#paths), and
the printout's location is known here and nowhere earlier, so
:func:`relative_to` runs at this point rather than at resolution.

"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TextIO

from sr.errors import BuildWarning
from sr.printout.model import (
    VERSION,
    Box,
    FontEntry,
    Line,
    Mark,
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
    "number",
    "page_object",
    "quoted",
    "relative_to",
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
        "width": paper.width,
        "height": paper.height,
        "leftMargin": paper.left,
        "rightMargin": paper.right,
        "topMargin": paper.top,
        "bottomMargin": paper.bottom,
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
        "size": entry.size,
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
        found["resolvedIndex"] = entry.index
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
        found["record"] = warning.record
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
    return {"x": box.x, "y": box.y, "width": box.width, "height": box.height}


def mark_object(mark: Mark) -> dict[str, Any]:
    """Return one mark.

    Args:
        mark: The mark to write.

    Raises:
        TypeError: The mark is of a kind this writer does not know.

    """
    found: dict[str, Any] = {"kind": mark.kind, "box": box_object(mark.box)}
    if isinstance(mark, Text):
        found["font"] = mark.font
        found["color"] = mark.color
        found["align"] = mark.align
        found["leading"] = mark.leading
        found["lines"] = list(mark.lines)
        if mark.last_line_justified:
            found["lastLineJustified"] = True
        return found
    if isinstance(mark, Line):
        found["width"] = mark.width
        found["dash"] = mark.dash
        found["color"] = mark.color
        found["backslant"] = mark.backslant
        return found
    if isinstance(mark, Rectangle):
        found["width"] = mark.width
        found["dash"] = mark.dash
        if mark.stroke is not None:
            found["stroke"] = mark.stroke
        if mark.fill is not None:
            found["fill"] = mark.fill
        if mark.radius:
            found["radius"] = mark.radius
        return found
    if isinstance(mark, Xref):
        found["type"] = mark.link
        found["target"] = mark.target
        if mark.caption is not None:
            found["caption"] = mark.caption
        found["marks"] = [mark_object(one) for one in mark.marks]
        return found
    raise TypeError(f"cannot write a {type(mark).__name__} mark")


def page_object(page: Page, default: Paper) -> dict[str, Any]:
    """Return one page line's object.

    The size and the four margins are written only where they differ
    from the document's, and independently so: a page flush to the
    paper edge under a header that insets is an override, so a zero
    is written rather than omitted.

    Args:
        page: The page.
        default: The document's own geometry, to compare against.

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
                found[key] = value
    found["marks"] = [mark_object(mark) for mark in page.marks]
    return found


def lines_of(printout: Printout, base: Path | None) -> Iterator[str]:
    """Yield the printout's records, header first.

    Args:
        printout: The document.
        base: The directory it is being written to.

    """
    yield dumps(header_object(printout, base))
    for page in printout.pages:
        yield dumps(page_object(page, printout.paper))


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
