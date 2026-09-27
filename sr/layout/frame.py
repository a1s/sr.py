"""Frames: the regions bands fill, from the top down.

doc/layout.md#frames describes a tree, each frame's geometry derived
from its parent's.  The page frame is the root: the page box inset by
the four margins, with the page header and footer reserved out of it.
A `columns` node makes a child, on `layout` or on any `group`, and
a group's title and summary belong to the frame that *contains* the
group's columns, which is what lets a group title span every column.

The nesting of a template is linear, one group per level, so the tree
is a chain: every frame has at most one child.

Two things are separate here, and the separation is the point.

* **A frame's extent on a page is fixed.**  Its ``top`` is its parent's
  top with its own header reserved below it, and its ``bottom`` its
  parent's bottom with its own footer reserved above it.  That is what
  ``VERTICAL_POSITION`` and ``VERTICAL_SPACE`` are measured against,
  and where a header and a footer are drawn.
* **Where the next band goes is the frame's ``fill``**, and it moves.
  A band committed to a frame advances that frame's fill, pushes every
  frame above it down to at least the band's bottom, since a column
  lies inside its parent's current column, and pushes every frame below
  it down too, since the band spans all of their columns.  The frames
  below keep that as their ``floor``, which is where a column they open
  later on the same page begins: below what their parent put across it.

:class:`Window` is the view a band is measured against: an extent
and a fill, and nothing about the tree.

"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from sr.errors import NodePath
from sr.template.model import Section, Style
from sr.units import fits, round_points

__all__ = ["Frame", "Window"]


@dataclass
class Window:
    """What a band is measured against: an extent, and where it starts.

    Attributes:
        x: The left edge of the column.
        width: Its width.
        top: The first Y content may occupy.
        bottom: The Y content must stop at.
        fill: Where the band being measured would go.

    """

    x: float
    width: float
    top: float
    bottom: float
    fill: float = field(init=False)

    def __post_init__(self) -> None:
        """Start the fill position at the top."""
        self.fill = self.top

    @property
    def height(self) -> float:
        """Return the most a band could get, which is an empty window."""
        return round_points(self.bottom - self.top)

    @property
    def available(self) -> float:
        """Return the space left below the fill."""
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


@dataclass
class Frame:
    """One region of the frame tree, as it stands on the current page.

    Attributes:
        count: How many columns; 1 for the page frame.
        gap: The space between two columns.
        balance: Whether a page's bands are spread over the columns.
        header: The band reserved at the top of each column.
        footer: The band reserved at the bottom of each column.
        styles: The `columns` node's `style` nodes, which a band
            inside it walks after its own.
        path: The node that made the frame, for a diagnostic.
        parent: The frame this one divides.
        child: The frame dividing this one, where there is one.
        column: The current column, from 0.
        x: The left edge of the current column.
        width: The width of one column.
        outer_top: Where the header goes: the parent's top.
        outer_bottom: Where the footer ends: the parent's bottom.
        top: The first Y content may occupy, below the header.
        bottom: The Y content must stop at, above the footer.
        fill: Where the next band goes in the current column.
        floor: The lowest edge a band an ancestor placed reaches on
            this page, which is where a column opened later begins.
        reserved_header: What the header reserved in this column.
        reserved_footer: What the footer reserved in this column.

    """

    count: int = 1
    gap: float = 0.0
    balance: bool = False
    header: Section | None = None
    footer: Section | None = None
    styles: tuple[Style, ...] = ()
    path: NodePath = field(default_factory=NodePath)
    parent: Frame | None = None
    child: Frame | None = None
    column: int = 0
    x: float = 0.0
    width: float = 0.0
    outer_top: float = 0.0
    outer_bottom: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    fill: float = 0.0
    floor: float = 0.0
    reserved_header: float = 0.0
    reserved_footer: float = 0.0

    # -- the tree ---------------------------------------------------------

    def ancestors(self) -> Iterator[Frame]:
        """Yield every frame above this one, nearest first."""
        up = self.parent
        while up is not None:
            yield up
            up = up.parent

    def descendants(self) -> Iterator[Frame]:
        """Yield every frame below this one, nearest first."""
        down = self.child
        while down is not None:
            yield down
            down = down.child

    @property
    def columned(self) -> bool:
        """Report whether the frame was made by a `columns` node."""
        return self.parent is not None

    @property
    def spare(self) -> bool:
        """Report whether the frame has a column it has not used yet."""
        return self.column < self.count - 1

    # -- geometry ---------------------------------------------------------

    def place_across(self) -> None:
        """Set ``x`` and ``width`` for the current column.

        doc/template.md#columns: the column width is the parent's less
        the gaps, shared equally, and the current column's left edge
        is that many widths and gaps in from the parent's.

        """
        assert self.parent is not None
        parent = self.parent
        self.width = round_points(
            (parent.width - (self.count - 1) * self.gap) / self.count
        )
        self.x = round_points(parent.x + (self.width + self.gap) * self.column)

    def settle(self, header: float, footer: float) -> None:
        """Take a column's reservations out of its extent.

        Args:
            header: What the header measured.
            footer: What the footer measured.

        """
        self.reserved_header = header
        self.reserved_footer = footer
        self.top = round_points(self.outer_top + header)
        self.bottom = round_points(self.outer_bottom - footer)
        self.fill = max(self.top, self.floor)

    @property
    def start(self) -> float:
        """Return where a band begins in an empty column of this frame.

        The frame's ``top``, or the innermost frame's inside it:
        every column this one opens draws their headers again,
        and a band placed across them goes below.

        """
        innermost = self
        for down in self.descendants():
            innermost = down
        return innermost.top

    @property
    def height(self) -> float:
        """Return the most a band could get, which is an empty column."""
        return round_points(self.bottom - self.start)

    @property
    def available(self) -> float:
        """Return the space left below what has been placed."""
        return round_points(self.bottom - self.fill)

    def accepts(self, height: float) -> bool:
        """Report whether a band of this height fits what is left.

        Args:
            height: The measured height of the band.

        """
        return fits(height, self.available)

    @property
    def empty(self) -> bool:
        """Report whether the column offers what an empty one would.

        No eject could give a band more room than that.  A column
        that begins at its floor, below a band an ancestor placed
        across it, is not empty although nothing has been placed
        in it: the next page offers more.

        """
        return self.fill <= self.start

    def window(self) -> Window:
        """Return the view a band in this frame is measured against."""
        view = Window(self.x, self.width, self.top, self.bottom)
        view.fill = self.fill
        return view

    # -- filling ----------------------------------------------------------

    def advance(self, bottom: float) -> None:
        """Record that a band placed in this frame ends at ``bottom``.

        Every frame above takes the band as reaching at least that far,
        because each of them holds this one inside its current column;
        every frame below does too, because the band spans all of their
        columns, and keeps it as a floor for a column it opens later.

        Args:
            bottom: The lower edge of what was placed.

        """
        self.fill = max(self.fill, bottom)
        for up in self.ancestors():
            up.fill = max(up.fill, bottom)
        for down in self.descendants():
            down.floor = max(down.floor, bottom)
            down.fill = max(down.fill, bottom)

    def reach(self, bottom: float) -> None:
        """Record that a band drawn in this column ends at ``bottom``.

        What :meth:`advance` does for the frames above, without moving
        this frame's own fill: a header and a footer are drawn at an
        edge of the column rather than filled into it.

        Args:
            bottom: The lower edge of what was drawn.

        """
        for up in self.ancestors():
            up.fill = max(up.fill, bottom)
