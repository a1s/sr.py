"""Frames: the regions bands fill, from the top down.

doc/layout.md#frames describes a tree, each frame's geometry derived
from its parent's, with columns and group levels adding children.
What is here is the root of that tree and nothing else: the page frame,
the page box inset by the four margins, with its header and footer
measured and reserved out of ``top`` and ``bottom``.

The children arrive with the nodes that create them.  A `columns` node
makes one, and so does every `group` that carries one, which is why
the fields below are the ones a column frame will need -- ``x`` and
``width`` are the *current column's* extent rather than the page's, and
a frame with one column is the same statement with the count left at 1.

"""

from __future__ import annotations

from dataclasses import dataclass, field

from sr.units import fits, round_points

__all__ = ["Frame"]


@dataclass
class Frame:
    """A rectangle bands fill downward.

    Attributes:
        x: The left edge of the current column.
        width: Its width.
        top: The first Y content may occupy, after the header reservation.
        bottom: The Y content must stop at, before the footer's band.
        fill: Where the next band goes.

    """

    x: float
    width: float
    top: float
    bottom: float
    fill: float = field(init=False)

    def __post_init__(self) -> None:
        """Start the fill position at the top of the frame."""
        self.fill = self.top

    @property
    def height(self) -> float:
        """Return the most a band could get, which is an empty frame."""
        return round_points(self.bottom - self.top)

    @property
    def available(self) -> float:
        """Return the space left below what has been placed."""
        return round_points(self.bottom - self.fill)

    def accepts(self, height: float) -> bool:
        """Report whether a band of this height fits what is left.

        The comparison carries the 0.001 pt tolerance of
        doc/layout.md#coordinates-and-rounding, so a band whose height
        matches the remaining space exactly fits rather than ejecting.

        Args:
            height: The measured height of the band.

        """
        return fits(height, self.available)

    def advance(self, height: float) -> None:
        """Move the fill position down by a committed band's height.

        Args:
            height: The band's height.

        """
        self.fill = round_points(self.fill + height)
