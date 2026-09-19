"""Line breaking, held to the printouts M1 recorded from the reference.

The `breaking/` probes are one template per question about
doc/layout.md#line-breaking, and each has the reference's own printout
committed beside it.  Those `lines` arrays are the answer the
specification was written from, so wrapping the same strings in the same
boxes and comparing is the closest this engine gets to the differential
harness before it can build a document at all.

Every probe here is a single `detail` band of `field` nodes with a literal
``width``, one `font` named by path, and marks emitted in document order.
That is what lets the test read the template with the real loader and
line the fields up against the marks one to one, rather than reimplementing
a band builder to check a line breaker.

"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sr.expr import apply_format, evaluate
from sr.fonts.face import open_face
from sr.fonts.text import Metrics, quote_char, wrap
from sr.template import load
from sr.template.model import Field, Report

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "tests" / "differential" / "probes" / "breaking"

# The probes whose fields are all literal ``text``, plus the one
# that also reaches the same break through an expression and a format.
NAMES = sorted(one.stem for one in PROBES.glob("*.kdl"))


def answer_marks(name: str) -> list[dict[str, object]]:
    """Return the text marks of a probe's committed printout, in order.

    Args:
        name: The probe's name, without its suffix.

    """
    marks: list[dict[str, object]] = []
    with (PROBES / f"{name}.answer.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("kind") == "page":
                marks.extend(
                    one for one in record["marks"] if one.get("kind") == "text"
                )
    return marks


def answer_warnings(name: str) -> list[dict[str, object]]:
    """Return the ``glyph`` warnings of a probe's committed printout.

    Args:
        name: The probe's name, without its suffix.

    """
    with (PROBES / f"{name}.answer.jsonl").open(encoding="utf-8") as stream:
        header = json.loads(stream.readline())
    return [one for one in header.get("warnings", []) if one["kind"] == "glyph"]


def probe(name: str) -> tuple[Report, Metrics, list[Field]]:
    """Return a probe's report, its one font's metrics, and its fields.

    Args:
        name: The probe's name, without its suffix.

    """
    loaded = load(PROBES / f"{name}.kdl")
    report = loaded.require()
    font = report.fonts[0]
    face = open_face(report.basedir / str(font.file))
    layout = report.layout
    assert layout is not None
    detail = layout.detail
    assert detail is not None
    fields = [one for one in detail.elements if isinstance(one, Field)]
    return report, Metrics(face, font.size), fields


def content(field: Field) -> str:
    """Return the string a probe's field finally holds.

    ``text`` is literal; ``expr`` is evaluated against nothing, since
    no probe here reads a record, and ``format`` is applied afterwards.
    Wrapping happens after that, so where the string came from makes
    no difference to how it is treated.

    Args:
        field: The node.

    """
    if field.text is not None:
        return field.text
    assert field.expr is not None
    value = evaluate(field.expr.source)
    if field.format:
        return apply_format(field.format, value)
    return str(value)


@pytest.mark.parametrize("name", NAMES)
def test_every_field_wraps_as_the_reference_wrapped_it(name: str) -> None:
    _, metrics, fields = probe(name)
    marks = answer_marks(name)
    count = f"{name}: {len(fields)} fields against {len(marks)} marks"
    assert len(fields) == len(marks), count
    for index, (field, mark) in enumerate(zip(fields, marks, strict=True)):
        box = mark["box"]
        assert isinstance(box, dict)
        width = float(box["width"])
        assert field.box.across.size == pytest.approx(width), f"{name} field {index}"
        wrapped = wrap(content(field), width, metrics)
        assert wrapped.lines == mark["lines"], f"{name} field {index}"


def named(message: str) -> str:
    """Return the character a `glyph` warning is about, as it quotes it.

    Args:
        message: The warning's message, from the committed printout.

    """
    return message.split("no glyph for ", 1)[1].split(",", 1)[0]


@pytest.mark.parametrize("name", NAMES)
def test_the_missing_characters_are_the_ones_the_reference_warned_about(
    name: str,
) -> None:
    _, metrics, fields = probe(name)
    found: dict[str, None] = {}
    for field in fields:
        for character in wrap(content(field), 0, metrics).missing:
            found[character] = None
    warned = {named(str(one["message"])) for one in answer_warnings(name)}
    assert {quote_char(one) for one in found} == warned


@pytest.mark.parametrize("name", NAMES)
def test_the_leading_is_the_one_in_the_printout(name: str) -> None:
    _, metrics, _fields = probe(name)
    for mark in answer_marks(name):
        assert metrics.leading == mark["leading"]


@pytest.mark.parametrize("name", NAMES)
def test_a_stretch_field_is_as_tall_as_its_lines(name: str) -> None:
    _, metrics, fields = probe(name)
    for field, mark in zip(fields, answer_marks(name), strict=True):
        if not field.stretch:
            continue
        box = mark["box"]
        assert isinstance(box, dict)
        lines = wrap(content(field), float(box["width"]), metrics).lines
        assert len(lines) * metrics.leading == pytest.approx(float(box["height"]))
