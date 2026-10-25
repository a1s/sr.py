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
what it has.  The engine checks its own printout in memory instead,
with :func:`check`, which says what :func:`violations` says about
the records :func:`sr.printout.write.records` makes of the printout,
sentence for sentence and in the same order, without making them:
making them is most of what checking them costs.  The two differ
only in how they read.  Each rule is written once, against the
plain values both read off what they are given, so the two cannot
judge or word a mark differently, and the test suite holds them
to the same answers.

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
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard, TypeVar

from sr.printout.model import (
    VERSION,
    Barcode,
    Box,
    Image,
    Mark,
    Outline,
    Printout,
    Text,
    Xref,
)
from sr.printout.write import (
    length,
    mark_object,
    number,
    page_geometry,
    paper_object,
)
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

# A mark's box as a check reads it: x, y, width, and height.
# An outline entry's point is one with no extent.
Bounds = tuple[float, float, float, float]

# A mark as a check walks it: a record, or the model's.
Walked = TypeVar("Walked")


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

    What :func:`violations` says about the printout's records,
    read off the model.  The header's format version and its page
    count are the model's own, so neither can be wrong.  An image and
    a barcode carry the most fields a record could get wrong, and are
    the rarest marks, so each is made into its record and read as one.

    Args:
        printout: The document, as the engine built it.

    """
    data = printout.data
    entries = sorted(printout.fonts, key=lambda one: one.name)
    font_names, said = fonts_of(((one.name, one.data) for one in entries), data)
    paper, wrong = paper_of(paper_object(printout.paper))
    said.extend(wrong)
    overflowed = any(one.kind == "overflow" for one in printout.warnings)
    outlines = Outlines()
    for page in printout.pages:
        label = f"page {plain(page.number)}"
        area, wrong = area_of(page_geometry(page, printout.paper), paper)
        said.extend(f"{label}: {one}" for one in wrong)
        for where, mark in walk(page.marks, f"{label}, mark ", inside):
            if isinstance(mark, Outline):
                broken = outlines.entry(where, mark.name, mark.level)
                if broken is not None:
                    said.append(broken)
                bounds = finite(mark.box, point=True)
            else:
                if isinstance(mark, Xref) and mark.link == "outline":
                    outlines.targets.append((where, mark.target))
                bounds = finite(mark.box)
                if isinstance(mark, Image | Barcode):
                    record = mark_object(mark)
                    said.extend(mark_violations(where, record, font_names, data))
                else:
                    said.extend(box_violations(where, mark.kind, bounds))
                if isinstance(mark, Text):
                    leading = length(mark.leading)
                    said.extend(
                        text_violations(
                            where,
                            mark.font,
                            len(mark.lines),
                            leading if math.isfinite(leading) else None,
                            bounds,
                            font_names,
                        )
                    )
            if not overflowed and area is not None:
                broken = placement_violation(where, mark.kind, bounds, area)
                if broken is not None:
                    said.append(broken)
    said.extend(outlines.unheld())
    return said


def inside(mark: Mark) -> Sequence[Mark]:
    """Return the marks an xref holds, and none for any other mark.

    Args:
        mark: The mark.

    """
    return mark.marks if isinstance(mark, Xref) else ()


def finite(box: Box, point: bool = False) -> Bounds | None:
    """Return a box as its record holds it, or ``None`` where it has no reading.

    Args:
        box: The mark's box.
        point: Whether the mark is an outline entry, whose record holds
            the box's corner as its point, with no extent.

    """
    x, y = length(box.x), length(box.y)
    width, height = (0.0, 0.0) if point else (length(box.width), length(box.height))
    for value in (x, y, width, height):
        if not math.isfinite(value):
            return None
    return x, y, width, height


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
    version = header.get("sr")
    if version != VERSION or isinstance(version, bool):
        opening.append(
            f"the header's format version is {plain(version)}, "
            f"and this reader understands {VERSION}"
        )
    data = mapped(header.get("data"))
    fonts = (
        (one.get("name"), one.get("resolvedData"))
        for one in listed(header.get("fonts"))
        if isinstance(one, dict)
    )
    font_names, said = fonts_of(fonts, data)
    paper, wrong = paper_of(header.get("page"))
    said.extend(wrong)
    warnings = header.get("warnings")
    overflowed = isinstance(warnings, list) and any(
        isinstance(one, dict) and one.get("kind") == "overflow" for one in warnings
    )
    outlines = Outlines()
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
        for where, mark in walk(marks, f"{label}, mark ", inside_record):
            if not isinstance(mark, dict):
                said.append(f"{where}: it is not an object")
                continue
            kind = str(mark.get("kind"))
            if kind == "outline":
                broken = outlines.entry(where, mark.get("name"), mark.get("level"))
                if broken is not None:
                    said.append(broken)
            elif kind == "xref" and mark.get("type") == "outline":
                outlines.targets.append((where, mark.get("target")))
            said.extend(mark_violations(where, mark, font_names, data))
            if not overflowed and area is not None:
                broken = placement_violation(where, kind, bounds_of(mark), area)
                if broken is not None:
                    said.append(broken)
    said.extend(outlines.unheld())
    counted = header.get("pages")
    if counted != followed or isinstance(counted, bool):
        opening.append(
            f"the header counts {plain(counted)} pages, "
            f"and {followed} page lines follow"
        )
    return opening + said


def inside_record(mark: Any) -> Sequence[Any]:
    """Return the marks a mark record holds, where it holds an array of them.

    Args:
        mark: The mark record, as read.

    """
    nested = mark.get("marks") if isinstance(mark, dict) else None
    return nested if isinstance(nested, list) else ()


def walk(
    marks: Sequence[Walked], prefix: str, inner: Callable[[Walked], Sequence[Walked]]
) -> Iterator[tuple[str, Walked]]:
    """Yield every mark with where it is, an xref's nested marks after it.

    A mark is numbered from 1 in paint order, and one inside an xref
    after the xref's number, so ``page 2, mark 5.1`` is the first mark
    inside the fifth.  Records and the model are numbered alike.

    Args:
        marks: A page's marks, or an xref's.
        prefix: What goes before each mark's number.
        inner: What returns the marks a mark holds.

    """
    for index, mark in enumerate(marks, start=1):
        where = f"{prefix}{index}"
        yield where, mark
        nested = inner(mark)
        if nested:
            yield from walk(nested, f"{where}.", inner)


def fonts_of(
    fonts: Iterable[tuple[Any, Any]], data: Collection[str]
) -> tuple[set[str], list[str]]:
    """Return the names of the header's fonts, and what is wrong with them.

    The fourth invariant, for a font: the data entry it names,
    where it names one, is in the header's data table.

    Args:
        fonts: Each font's name and the data entry that holds it,
            as read, in the header's order.
        data: The header's data table.

    """
    names: set[str] = set()
    wrong: list[str] = []
    for name, blob in fonts:
        if isinstance(name, str):
            names.add(name)
        if blob is not None and not held(blob, data):
            wrong.append(
                f"font {plain(name)}: its data {plain(blob)} "
                "is not in the header's data table"
            )
    return names, wrong


class Outlines:
    """The outline entries and the xrefs to them, as a check meets them.

    The tenth invariant is about each entry and the one before it,
    and the fifth about each xref to an entry and every entry,
    before it or after: so the names are gathered as the marks
    are read, and the targets are judged once all have been.

    Attributes:
        names: The names of the entries met so far.
        targets: Each xref to an entry met so far, with where it is,
            and its target as read.
        previous: The level of the last entry met, 0 before the first.

    """

    def __init__(self) -> None:
        """Start with no entry met."""
        self.names: set[str] = set()
        self.targets: list[tuple[str, Any]] = []
        self.previous = 0

    def entry(self, where: str, name: Any, level: Any) -> str | None:
        """Note an outline entry, and return what it breaks, if anything.

        Args:
            where: The entry, as a reader would find it.
            name: Its name, as read.
            level: Its level, as read.

        """
        if isinstance(name, str):
            self.names.add(name)
        if not isinstance(level, int) or isinstance(level, bool):
            return f"{where}: the outline entry has no level"
        before, self.previous = self.previous, level
        if level > before + 1 or level < 1:
            return (
                f"{where}: the outline entry is at level {level}, "
                f"and the one before it at level {before}"
            )
        return None

    def unheld(self) -> Iterator[str]:
        """Yield a violation for each xref whose target names no entry."""
        for where, target in self.targets:
            if target is None:
                yield f"{where}: the xref to an outline entry has no target"
            elif not held(target, self.names):
                yield (
                    f"{where}: the xref targets the outline entry {plain(target)}, "
                    "and no entry is named so"
                )


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
    """Yield what one mark record breaks of the invariants about a mark alone.

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
    bounds = box_of(mark)
    if kind != "outline":
        yield from box_violations(where, kind, bounds)
    if kind == "text":
        lines = mark.get("lines")
        leading = mark.get("leading")
        yield from text_violations(
            where,
            mark.get("font"),
            len(lines) if isinstance(lines, list) else 0,
            leading if is_number(leading) else None,
            bounds,
            fonts,
        )
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
        if bounds is not None:
            yield from barcode_violations(where, mark, bounds)


