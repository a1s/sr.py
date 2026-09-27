"""Balanced columns: spreading a page's bands so the columns end level.

doc/layout.md#balanced-columns balances the **fragment**, which is what
a frame was given since the current page opened, and does it as the page
ends: at the page break before the footers, and at the end of the report
before the summary.

1. Each column of the fragment begins where its first band was placed.
   A column the fragment never reached begins at the frame's top, and
   is open to it only where no header or footer would have to be placed
   in it after the fact.
2. The fill is reproduced by packing the bands into the same columns
   to the same bottom.  If that does not put every band exactly where
   it went, something other than the room left decided it, and the
   fragment is left alone.
3. The shallowest bottom the same bands still reach in those columns is
   found by bisection, and every band is moved to where it is assigned.
4. The frame is left filled to the deepest of the balanced columns.

Nothing is measured again.  The columns are one width, so moving a band
is a translation of the marks already built, and the marks keep the order
they were painted in.

"""

from __future__ import annotations

from dataclasses import dataclass, field

from sr.layout.frame import Frame
from sr.printout.model import Mark
from sr.units import fits, round_points

__all__ = ["Fragment", "balance"]

# Bisection runs on whole thousandths of a point, which is the grid every
# coordinate is on, so it settles on the smallest bottom that grid has.
SCALE = 1000


@dataclass
class Placed:
    """One band of a fragment, where the fill put it.

    Attributes:
        column: The column, from 0.
        start: The index of its first mark on the page.
        end: The index after its last.
        top: The Y it was placed at.
        height: Its height.

    """

    column: int
    start: int
    end: int
    top: float
    height: float


@dataclass
class Fragment:
    """What a balanced frame was given since the page opened.

    Attributes:
        frame: The frame.
        alone: Whether something happened that balancing would not
            reproduce, which leaves the fragment where the fill put it.
        bands: The bands, in the order they were placed.

    """

    frame: Frame
    alone: bool = False
    bands: list[Placed] = field(default_factory=list)

    def add(self, column: int, start: int, end: int, top: float, height: float) -> None:
        """Note one band placed in the frame.

        Args:
            column: The column it went in.
            start: The index of its first mark on the page.
            end: The index after its last.
            top: The Y it was placed at.
            height: Its height.

        """
        self.bands.append(Placed(column, start, end, top, height))


def pack(
    bands: list[Placed],
    begin: dict[int, float],
    columns: int,
    limit: float,
    lenient: bool,
) -> list[tuple[int, float]] | None:
    """Return where each band goes when packed to ``limit``, or ``None``.

    Bands go into a column while they fit it, and on into the next
    once one does not.  A band too tall for an empty column goes in it
    all the same where ``lenient``, which is what the fill did with it.

    Args:
        bands: The fragment's bands, in order.
        begin: Where each column begins.
        columns: How many columns may be used.
        limit: The bottom each column is packed to.
        lenient: Whether an oversized band may stand alone in a column.

    """
    column = bands[0].column
    down = begin[column]
    empty = True
    found: list[tuple[int, float]] = []
    for band in bands:
        if not fits(round_points(down + band.height), limit):
            if not empty:
                column += 1
                if column >= columns:
                    return None
                down = begin[column]
                empty = True
            if not fits(round_points(down + band.height), limit) and not lenient:
                return None
        found.append((column, down))
        down = round_points(down + band.height)
        empty = False
    return found


def balance(fragment: Fragment, marks: list[Mark], opened: bool) -> None:
    """Spread a fragment over its columns, where that reproduces the fill.

    Args:
        fragment: The fragment.
        marks: The page's marks, which the bands' marks are moved in place.
        opened: Whether columns the fragment never reached may be used,
            which they may only where no header or footer is in the frame.

    """
    frame = fragment.frame
    bands = fragment.bands
    reached = max(band.column for band in bands) + 1
    columns = frame.count if opened else reached
    begin: dict[int, float] = {}
    for band in bands:
        begin.setdefault(band.column, band.top)
    for column in range(columns):
        begin.setdefault(column, max(frame.top, frame.floor))
    filled = pack(bands, begin, columns, frame.bottom, lenient=True)
    if filled != [(band.column, band.top) for band in bands]:
        return
    low, high = 0, round(frame.bottom * SCALE)
    while low < high:
        middle = (low + high) // 2
        if pack(bands, begin, columns, middle / SCALE, lenient=False) is None:
            low = middle + 1
        else:
            high = middle
    placed = pack(
        bands, begin, columns, high / SCALE, lenient=high == round(frame.bottom * SCALE)
    )
    assert placed is not None
    deepest = frame.top
    for band, (column, down) in zip(bands, placed, strict=True):
        across = round_points(left(frame, column) - left(frame, band.column))
        drop = round_points(down - band.top)
        for index in range(band.start, band.end):
            marks[index] = marks[index].moved(across, drop)
        deepest = max(deepest, round_points(down + band.height))
    # The frame stays in the column the fill left it in, which is what
    # `COLUMN_NUMBER` reads after this, and is filled to the deepest.
    frame.fill = deepest
    for inner in frame.descendants():
        inner.fill = deepest
    if frame.footer is None:
        for outer in frame.ancestors():
            outer.fill = deepest


def left(frame: Frame, column: int) -> float:
    """Return the left edge of one of a frame's columns.

    Args:
        frame: The frame.
        column: The column, from 0.

    """
    assert frame.parent is not None
    return round_points(frame.parent.x + (frame.width + frame.gap) * column)
