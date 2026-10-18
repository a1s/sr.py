"""The twelve invariants every printout satisfies.

doc/printout.md#invariants lists them,
and three parties hold a printout to them:

* **The engine**, before it hands a printout out.  A build that
  produced a document breaking its own invariants is a fault in
  the engine, and doc/cli.md#exit-codes makes it a failed run
  rather than a document with a defect nobody was told about.
* **`s-reports inspect`**, after its dump, per doc/cli.md#sr-inspect:
  an inspector that read a broken printout without saying so
  would be the wrong tool.
* **The test suite**, on every printout it produces, which it gets
  by way of the first: nothing leaves the engine unchecked.

The check reads **records** -- the objects both encodings write
and both readers return -- rather than :mod:`sr.printout.model`.
`inspect` reads printouts another engine wrote, and records are
what it has; the engine turns its model into records with
:func:`sr.printout.write.records`, the same objects it writes,
so what is checked is what a reader sees.

Records are read defensively.  A field that is missing or of
the wrong type is itself a violation where an invariant needs it,
and is otherwise passed over, so a malformed printout is reported
rather than crashing the reader.  A name is looked up only where
it is a string, since an array in its place cannot be hashed,
and a number is used only where it is finite as a double.

The seventh invariant is also a rule of layout, doc/layout.md#errors:
a mark outside the printable area is an overflow.  :func:`crossing`
is that judgement, shared by both, so the engine and the check cannot
disagree about where a margin is.

"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, TypeGuard

from sr.printout.model import VERSION, Printout
from sr.printout.write import number, records
from sr.units import fits, round_points

__all__ = [
    "Area",
    "Crossing",
    "check",
    "crossing",
    "described",
    "listed",
    "mapped",
    "violations",
]

# A page's geometry, in the order Area.of takes it.
GEOMETRY = (
    "width",
    "height",
    "leftMargin",
    "rightMargin",
    "topMargin",
    "bottomMargin",
)

# What a mark of each kind is called in a sentence about it.
CALLED = {
    "text": "text",
    "line": "line",
    "rectangle": "rectangle",
    "image": "image",
    "barcode": "barcode",
    "outline": "outline entry",
    "xref": "xref",
}


@dataclass(frozen=True)
class Area:
    """A page's printable area, inside its margins, in page coordinates.

    Attributes:
        left: The left margin's edge.
        top: The top margin's edge.
        right: The right margin's edge, measured from the page's left.
        bottom: The bottom margin's edge, measured from the page's top.

    """

    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def of(
        cls,
        width: float,
        height: float,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> Area:
        """Return the printable area of a page of this geometry.

        Args:
            width: The paper's width.
            height: Its height.
            left: The left margin.
            right: The right margin.
            top: The top margin.
            bottom: The bottom margin.

        """
        return cls(
            left, top, round_points(width - right), round_points(height - bottom)
        )


@dataclass(frozen=True)
class Crossing:
    """Where a mark leaves the printable area.

    Attributes:
        side: ``left``, ``right``, ``top``, or ``bottom``: the margin crossed.
        at: Where the mark's edge on that side is.
        limit: Where the margin is.

    """

    side: str
    at: float
    limit: float


def crossing(
    area: Area, x: float, y: float, width: float = 0.0, height: float = 0.0
) -> Crossing | None:
    """Return the first margin a rectangle or a point crosses, if any.

    doc/printout.md#invariants allows the 3-decimal rounding tolerance,
    and the comparison is the one doc/layout.md#coordinates-and-rounding
    writes for a frame boundary, ``extent <= limit + 0.001`` in binary64,
    so a mark flush with a margin lies inside it.  The sides are tried
    left, right, top, bottom, and the first one crossed is the answer.

    Args:
        area: The page's printable area.
        x: The left edge, or the point's X.
        y: The top edge, or the point's Y.
        width: The extent across; zero for a point.
        height: The extent down; zero for a point.

    """
    right = round_points(x + width)
    bottom = round_points(y + height)
    if not fits(area.left, x):
        return Crossing("left", x, area.left)
    if not fits(right, area.right):
        return Crossing("right", right, area.right)
    if not fits(area.top, y):
        return Crossing("top", y, area.top)
    if not fits(bottom, area.bottom):
        return Crossing("bottom", bottom, area.bottom)
    return None


def described(kind: str, found: Crossing) -> str:
    """Return what a mark outside the printable area says about it.

    The same sentence for the engine's overflow and for the check,
    with the measured values that doc/layout.md#errors asks for.

    Args:
        kind: The mark's `kind`.
        found: Where it leaves the area.

    """
    called = CALLED.get(kind, kind)
    edge = " is" if kind == "outline" else f"'s {found.side} edge is"
    return (
        f"the {called}{edge} at {number(found.at)} pt, "
        f"past the {found.side} margin at {number(found.limit)} pt"
    )


def check(printout: Printout) -> list[str]:
    """Return every invariant a printout in memory breaks.

    The records are made one at a time and read as they come,
    so no more than one page of them is held at once.  The `data`
    table's contents are left out of them: no invariant reads
    the bytes of a blob, only its name.

    Args:
        printout: The document, as the engine built it.

    """
    return violations(records(printout, None, contents=False))


def violations(found: Iterable[Any]) -> list[str]:
    """Return every invariant a printout's records break, in a reading order.

    The header's own invariants come first, then each page's, mark by
    mark in paint order, then the two that span the document: outline
    targets, which a mark may name before or after the entry it targets.
    The records are read once, in order, so a caller may hand them over
    as they are made.

    Args:
        found: The records, header first, as a reader returns them.

    """
    lines = iter(found)
    header = next(lines, None)
    if not isinstance(header, dict):
        return ["the first record is not a header"]
    opening: list[str] = []
    said: list[str] = []
    version = header.get("sr")
    if version != VERSION or isinstance(version, bool):
        opening.append(
            f"the header's format version is {plain(version)}, "
            f"and this reader understands {VERSION}"
        )
    data = mapped(header.get("data"))
    font_names: set[str] = set()
    for one in listed(header.get("fonts")):
        if not isinstance(one, dict):
            continue
        if isinstance(one.get("name"), str):
            font_names.add(one["name"])
        blob = one.get("resolvedData")
        if blob is not None and not held(blob, data):
            said.append(
                f"font {plain(one.get('name'))}: its data {plain(blob)} "
                "is not in the header's data table"
            )
    paper, wrong = paper_of(header.get("page"))
    said.extend(wrong)
    warnings = header.get("warnings")
    overflowed = isinstance(warnings, list) and any(
        isinstance(one, dict) and one.get("kind") == "overflow" for one in warnings
    )
    names: set[str] = set()
    targets: list[tuple[str, Any]] = []
    previous = 0
    followed = 0
    for ordinal, page in enumerate(lines, start=1):
        followed = ordinal
        if not isinstance(page, dict):
            said.append(f"page line {ordinal} is not an object")
            continue
        label = f"page {plain(page.get('number', ordinal))}"
        area, wrong = area_of(page, paper)
        said.extend(f"{label}: {one}" for one in wrong)
        marks = page.get("marks")
        if not isinstance(marks, list):
            said.append(f"{label}: it has no marks array")
            continue
        for where, mark in walk(marks, f"{label}, mark "):
            if not isinstance(mark, dict):
                said.append(f"{where}: it is not an object")
                continue
            kind = mark.get("kind")
            if kind == "outline":
                if isinstance(mark.get("name"), str):
                    names.add(mark["name"])
                level = mark.get("level")
                if not isinstance(level, int) or isinstance(level, bool):
                    said.append(f"{where}: the outline entry has no level")
                else:
                    if level > previous + 1 or level < 1:
                        said.append(
                            f"{where}: the outline entry is at level {level}, "
                            f"and the one before it at level {previous}"
                        )
                    previous = level
            elif kind == "xref" and mark.get("type") == "outline":
                targets.append((where, mark.get("target")))
            said.extend(mark_violations(where, mark, font_names, data))
            if not overflowed and area is not None:
                said.extend(placement_violations(where, mark, area))
    for where, target in targets:
        if target is None:
            said.append(f"{where}: the xref to an outline entry has no target")
        elif not held(target, names):
            said.append(
                f"{where}: the xref targets the outline entry {plain(target)}, "
                "and no entry is named so"
            )
    counted = header.get("pages")
    if counted != followed or isinstance(counted, bool):
        opening.append(
            f"the header counts {plain(counted)} pages, "
            f"and {followed} page lines follow"
        )
    return opening + said


def walk(marks: list[Any], prefix: str) -> Iterator[tuple[str, Any]]:
    """Yield every mark with where it is, an xref's nested marks after it.

    A mark is numbered from 1 in paint order, and one inside an xref
    after the xref's number, so ``page 2, mark 5.1`` is the first mark
    inside the fifth.

    Args:
        marks: A page's marks, or an xref's.
        prefix: What goes before each mark's number.

    """
    for index, mark in enumerate(marks, start=1):
        where = f"{prefix}{index}"
        yield where, mark
        if isinstance(mark, dict) and isinstance(mark.get("marks"), list):
            yield from walk(mark["marks"], f"{where}.")


def paper_of(page: Any) -> tuple[dict[str, float], list[str]]:
    """Return the header's page geometry, and what is wrong with it.

    Every page takes its geometry from here where it states none of
    its own, so each of the six fields is needed, and one that cannot
    be read is a violation however many pages it leaves unmeasured.

    Args:
        page: The header's `page` object.

    """
    if not isinstance(page, dict):
        return {}, ["the header has no page object"]
    values: dict[str, float] = {}
    wrong: list[str] = []
    for key in GEOMETRY:
        value = page.get(key)
        if is_number(value):
            values[key] = float(value)
        elif value is None:
            wrong.append(f"the header's page has no {key}")
        else:
            shown = plain(value)
            wrong.append(f"the header's page {key} is {shown}, not a number")
    return values, wrong


def area_of(
    page: dict[str, Any],
    paper: dict[str, float],
) -> tuple[Area | None, list[str]]:
    """Return a page's printable area, and what is wrong with its geometry.

    A page states only what differs from the header's `page`,
    and each of its six fields does so independently.
    Where a field cannot be read, here or in the header, there is
    no area, and what cannot be read is said where it is written.

    Args:
        page: The page record.
        paper: What the header's `page` says, as far as it can be read.

    """
    values = dict(paper)
    wrong: list[str] = []
    for key in GEOMETRY:
        if key not in page:
            continue
        value = page[key]
        if is_number(value):
            values[key] = float(value)
        else:
            values.pop(key, None)
            wrong.append(f"its {key} is {plain(value)}, not a number")
    if len(values) < len(GEOMETRY):
        return None, wrong
    return Area.of(*(values[key] for key in GEOMETRY)), wrong


def mark_violations(
    where: str, mark: dict[str, Any], fonts: set[str], data: dict[str, Any]
) -> Iterator[str]:
    """Yield what one mark breaks of the invariants about a mark alone.

    Those are the third, fourth, sixth, eighth, ninth, eleventh,
    and twelfth.  Where a mark sits on the page is the seventh,
    which a warning can waive, so it is apart.

    Args:
        where: The mark, as a reader would find it.
        mark: The mark record.
        fonts: The names of the header's fonts.
        data: The header's data table.

    """
    kind = str(mark.get("kind"))
    called = CALLED.get(kind, kind)
    box = box_of(mark)
    if kind != "outline":
        if box is None:
            yield f"{where}: the {called} has no box"
        else:
            for key in ("width", "height"):
                if box[key] < 0:
                    yield (
                        f"{where}: the {called}'s box has a "
                        f"negative {key}, {number(box[key])}"
                    )
    if kind == "text":
        if not held(mark.get("font"), fonts):
            yield (
                f"{where}: its font {plain(mark.get('font'))} "
                "is not in the header's font table"
            )
        yield from text_violations(where, mark, box)
    elif kind == "image":
        has_data = mark.get("data") is not None
        has_file = mark.get("file") is not None
        if has_data == has_file:
            which = "both data and file" if has_data else "neither data nor file"
            yield f"{where}: the image carries {which}"
        if has_data and not held(mark.get("data"), data):
            yield (
                f"{where}: its data {plain(mark.get('data'))} "
                "is not in the header's data table"
            )
    elif kind == "barcode":
        ink = mark.get("ink")
        if not isinstance(ink, str) or not ink:
            yield f"{where}: the barcode carries no ink"
        if box is not None:
            yield from barcode_violations(where, mark, box)


def text_violations(
    where: str, mark: dict[str, Any], box: dict[str, float] | None
) -> Iterator[str]:
    """Yield what a text mark breaks of the eighth invariant.

    Args:
        where: The mark, as a reader would find it.
        mark: The mark record.
        box: Its box, where it has a readable one.

    """
    lines = mark.get("lines")
    if not isinstance(lines, list) or not lines:
        yield f"{where}: the text has no lines"
        return
    leading = mark.get("leading")
    if not is_number(leading):
        yield f"{where}: the text has no leading"
        return
    if box is None:
        return
    needed = len(lines) * leading
    if not fits(needed, box["height"]):
        yield (
            f"{where}: {len(lines)} lines of {number(leading)} pt "
            f"need {number(round_points(needed))} pt, "
            f"in a box {number(box['height'])} pt tall"
        )


def barcode_violations(
    where: str, mark: dict[str, Any], box: dict[str, float]
) -> Iterator[str]:
    """Yield what a barcode mark breaks of the ninth invariant.

    The runs are in modules, so their sum times the module is a length,
    and it has to be the box's along the coding direction, which is
    across the page unless the symbol is `vertical`.  A 2-D symbol's
    rows have one more length to match, across the coding direction.

    Args:
        where: The mark, as a reader would find it.
        mark: The mark record.
        box: Its box.

    """
    module = mark.get("module")
    if not is_number(module):
        yield f"{where}: the barcode has no module"
        return
    vertical = mark.get("vertical") is True
    along, across = (
        (box["height"], box["width"]) if vertical else (box["width"], box["height"])
    )
    stripes = mark.get("stripes")
    rows = mark.get("rows")
    if stripes is not None and rows is not None:
        yield f"{where}: the barcode carries both stripes and rows"
        return
    if stripes is not None:
        if not runs(stripes):
            yield f"{where}: the barcode's stripes are not runs of modules"
            return
        yield from matched(where, "the stripes", sum(stripes), module, along, "long")
        return
    if not isinstance(rows, list) or not rows or not all(runs(row) for row in rows):
        yield f"{where}: the barcode carries neither stripes nor rows of runs"
        return
    sums = {sum(row) for row in rows}
    if len(sums) != 1:
        yield f"{where}: the barcode's rows are not all one length"
        return
    yield from matched(where, "each row", sums.pop(), module, along, "long")
    yield from matched(where, "the rows", len(rows), module, across, "across")


def matched(
    where: str, what: str, count: int, module: float, extent: float, word: str
) -> Iterator[str]:
    """Yield a violation where a count of modules is not the box's length.

    Args:
        where: The mark, as a reader would find it.
        what: What was counted, for the sentence.
        count: How many modules.
        module: The module, in points.
        extent: The box's length in that direction.
        word: ``long`` or ``across``, for the sentence.

    """
    try:
        made = round_points(count * module)
    except OverflowError:
        # More modules than a double counts, which no box matches.
        made = math.inf
    if fits(made, extent) and fits(extent, made):
        return
    yield (
        f"{where}: {what} make {count} modules of {number(module)} pt, "
        f"{number(made)} pt, and the box is {number(extent)} pt {word}"
    )


def placement_violations(
    where: str,
    mark: dict[str, Any],
    area: Area,
) -> Iterator[str]:
    """Yield a violation where a mark lies outside its page's printable area.

    The seventh invariant.  An outline entry is a point,
    and every other mark is its box; an xref's nested marks
    are judged as marks of their own, as they are walked.

    Args:
        where: The mark, as a reader would find it.
        mark: The mark record.
        area: Its page's printable area.

    """
    kind = str(mark.get("kind"))
    if kind == "outline":
        x, y = mark.get("x"), mark.get("y")
        if not (is_number(x) and is_number(y)):
            yield f"{where}: the outline entry has no point"
            return
        found = crossing(area, x, y)
    else:
        box = box_of(mark)
        if box is None:
            return
        found = crossing(area, box["x"], box["y"], box["width"], box["height"])
    if found is not None:
        yield f"{where}: {described(kind, found)}"


def box_of(mark: dict[str, Any]) -> dict[str, float] | None:
    """Return a mark's box, or ``None`` where it has no readable one.

    Args:
        mark: The mark record.

    """
    box = mark.get("box")
    if not isinstance(box, dict):
        return None
    found: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        value = box.get(key)
        if not is_number(value):
            return None
        found[key] = value
    return found


def listed(value: Any) -> list[Any]:
    """Return a record's value where it is an array, and an empty one otherwise.

    Args:
        value: What the record held.

    """
    return value if isinstance(value, list) else []


def mapped(value: Any) -> dict[str, Any]:
    """Return a record's value where it is an object, and an empty one otherwise.

    Args:
        value: What the record held.

    """
    return value if isinstance(value, dict) else {}


def runs(value: Any) -> bool:
    """Report whether a value is a list of run lengths: non-negative integers.

    Args:
        value: What the record held.

    """
    return isinstance(value, list) and all(
        isinstance(one, int) and not isinstance(one, bool) and one >= 0 for one in value
    )


def held(value: Any, table: Collection[str]) -> bool:
    """Report whether a record's value is a name a table holds.

    Only a string is a name, and only a name is looked up:
    an array where a name belongs would not hash.

    Args:
        value: What the record held.
        table: The names, or a table keyed by them.

    """
    return isinstance(value, str) and value in table


def is_number(value: Any) -> TypeGuard[float]:
    """Report whether a record's value is a finite number.

    A boolean is not one, and nor is an infinity, a NaN, or an integer
    past the range of a double: no length in a printout is any of those,
    and arithmetic on them would overflow or compare false either way.

    Args:
        value: What the record held.

    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def plain(value: Any) -> str:
    """Return a record's value as a sentence shows it.

    Args:
        value: What the record held.

    """
    if isinstance(value, str):
        return f'"{value}"'
    if value is None:
        return "absent"
    if is_number(value):
        return number(value)
    return str(value)