def box_violations(where: str, kind: str, bounds: Bounds | None) -> Iterator[str]:
    """Yield what a mark's box breaks of the sixth invariant.

    Every mark but an outline entry has a box, which is read here.

    Args:
        where: The mark, as a reader would find it.
        kind: The mark's `kind`.
        bounds: Its box, or ``None`` where it has no readable one.

    """
    called = CALLED.get(kind, kind)
    if bounds is None:
        yield f"{where}: the {called} has no box"
        return
    for key, value in (("width", bounds[2]), ("height", bounds[3])):
        if value < 0:
            yield (f"{where}: the {called}'s box has a negative {key}, {number(value)}")


def text_violations(
    where: str,
    font: Any,
    lines: int,
    leading: float | None,
    bounds: Bounds | None,
    fonts: set[str],
) -> Iterator[str]:
    """Yield what a text mark breaks of the third and eighth invariants.

    Args:
        where: The mark, as a reader would find it.
        font: The name of its font, as read.
        lines: How many lines it holds, where it holds an array of them.
        leading: Its leading, or ``None`` where it has no readable one.
        bounds: Its box, or ``None`` where it has no readable one.
        fonts: The names of the header's fonts.

    """
    if not held(font, fonts):
        yield (f"{where}: its font {plain(font)} is not in the header's font table")
    if not lines:
        yield f"{where}: the text has no lines"
        return
    if leading is None:
        yield f"{where}: the text has no leading"
        return
    if bounds is None:
        return
    height = bounds[3]
    needed = lines * leading
    if not fits(needed, height):
        yield (
            f"{where}: {lines} lines of {number(leading)} pt "
            f"need {number(round_points(needed))} pt, "
            f"in a box {number(height)} pt tall"
        )


