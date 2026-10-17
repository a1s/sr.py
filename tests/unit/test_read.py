"""Reading a printout back into the model, as `sr.py render` does.

The reader's promise is that nothing is lost: a printout written,
read back and written again is the same bytes, in either encoding.
Its other duty is to refuse a field it cannot draw from, naming
where it is, rather than handing the renderer a box whose corner
is a string.

"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.errors import BuildError
from sr.printout.model import Paper, Printout
from sr.printout.read import printout_of, read
from sr.printout.write import lines_of, records, write_cbor, write_jsonl

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = {
    "sakila": ("example/sakila/sakila.kdl", "example/sakila/payments.jsonl"),
    "invoices": (
        "example/invoices/invoices.kdl",
        "example/invoices/invoices.jsonl",
    ),
}


def example(name: str) -> Printout:
    """Return one of the examples, built reproducibly.

    Args:
        name: Its key in :data:`EXAMPLES`.

    """
    template, data = EXAMPLES[name]
    options = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(ROOT / template, ROOT / data, options).printout


@pytest.mark.parametrize("suffix", [".srp.jsonl", ".srp.cbor"])
@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_a_printout_read_back_writes_the_same_bytes(
    name: str,
    suffix: str,
    tmp_path: Path,
) -> None:
    printout = example(name)
    path = tmp_path / f"{name}{suffix}"
    if suffix == ".srp.cbor":
        with path.open("wb") as handle:
            write_cbor(printout, handle, tmp_path)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            write_jsonl(printout, handle, tmp_path)
    again = read(path)
    written = list(lines_of(printout, tmp_path))
    assert list(lines_of(again, tmp_path)) == written


def valid() -> list[dict[str, Any]]:
    """Return the records of a printout that holds one mark of each kind."""
    return list(records(example("sakila"), ROOT))


def first(records_: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Return the first mark of a kind on the first page, nested or not.

    Args:
        records_: The records.
        kind: The mark's kind.

    """
    pending = list(records_[1]["marks"])
    while pending:
        mark: dict[str, Any] = pending.pop(0)
        if mark["kind"] == kind:
            return mark
        pending[:0] = mark.get("marks", [])
    raise AssertionError(f"no {kind} mark on the first page")


def test_a_relative_path_resolves_against_the_printout_s_directory() -> None:
    found = valid()
    found[0]["fonts"][0]["resolvedFile"] = "fonts/Go.ttf"
    found[0]["fonts"][1]["resolvedFile"] = "C:/Fonts/Go.ttf"
    base = Path("archive")
    read_back = printout_of(found, base)
    assert read_back.fonts[0].file == base / "fonts" / "Go.ttf"
    absolute = Path("C:/Fonts/Go.ttf")
    expected = absolute if absolute.is_absolute() else base / absolute
    assert read_back.fonts[1].file == expected


def test_a_page_states_what_differs_and_has_the_rest_from_the_header() -> None:
    found = valid()
    found[1]["width"] = 841.89
    found[1]["height"] = 595.276
    page = printout_of(found, ROOT).pages[0]
    header = found[0]["page"]
    assert page.paper == Paper(
        841.89,
        595.276,
        header["leftMargin"],
        header["rightMargin"],
        header["topMargin"],
        header["bottomMargin"],
    )
    assert printout_of(valid(), ROOT).pages[0].paper is None


def broken(change: str) -> list[dict[str, Any]]:
    """Return the records with one thing wrong in them.

    Args:
        change: Which thing.

    """
    found = copy.deepcopy(valid())
    header, page = found[0], found[1]
    text = first(found, "text")
    image = first(found, "image")
    barcode = first(found, "barcode")
    if change == "version":
        header["sr"] = 2
    elif change == "kind":
        header["kind"] = "page"
    elif change == "no box":
        del text["box"]
    elif change == "box text":
        text["box"]["x"] = "12"
    elif change == "box flag":
        text["box"]["y"] = True
    elif change == "unknown kind":
        text["kind"] = "circle"
    elif change == "colour":
        text["color"] = "black"
    elif change == "align":
        text["align"] = "middle"
    elif change == "lines":
        text["lines"] = ["a", 1]
    elif change == "dash":
        first(found, "line")["dash"] = "dotted"
    elif change == "image both":
        image["file"] = "x.png"
    elif change == "font both":
        header["fonts"][0]["resolvedData"] = "x"
    elif change == "base64":
        next(iter(header["data"].values()))["content"] = "%%%"
    elif change == "encoding":
        next(iter(header["data"].values()))["encoding"] = "gzip"
    elif change == "warning":
        header["warnings"] = [{"kind": "oops", "message": "m"}]
    elif change == "runs":
        barcode["stripes"] = [1, -1]
    elif change == "page kind":
        page["kind"] = "header"
    elif change == "number":
        page["number"] = 1.5
    return found


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ("version", "the header: its format version is 2"),
        ("kind", "the header: its kind is not `header`"),
        ("no box", "page 1, mark 7.1: `box` is missing"),
        ("box text", "page 1, mark 7.1's `box`: `x` is not a number"),
        ("box flag", "`y` is not a number"),
        ("unknown kind", "its kind 'circle' is not one the format defines"),
        ("colour", "`color` is 'black', which is not #RRGGBB"),
        ("align", "`align` is 'middle', and not one of left, center"),
        ("lines", "`lines` holds something that is not text"),
        ("dash", "`dash` is 'dotted'"),
        ("image both", "exactly one of `data` and `file`"),
        ("font both", "exactly one of `resolvedFile` and `resolvedData`"),
        ("base64", "its content is not base64"),
        ("encoding", "its encoding 'gzip' is not base64"),
        ("warning", "its kind 'oops'"),
        ("runs", "`stripes` is not an array of runs"),
        ("page kind", "page line 1: its kind is not `page`"),
        ("number", "page line 1: `number` is not a whole number"),
    ],
)
def test_a_field_the_renderer_cannot_draw_from_is_refused_where_it_is(
    change: str,
    said: str,
) -> None:
    with pytest.raises(BuildError) as refused:
        printout_of(broken(change), ROOT, "out.srp.jsonl")
    message = str(refused.value)
    assert message.startswith("out.srp.jsonl: not a printout: ")
    assert said in message


def test_nothing_at_all_is_not_a_printout() -> None:
    with pytest.raises(BuildError, match="no header"):
        printout_of([], ROOT)
