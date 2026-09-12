"""The value parsers, against what the reference actually placed.

The `values/` probes were built with the reference binary and their
printouts are committed beside them.  This reads both ends of that:
the template through our own KDL adapter, and the recorded answer as JSON.
A dimension the template writes must parse to the coordinate the reference
put in the mark, and a colour to the stroke it drew.

No engine runs here, so this is a unit test rather than a differential
one -- but the numbers it checks against are the oracle's, which is
what makes it worth more than a table of expectations written by hand.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr import kdl

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "tests" / "differential" / "probes" / "values"


def rectangles(name: str) -> tuple[kdl.Document, tuple[kdl.Node, ...]]:
    """Return a probe's document and the rectangles in its detail band."""
    doc = kdl.read(PROBES / f"{name}.kdl")
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    detail = layout.child("detail")
    assert detail is not None
    return doc, detail.each("rectangle")


def marks(name: str) -> list[dict[str, Any]]:
    """Return the marks on the one page of a probe's recorded answer."""
    lines = (PROBES / f"{name}.answer.jsonl").read_text(encoding="utf-8").splitlines()
    page: dict[str, Any] = json.loads(lines[1])
    return list(page["marks"])


def test_the_dimension_probe_is_read_without_complaint() -> None:
    doc, drawn = rectangles("dimensions")
    assert not doc.diagnostics
    assert len(drawn) == len(marks("dimensions"))


def test_every_dimension_parses_to_the_coordinate_the_reference_placed() -> None:
    doc, drawn = rectangles("dimensions")
    placed = marks("dimensions")
    for node, mark in zip(drawn, placed, strict=True):
        assert node.dimension("left") == mark["box"]["x"], node.raw("left")
    assert not doc.diagnostics


def test_every_dimension_parses_to_the_stroke_width_it_was_given() -> None:
    """The same value again, on the side of the mark geometry does not touch.

    A rectangle's `width` is its stroke, so it reaches the printout
    as it was parsed rather than resolved against a container.

    """
    doc, drawn = rectangles("dimensions")
    placed = marks("dimensions")
    for node, mark in zip(drawn, placed, strict=True):
        assert node.dimension("width") == mark["width"], node.raw("width")
    assert not doc.diagnostics


def test_every_colour_parses_to_the_stroke_the_reference_drew() -> None:
    doc, drawn = rectangles("colors")
    placed = marks("colors")
    assert len(drawn) == len(placed)
    for node, mark in zip(drawn, placed, strict=True):
        style = node.child("style")
        assert style is not None
        assert style.color("color") == mark["stroke"], style.raw("color")
    assert not doc.diagnostics


def test_the_probe_the_reference_reads_and_this_engine_does_not() -> None:
    """`values/hex-float`, the registered divergence.

    The reference builds it, so it has a recorded answer; here the
    two values in it are not numbers, and the refusal names the property.
    The entry in `divergences.toml` retires when the Go side tightens
    its parser, and this test comes out with it.

    """
    doc, drawn = rectangles("hex-float")
    for node in drawn:
        assert node.dimension("left") is None
        assert node.dimension("width") is None
    assert [one.message for one in doc.diagnostics] == [
        'bad dimension "0x1p-2"',
        'bad dimension "0x1.8p1"',
    ]
    assert (PROBES / "hex-float.answer.jsonl").is_file()


@pytest.mark.parametrize("name", ["dimensions", "colors", "hex-float"])
def test_each_probe_declares_the_font_it_measures_with(name: str) -> None:
    """Every probe resolves its fonts by path, since builds are strict."""
    doc = kdl.read(PROBES / f"{name}.kdl")
    report = doc.only_root("report")
    assert report is not None
    font = report.child("font")
    assert font is not None
    named = font.string("file")
    assert named is not None
    assert (PROBES / named).resolve().is_file()