def barcode_violations(
    where: str, mark: dict[str, Any], bounds: Bounds
) -> Iterator[str]:
    """Yield what a barcode mark breaks of the ninth invariant.

    The runs are in modules, so their sum times the module is a length,
    and it has to be the box's along the coding direction, which is
    across the page unless the symbol is `vertical`.  A 2-D symbol's
    rows have one more length to match, across the coding direction.

    Args:
        where: The mark, as a reader would find it.
        mark: The mark record.
        bounds: Its box.

    """
    module = mark.get("module")
    if not is_number(module):
        yield f"{where}: the barcode has no module"
        return
    vertical = mark.get("vertical") is True
    width, height = bounds[2], bounds[3]
    along, across = (height, width) if vertical else (width, height)
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


def placement_violation(
    where: str, kind: str, bounds: Bounds | None, area: Area
) -> str | None:
    """Return a violation where a mark lies outside its page's printable area.

    The seventh invariant.  An outline entry is a point,
    and every other mark is its box; an xref's nested marks
    are judged as marks of their own, as they are walked.

    Args:
        where: The mark, as a reader would find it.
        kind: The mark's `kind`.
        bounds: Its box, or an outline entry's point with no extent;
            ``None`` where it has no readable one.
        area: Its page's printable area.

    """
    if bounds is None:
        if kind == "outline":
            return f"{where}: the outline entry has no point"
        return None
    found = crossing(area, *bounds)
    if found is None:
        return None
    return f"{where}: {described(kind, found)}"


def bounds_of(mark: dict[str, Any]) -> Bounds | None:
    """Return what of a mark record the seventh invariant places.

    That is an outline entry's point, with no extent, and any other
    mark's box; ``None`` where the record has no readable one.

    Args:
        mark: The mark record.

    """
    if mark.get("kind") != "outline":
        return box_of(mark)
    x, y = mark.get("x"), mark.get("y")
    if not (is_number(x) and is_number(y)):
        return None
    return x, y, 0.0, 0.0


def box_of(mark: dict[str, Any]) -> Bounds | None:
    """Return a mark's box, or ``None`` where it has no readable one.

    Args:
        mark: The mark record.

    """
    box = mark.get("box")
    if not isinstance(box, dict):
        return None
    x, y = box.get("x"), box.get("y")
    width, height = box.get("width"), box.get("height")
    if not (is_number(x) and is_number(y) and is_number(width) and is_number(height)):
        return None
    return x, y, width, height


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
