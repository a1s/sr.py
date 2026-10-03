"""Splitting a band: where it may be cut, and the two halves a cut makes.

doc/layout.md#legal-split-points has three requirements, and the
difference between them is what the last branch of placing a band
turns on:

1. **The cut must not fall through a mark.**  An offset no mark's span
   strictly contains is a *cut point*, and so is an offset on one of
   a stretch field's line boundaries.  A mark's span is its content
   box, not the box it was given, so the empty space under a field's
   one line has nothing in it to divide.
2. **The cut must divide content**, so that neither half is a blank strip.
3. **``orphans`` and ``widows`` must hold** for every field the cut
   passes through.

A cut point that meets all three is a *legal split point*.
A band too tall for any frame is cut at a cut point, having
given up the other two.

Both halves are already built.  The head keeps its marks where they are,
a split field keeps its leading lines and says ``lastLineJustified``,
and the tail's marks move up by the cut, since the tail continues
from the top of the next frame.  Nothing is measured a second time.

"""

from __future__ import annotations

from dataclasses import replace

from sr.layout.defer import Deferral
from sr.layout.measure import Extent, Measurement
from sr.printout.model import Box, Mark, Text
from sr.units import fits, round_points

__all__ = ["choose", "is_cut", "is_legal", "split"]


def boundaries(extent: Extent) -> list[float]:
    """Return the offsets between a stretch field's lines.

    Args:
        extent: The field's span.

    """
    return [
        round_points(extent.top + taken * extent.leading)
        for taken in range(1, extent.lines)
    ]


def through(extent: Extent, cut: float) -> bool:
    """Report whether a cut falls strictly inside an element's span.

    Args:
        extent: The element's span.
        cut: The offset, band-relative.

    """
    return extent.top < cut < extent.bottom


def is_cut(measured: Measurement, cut: float) -> bool:
    """Report whether an offset is a cut point: no mark is cut through.

    Args:
        measured: The band.
        cut: The offset, band-relative.

    """
    for extent in measured.extents:
        if through(extent, cut) and cut not in boundaries(extent):
            return False
    return True


def is_legal(measured: Measurement, cut: float, orphans: int, widows: int) -> bool:
    """Report whether a cut point is a legal split point.

    Args:
        measured: The band.
        cut: The offset, band-relative.
        orphans: The fewest lines a split field may leave above the cut.
        widows: The fewest it may carry below.

    """
    if not is_cut(measured, cut):
        return False
    above = below = False
    for extent in measured.extents:
        if through(extent, cut):
            taken = lines_above(extent, cut)
            if taken < orphans or extent.lines - taken < widows:
                return False
            above = below = True
        elif extent.bottom <= cut:
            above = True
        else:
            below = True
    return above and below


def lines_above(extent: Extent, cut: float) -> int:
    """Return how many of a stretch field's lines a cut leaves above it.

    Args:
        extent: The field's span, which the cut falls on a boundary of.
        cut: The offset, band-relative.

    """
    return boundaries(extent).index(cut) + 1


def choose(
    measured: Measurement,
    limit: float,
    orphans: int = 1,
    widows: int = 1,
    *,
    legal: bool = True,
) -> float | None:
    """Return the greatest offset within ``limit`` the band may be cut at.

    The candidates are every offset something in the band starts, ends,
    or breaks a line at, and ``limit`` itself, which may fall in empty
    space.  An offset of zero is never a cut, since it moves the whole
    band and takes nothing from it.

    Args:
        measured: The band.
        limit: The space available for the head.
        orphans: The band's ``orphans``.
        widows: The band's ``widows``.
        legal: Whether the cut must be a legal split point, or only
            a cut point, which is what a band too tall for any frame asks.

    """
    candidates = {limit}
    for extent in measured.extents:
        candidates.add(extent.top)
        candidates.add(extent.bottom)
        candidates.update(boundaries(extent))
    for cut in sorted(candidates, reverse=True):
        if cut <= 0 or cut >= measured.height or not fits(cut, limit):
            continue
        if legal and is_legal(measured, cut, orphans, widows):
            return cut
        if not legal and is_cut(measured, cut):
            return cut
    return None


def split(measured: Measurement, cut: float) -> tuple[Measurement, Measurement]:
    """Return a band's head and tail, cut at a cut point.

    An element wholly above the cut is the head's as it is, and one
    wholly below it the tail's, moved up by the cut.  A stretch field the
    cut falls inside is divided at the line boundary: its leading lines
    stay, marked as not ending a paragraph, and the rest start the tail.

    A deferred element goes with its mark, which a cut never divides,
    and takes the index that mark has in its half.

    Args:
        measured: The band.
        cut: A cut point, band-relative.

    """
    head: list[tuple[Mark, Extent]] = []
    tail: list[tuple[Mark, Extent]] = []
    # Where each mark went: whether to the head, and its index there.
    went: list[tuple[bool, int]] = []
    for mark, extent in zip(measured.marks, measured.extents, strict=True):
        if extent.bottom <= cut:
            went.append((True, len(head)))
            head.append((mark, extent))
        elif extent.top >= cut:
            went.append((False, len(tail)))
            tail.append((mark.moved(0.0, -cut), extent.moved(-cut)))
        else:
            above, below = divide(mark, extent, cut)
            went.append((True, len(head)))
            head.append(above)
            tail.append(below)
    deferred: tuple[list[Deferral], list[Deferral]] = ([], [])
    for one in measured.deferred:
        first, *rest = one.path
        upper, index = went[first]
        deferred[0 if upper else 1].append(one.at((index, *rest)))
    return (
        Measurement(
            cut,
            tuple(mark for mark, _ in head),
            tuple(extent for _, extent in head),
            tuple(deferred[0]),
        ),
        Measurement(
            round_points(measured.height - cut),
            tuple(mark for mark, _ in tail),
            tuple(extent for _, extent in tail),
            tuple(deferred[1]),
        ),
    )


def divide(
    mark: Mark, extent: Extent, cut: float
) -> tuple[tuple[Mark, Extent], tuple[Mark, Extent]]:
    """Return a stretch field's two halves at a line boundary.

    Args:
        mark: The field's text mark.
        extent: Its span.
        cut: The line boundary it is cut at, band-relative.

    """
    assert isinstance(mark, Text)
    taken = lines_above(extent, cut)
    leading = extent.leading
    head = replace(
        mark,
        box=Box(
            mark.box.x,
            mark.box.y,
            mark.box.width,
            round_points(taken * leading),
        ),
        lines=mark.lines[:taken],
        last_line_justified=True,
    )
    rest = len(mark.lines) - taken
    tail = replace(
        mark,
        box=Box(mark.box.x, 0.0, mark.box.width, round_points(rest * leading)),
        lines=mark.lines[taken:],
    )
    return (
        (head, Extent(extent.top, cut, taken, leading)),
        (tail, Extent(0.0, round_points(extent.bottom - cut), rest, leading)),
    )
