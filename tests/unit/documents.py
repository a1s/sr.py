"""Printouts made by hand, for the renderer's tests.

A template would be a long way round to a printout that
holds one text mark in two fonts, so the renderer's tests
build the model directly and render it.  Every file they render
goes through :func:`tests.pdfcheck.check` on the way back.

"""

from __future__ import annotations

from pathlib import Path

from sr.pdf import render
from sr.printout.model import (
    Box,
    FontEntry,
    Mark,
    Page,
    Paper,
    Printout,
    Report,
    Text,
)
from tests.pdfcheck import Parsed, check

__all__ = [
    "BOLD",
    "PAPER",
    "REGULAR",
    "document",
    "entry",
    "rendered",
    "text",
]

ROOT = Path(__file__).resolve().parents[2]
REGULAR = ROOT / "example" / "fonts" / "Go-Regular.ttf"
BOLD = ROOT / "example" / "fonts" / "Go-Bold.ttf"

# A4, with no margins, so that no mark is outside the printable area.
PAPER = Paper(595.276, 841.89, 0, 0, 0, 0)


def entry(
    name: str,
    file: Path | None = REGULAR,
    size: int = 10,
    *,
    data: str | None = None,
    index: int = 0,
    underline: bool = False,
) -> FontEntry:
    """Return a font entry, read from the Go face unless told otherwise.

    Args:
        name: Its name.
        file: The file its face is in.
        size: Its size.
        data: The data entry its face is in, instead of a file.
        index: The face's position in a collection.
        underline: Whether it draws an underline.

    """
    return FontEntry(
        name=name,
        size=size,
        bold=False,
        italic=False,
        underline=underline,
        face="Go",
        step="explicit",
        file=None if data is not None else file,
        data=data,
        index=index,
    )


def text(line: str, font: str = "body", y: float = 0) -> Text:
    """Return a one-line text mark.

    Args:
        line: Its line.
        font: Its font's name.
        y: How far down the page it is.

    """
    return Text(
        Box(0, y, 300, 12),
        font=font,
        color="#000000",
        align="left",
        leading=12,
        lines=(line,),
    )


def document(
    *marks: Mark,
    fonts: tuple[FontEntry, ...] = (entry("body"),),
    data: dict[str, bytes | str] | None = None,
    pages: tuple[Page, ...] | None = None,
    report: Report | None = None,
    built: str = "2026-08-04T09:12:44Z",
) -> Printout:
    """Return a printout of one page holding these marks, or of these pages.

    Args:
        *marks: The one page's marks, where ``pages`` is not given.
        fonts: The header's fonts.
        data: The header's data table.
        pages: The pages, in place of a page of ``marks``.
        report: What the template said about itself.
        built: The build time.

    """
    return Printout(
        report=report or Report(name="Test"),
        built=built,
        engine="sr 0.1.0",
        strict_fonts=True,
        paper=PAPER,
        fonts=fonts,
        data=data or {},
        pages=pages if pages is not None else (Page(1, marks),),
    )


def rendered(
    printout: Printout,
    compress: bool = True,
) -> tuple[bytes, Parsed]:
    """Return a printout rendered, and its objects, once it passed the check.

    Args:
        printout: The document.
        compress: Whether streams are compressed.

    """
    data = render(printout, compress)
    return data, check(data)
