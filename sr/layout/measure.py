"""Measuring a band: content in, marks and a height out.

doc/layout.md#measure-decide-commit makes this the pure step.
It resolves styles, evaluates expressions, wraps text, floats what
floats, and computes the band's height and its marks **at band-relative
coordinates**, and it mutates nothing: no variable folds, no counters,
no output.  Translating those marks onto a page is the caller's business,
which is what makes splitting and splicing translations of already-built
marks rather than re-measurements.

doc/layout.md#building-a-band is the order, and the part worth reading
twice is how the height is settled, because it is two maxima rather than
one and the difference shows in every band that holds a rule.

1. Every element is placed on the horizontal axis and given its content.
   Those whose vertical extent is **their own** -- a declared height,
   or a content height such as a `stretch` field's wrapped text -- are
   sized as well.
2. The floating elements are moved down below what lies above them.
3. The band's first height is the greater of its declared minimum
   and the lowest bottom edge among the elements sized so far.
4. The **container-dependent** elements, anchored to the band's bottom
   edge, are resolved against that first height.  A rule written
   ``top=10`` and nothing else ends up exactly there.
5. The band is as tall as the lowest bottom edge of the marks it produced,
   or that first height, whichever is greater.

Stage 5 cannot feed back into stage 4, and that is not a simplification:
resolving the rule against the final height instead would make the two
define each other.  A band whose only content is an overflowing field
therefore grows to the text while a rule inside it keeps the height
the declared boxes gave.

An `xref` is the one container inside a band.  Its box comes from its
own geometry and nothing else, it never grows to what it holds, and its
children are laid out against it the way a band's elements are laid out
against the band, except that the height they resolve against is the
xref's rather than a maximum they take part in.  What they hold still
reaches the band's second maximum, so a stretch field inside an xref
that does not hold it pushes the next band down all the same.

Everything that evaluates -- content, conditions, styles -- happens
in stage 1 and in document order, an xref's children at the xref's
place among the band's elements.  A glyph warning names the first element
that needed the character and an error is the first one the document reaches,
which is only true if nothing is evaluated out of turn.

A deferred `field` or `barcode` is the one element whose ``expr``
is not evaluated here at all.  It is measured from its placeholder,
and what it reads where it sits is kept with it, per
doc/layout.md#deferred-evaluation; :meth:`Measurer.resolve` sets
the value in the placeholder's room when the scope ends.

A `barcode` is encoded here, where its content is resolved,
and its symbol's size at the declared module is its content height.
What the symbol is finally drawn at waits for its box, since ``grow``
expands it to what the box offers: doc/layout.md#a-barcode-in-its-box.

"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from sr.barcode import Symbol, Unencodable, encode
from sr.errors import (
    BuildError,
    BuildWarning,
    Location,
    NodePath,
    Unsupported,
)
from sr.expr import Namespace, apply_format
from sr.expr.values import quote
from sr.fonts.text import Metrics, missing_glyph, wrap
from sr.layout.context import Context, condition, evaluate, evaluate_at
from sr.layout.defer import Deferral, scope_of, snapshot
from sr.layout.frame import Window
from sr.printout.model import Barcode as BarcodeMark
from sr.printout.model import Box, Line, Mark, Rectangle, Text
from sr.printout.model import Xref as XrefMark
from sr.printout.write import number
from sr.template.model import (
    Barcode,
    Element,
    Field,
    Section,
    Span,
    Style,
    Xref,
)
from sr.template.model import Line as LineElement
from sr.template.model import Rectangle as RectangleElement
from sr.units import fill_points, fits, round_points

__all__ = ["Extent", "Measurement", "Measurer", "Styling", "resolve_span"]

# The body elements this milestone draws,
# and the milestone that brings each kind it does not.
DRAWN = (Field, LineElement, RectangleElement, Barcode)
ELEMENT_MILESTONE = {"Image": "M11"}

# The colour a mark is drawn in when no `style` in the walk set one.
# doc/template.md leaves a text mark's `color` required and a
# rectangle's `stroke` optional, and the two differ here for that
# reason: text falls back to black, and an outline nothing gave a
# colour to is simply not drawn.
DEFAULT_COLOR = "#000000"

# How far down a box its content starts, per `valign`,
# and how far across, per `halign`.
VALIGN = {"top": 0.0, "center": 0.5, "bottom": 1.0}
HALIGN = {"left": 0.0, "center": 0.5, "right": 1.0}

# A 1-D symbol's bars are this share of its length across the coding
# direction, or a quarter of an inch where that is more.
BAR_SHARE = 0.15
BAR_MINIMUM = 18.0


@dataclass(frozen=True)
class Styling:
    """What a walk of the `style` nodes produced.

    Attributes:
        font: The name of a `font` node, where one was set.
        color: The stroke colour, where one was set.
        bgcolor: The fill colour, where one was set.

    """

    font: str | None = None
    color: str | None = None
    bgcolor: str | None = None


@dataclass(frozen=True)
class Extent:
    """How far down one element's marks reach, which is what a cut reads.

    doc/layout.md#legal-split-points makes an element's vertical span
    the span of the marks it produced: its content box, and for an
    `xref` everything inside it as well.  A stretch field's span may
    be cut between its lines, so it carries how many there are and
    how far apart; every other element's may not be cut at all.

    Attributes:
        top: The span's upper edge, band-relative.
        bottom: Its lower edge.
        lines: A stretch field's line count; 0 for anything else.
        leading: The distance between those lines.

    """

    top: float
    bottom: float
    lines: int = 0
    leading: float = 0.0

    def moved(self, down: float) -> Extent:
        """Return the span translated down.

        Args:
            down: What to add to both edges.

        """
        return Extent(
            round_points(self.top + down),
            round_points(self.bottom + down),
            self.lines,
            self.leading,
        )


@dataclass(frozen=True)
class Measurement:
    """A band, measured but not committed.

    Attributes:
        height: What the band takes from the frame.
        marks: Its marks, at band-relative coordinates.
        extents: Each mark's span, in the same order, for splitting.
        deferred: Its deferred elements, in document order,
            each with the path of its placeholder's mark among ``marks``.

    """

    height: float
    marks: tuple[Mark, ...] = ()
    extents: tuple[Extent, ...] = ()
    deferred: tuple[Deferral, ...] = ()


@dataclass
class Placement:
    """One element on its way from a declaration to a mark.

    Attributes:
        element: The node.
        style: Its resolved formatting.
        x: The left edge, container-relative.
        width: The extent across.
        top: The top edge, once it is known.
        height: The extent down, once it is known.
        lines: The wrapped lines of a `field`.
        content_height: The height the element determines itself,
            where it has one: a `stretch` field's wrapped text.
        dependent: Whether its extent comes from the container's height.
        declared_top: The top of its declared box, for the floating pass.
        declared_bottom: The bottom of that box.
        target: An `xref`'s link, evaluated.
        caption: An `xref`'s hover text, evaluated, where it has one.
        children: An `xref`'s children, placed as far as its box allows.
        marks: An `xref`'s children's marks, relative to its box.
        reach: How far down an `xref`'s contents reach, relative to
            its top, which is what it gives the band's second maximum.
        deferral: What a deferred element waits for its scope with.
        value: The string a `barcode` encodes.
        symbol: What it encodes to.

    """

    element: Element | Xref
    style: Styling
    x: float
    width: float
    top: float = 0.0
    height: float = 0.0
    lines: tuple[str, ...] = ()
    content_height: float | None = None
    dependent: bool = False
    declared_top: float = 0.0
    declared_bottom: float = 0.0
    target: str = ""
    caption: str | None = None
    children: list[Placement] = field(default_factory=list)
    marks: tuple[Mark, ...] = ()
    reach: float = 0.0
    deferral: Deferral | None = None
    value: str = ""
    symbol: Symbol | None = None

    @property
    def floating(self) -> bool:
        """Report whether the element was declared ``float=#true``.

        An element sized from its container never floats, whatever it
        declared: doc/layout.md#what-a-floating-element-may-be leaves it
        out because its top and the band's height would define each other.

        """
        return not self.dependent and getattr(self.element, "floating", False)

    @property
    def bottom(self) -> float:
        """Return the edge below the element as it now stands."""
        return round_points(self.top + self.height)


def resolve_span(span: Span, container: float) -> tuple[float, float]:
    """Return one axis as a start and an extent.

    Exactly two of ``start``, ``end`` and ``size`` are set once
    the loader has filled the axis in, so the third follows from
    the container.  ``limit`` clamps the extent afterwards and
    keeps whichever edge the declaration anchored: a box whose start
    was derived from the other two stays against the container's
    far edge, and every other box keeps its start.

    Args:
        span: The axis, as the template declared it.
        container: The extent of the container.

    """
    start, end, size = span.start, span.end, span.size
    if size is None:
        assert start is not None and end is not None
        size = round_points(container - end - start)
    elif start is None:
        assert end is not None
        start = round_points(container - end - size)
    size = max(0.0, size)
    if span.limit is not None and size > span.limit:
        if span.start is None and span.end is not None:
            start = round_points(container - span.end - span.limit)
        size = span.limit
    assert start is not None
    return start, size


def fitting_lines(lines: tuple[str, ...], height: float, leading: float) -> int:
    """Return how many lines a box of this height holds.

    At least one, always: doc/printout.md#text says a text mark's
    ``lines`` is never empty, so a box too short for a single line
    overflows rather than emptying.  Beyond that the lines beyond
    the box are dropped at a line boundary, which is what a `field`
    without ``stretch`` does with text that does not fit.

    Args:
        lines: The wrapped lines.
        height: The box's height.
        leading: The baseline-to-baseline distance.

    """
    taken = 0
    while taken < len(lines) and fits(round_points((taken + 1) * leading), height):
        taken += 1
    return max(1, taken)


def reached(placements: list[Placement], marks: tuple[Mark, ...]) -> float:
    """Return the lowest edge a container's contents reach.

    That is the lowest of its marks, and of how far each xref in it
    reached.  An xref's mark alone does not say the second: its box is
    fixed, while an element inside it with a box of its own reaches as
    far as that box whether or not the element's mark does.  The reach
    already covers every mark inside the xref, so those are not walked
    a second time here.

    Args:
        placements: The container's elements, settled.
        marks: The marks they produced, in the same coordinates.

    """
    found = 0.0
    for mark in marks:
        found = max(found, round_points(mark.box.bottom))
    for placed in placements:
        if isinstance(placed.element, Xref):
            found = max(found, round_points(placed.top + placed.reach))
    return found


class Measurer:
    """Measures bands against one report's fonts and blobs.

    Attributes:
        fonts: The metrics of each `font` node, by name.
        blobs: The report's `data` nodes' contents, by name.
        file: The template, for a diagnostic.
        warnings: What the build has to say, in the order found.
        used: The fonts some measured element's style walk resolved to,
            which are the ones doc/printout.md#fonts lists.

    """

    def __init__(
        self,
        fonts: dict[str, Metrics],
        blobs: dict[str, str],
        file: str | None = None,
    ) -> None:
        """Hold what every band in one report is measured against.

        Args:
            fonts: The metrics of each `font` node, by name.
            blobs: The contents of the report's `data` nodes, by name.
            file: The template, for a diagnostic.

        """
        self.fonts = fonts
        self.blobs = blobs
        self.file = file
        self.warnings: list[BuildWarning] = []
        self.reported: set[tuple[str, str]] = set()
        self.used: set[str] = set()

    def prints(self, section: Section, frame: Window, context: Context) -> bool:
        """Report whether a band's ``printwhen`` lets it print.

        Separate from :meth:`band` because the answer
        is needed before the band is measured:
        doc/expressions.md#ordering-against-section-printing
        does not iterate a detail band's variables when the band
        is suppressed, and measuring happens after that fold.

        Args:
            section: The band.
            frame: The frame it is being tried against.
            context: The report as it stands.

        """
        return condition(
            section.printwhen,
            self.names(frame, context),
            section.path,
            context,
            "printwhen",
        )

    def names(self, frame: Window, context: Context) -> dict[str, Any]:
        """Return the environment a band's expressions are evaluated in.

        Args:
            frame: The frame the band is being tried against, which is
                what ``VERTICAL_POSITION`` and ``VERTICAL_SPACE`` describe.
            context: The report as it stands.

        """
        return context.environment(
            round_points(frame.fill - frame.top), frame.available
        )

    def band(
        self,
        section: Section,
        frame: Window,
        context: Context,
        styles: tuple[tuple[Style, ...], ...],
        printing: bool | None = None,
    ) -> Measurement | None:
        """Measure one band, or return ``None`` where it does not print.

        Args:
            section: The band.
            frame: The frame it is being tried against.
            context: The report as it stands.
            styles: The `style` nodes to walk, innermost scope first.
            printing: What :meth:`prints` already answered,
                for a caller that asked before folding a variable.

        Raises:
            BuildError: An expression would not evaluate.
            Unsupported: The band asks for work a later milestone brings.

        """
        names = self.names(frame, context)
        if not (
            printing
            if printing is not None
            else condition(section.printwhen, names, section.path, context, "printwhen")
        ):
            return None
        self.refuse_unsupported(section)
        outer = self.outer(
            tuple(style for scope in styles for style in scope), names, context
        )
        placements = self.placements(
            section.elements, frame.width, context, names, outer
        )
        first = self.arrange(placements, minimum=section.height or 0.0)
        marks = tuple(self.mark(placed) for placed in placements)
        height = max(first, reached(placements, marks))
        extents = tuple(
            extent(placed, mark) for placed, mark in zip(placements, marks, strict=True)
        )
        return Measurement(
            round_points(height), marks, extents, tuple(deferrals(placements))
        )

    def outer(
        self,
        walk: tuple[Style, ...],
        names: dict[str, Any],
        context: Context,
    ) -> Callable[[], Styling]:
        """Return the band's part of every element's style walk, resolved once.

        Everything outward of an element's own `style` nodes -- the band's,
        and the scopes around it -- is the same walk for every element in
        the band, evaluated against the same names, so it gives the same
        answer each time.  It is resolved the first time an element needs it
        and kept, rather than asked again per element; a band whose elements
        all set every property themselves never asks at all.

        Args:
            walk: The band's `style` nodes, innermost scope first.
            names: The environment a ``when`` is evaluated in.
            context: The report as it stands.

        """
        kept: list[Styling] = []

        def resolved() -> Styling:
            if not kept:
                kept.append(self.styling(walk, names, context))
            return kept[0]

        return resolved

    # -- the steps --------------------------------------------------------

    def placements(
        self,
        elements: tuple[Element | Xref, ...],
        width: float,
        context: Context,
        names: dict[str, Any],
        outer: Callable[[], Styling],
    ) -> list[Placement]:
        """Place every element that prints, as far as its container allows.

        A suppressed element contributes no marks and no height,
        so it neither shows nor pushes its followers down, and
        it is not here at all for the floating pass to find.

        Args:
            elements: The container's elements, in document order.
            width: The container's width, which each box resolves against.
            context: The report as it stands.
            names: The environment their expressions are evaluated in.
            outer: The band's part of the `style` walk.

        """
        return [
            placed
            for element in elements
            if (placed := self.element(element, width, context, names, outer))
        ]

    def arrange(
        self,
        placements: list[Placement],
        minimum: float = 0.0,
        fixed: float | None = None,
    ) -> float:
        """Settle every element's vertical extent, and return the first height.

        Floating comes before the first maximum and the container-dependent
        elements after it, which is the order of doc/layout.md#building-a-band:
        a floated element's new bottom edge is what the band's first height
        reaches, and a rule spanning the band spans the floats as well.
        Nothing here evaluates anything; that was done in document order
        when the elements were placed.

        Args:
            placements: The container's elements, sized where they can be.
            minimum: A band's declared ``height``, which is a minimum.
            fixed: An xref's height, which its children resolve against
                instead of a maximum they would take part in.

        """
        self.float_down(placements)
        if fixed is None:
            height = minimum
            for placed in placements:
                if not placed.dependent:
                    height = max(height, placed.bottom)
            height = round_points(height)
        else:
            height = fixed
        for placed in placements:
            if placed.dependent:
                self.size_dependent(placed, height)
        for placed in placements:
            if isinstance(placed.element, Xref):
                self.fill(placed)
        return height

    def element(
        self,
        element: Element | Xref,
        width: float,
        context: Context,
        names: dict[str, Any],
        outer: Callable[[], Styling],
    ) -> Placement | None:
        """Place one element, or return ``None`` where it does not print.

        Args:
            element: The node.
            width: The container's width, which its box resolves against.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            outer: The band's part of the `style` walk, which its own extends.

        """
        self.refuse_unsupported_element(element)
        placed = (
            self.xref(element, width, context, names, outer)
            if isinstance(element, Xref)
            else self.body(element, width, context, names, outer)
        )
        if placed is None:
            return None
        down = element.box.down
        placed.dependent = down.end_written or (
            down.size is None and placed.content_height is None
        )
        if not placed.dependent:
            self.size_own(placed)
        return placed

    def body(
        self,
        element: Element,
        width: float,
        context: Context,
        names: dict[str, Any],
        outer: Callable[[], Styling],
    ) -> Placement | None:
        """Place a body element across, and resolve its content.

        Args:
            element: The node.
            width: The container's width.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            outer: The band's part of the `style` walk.

        """
        if not condition(element.printwhen, names, element.path, context, "printwhen"):
            return None
        style = self.styling(element.styles, names, context, outer)
        if style.font is not None:
            self.used.add(style.font)
        x, extent = resolve_span(element.box.across, width)
        placed = Placement(element, style, x, extent)
        if isinstance(element, Field):
            self.wrap_content(placed, context, names)
        elif isinstance(element, Barcode):
            self.encode_content(placed, context, names)
        return placed

    def xref(
        self,
        xref: Xref,
        width: float,
        context: Context,
        names: dict[str, Any],
        outer: Callable[[], Styling],
    ) -> Placement:
        """Place an `xref` across, evaluate its link, and place its children.

        The children are placed here, at the xref's own place among the
        band's elements, so that their content is evaluated in document
        order.  Only their vertical extent waits, for :meth:`fill`,
        since they are laid out against the xref's height and that may be
        the band's.  Their `style` walk is the band's, as an xref has none.

        Args:
            xref: The node.
            width: The container's width.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            outer: The band's part of the `style` walk.

        Raises:
            BuildError: The target or the caption is not a string.

        """
        x, extent = resolve_span(xref.box.across, width)
        placed = Placement(xref, Styling(), x, extent)
        assert xref.target is not None
        placed.target = self.string(xref, "target", xref.target, names, context)
        if xref.caption is not None:
            placed.caption = self.string(xref, "caption", xref.caption, names, context)
        placed.children = self.placements(xref.elements, extent, context, names, outer)
        return placed

    def string(
        self,
        xref: Xref,
        prop: str,
        expression: Any,
        names: dict[str, Any],
        context: Context,
    ) -> str:
        """Return what one of an xref's expressions says, which must be text.

        Args:
            xref: The node.
            prop: ``target`` or ``caption``.
            expression: The compiled expression.
            names: The environment to evaluate it in.
            context: The report as it stands.

        Raises:
            BuildError: It would not evaluate, or it is not a string.

        """
        value = evaluate(expression, names, xref.path, context, prop)
        if not isinstance(value, str):
            raise BuildError(
                f"an xref's {prop} must be a string, not {type(value).__name__}",
                Location(
                    file=self.file,
                    path=xref.path,
                    prop=prop,
                    record=context.record_index,
                ),
            )
        return value

    def fill(self, placed: Placement) -> None:
        """Lay an xref's children out inside its box.

        They are arranged against the xref as a band's elements are
        arranged against the band, and their own ``halign`` and ``valign``
        are the only alignment they get: the xref's are not applied to them.

        Args:
            placed: The xref, with its box settled.

        """
        children = placed.children
        self.arrange(children, fixed=placed.height)
        placed.marks = tuple(self.mark(child) for child in children)
        placed.reach = max(
            placed.height,
            reached(children, placed.marks),
            *(child.bottom for child in children if not child.dependent),
        )

    def size_own(self, placed: Placement) -> None:
        """Give an element whose vertical extent is its own that extent.

        The declared box is kept beside it, since the floating pass
        orders elements by what was written rather than by what the data
        made of it.  That box is the declaration before any clamp, so
        a ``maxheight`` shortens the element but not the gap below it.
        An element with no declared height -- a stretch field given only
        a ``top`` -- has a declared box of no height at its top.
        A negative height never gets this far: validation refuses one.

        A ``maxheight`` clamps what a `barcode` declared and not its symbol,
        which is drawn at its size whatever the clamp: the box is then
        at least the symbol, as a box with no clamp is.

        Args:
            placed: The element being placed.

        """
        down = placed.element.box.down
        assert down.start is not None
        declared = down.size
        content = placed.content_height or 0.0
        if drawn_whole(placed.element):
            box = declared or 0.0
            if down.limit is not None:
                box = min(box, down.limit)
            size = max(0.0, box, content)
        else:
            size = max(0.0, declared or 0.0, content)
            if down.limit is not None:
                size = min(size, down.limit)
        placed.top = down.start
        placed.height = round_points(size)
        placed.declared_top = down.start
        placed.declared_bottom = round_points(down.start + (declared or 0.0))

    def size_dependent(self, placed: Placement, height: float) -> None:
        """Give a container-dependent element its extent.

        The extent is the container's, whatever the content: a stretch
        field that declared a ``bottom`` keeps the box the band gives it
        rather than growing, and its text runs past that box instead,
        placed by ``valign`` as any content taller than its box is.

        Args:
            placed: The element being placed.
            height: The height it resolves against: the band's first
                maximum, or an xref's own height.

        """
        top, size = resolve_span(placed.element.box.down, height)
        placed.top = top
        placed.height = round_points(size)

    def float_down(self, placements: list[Placement]) -> None:
        """Move every floating element below whatever lies above it.

        doc/layout.md#floating-elements, read against the declared boxes.
        Only elements whose vertical extent is their own take part,
        and the horizontal axis plays no part at all: an element at the
        far side of the band is above one at the near side all the same.

        * A non-floating element precedes a floating one when it is wholly
          above it: its declared bottom no lower than the other's top.
        * A floating element precedes another when it starts earlier,
          whether or not the two declared boxes overlap.
        * The gap is the declared distance from the floating element's top
          to the nearest element wholly above it, which is the one with
          the lowest declared bottom.  The nearest need not precede it:
          a floating element with no declared height that starts level
          with this one is wholly above it without starting earlier,
          and makes the gap nothing.  Where no element is wholly above,
          the gap is the distance to the band's top edge, or to the
          highest declared top among the elements taking part where one
          starts above that edge.

        The new top is then the lowest bottom edge among the predecessors,
        as they now stand, plus that gap, and an element with no predecessor
        stays where it was declared.  Floating elements are settled in the
        order they start, which is a topological order of that graph because
        a floating predecessor always starts earlier.  Ties keep document
        order, and cannot matter, since two that start together do not
        precede one another.

        Args:
            placements: The container's elements, own extents settled.

        """
        own = [placed for placed in placements if not placed.dependent]
        origin = min((placed.declared_top for placed in own), default=0.0)
        origin = min(origin, 0.0)
        floating = sorted(
            (placed for placed in own if placed.floating),
            key=lambda placed: placed.declared_top,
        )
        for placed in floating:
            before = [
                other
                for other in own
                if other is not placed
                and (
                    other.declared_top < placed.declared_top
                    if other.floating
                    else other.declared_bottom <= placed.declared_top
                )
            ]
            if not before:
                continue
            above = [
                other.declared_bottom
                for other in own
                if other is not placed and other.declared_bottom <= placed.declared_top
            ]
            gap = round_points(placed.declared_top - max(above, default=origin))
            edge = max(other.bottom for other in before)
            placed.top = round_points(edge + gap)

    # -- content ----------------------------------------------------------

    def wrap_content(
        self, placed: Placement, context: Context, names: dict[str, Any]
    ) -> None:
        """Resolve a `field`'s text and wrap it to the box's width.

        A deferred field is wrapped from its placeholder instead, and
        keeps what its ``expr`` reads where it sits.  The placeholder
        is measured and never drawn, so a character its font lacks raises
        no warning: the resolved value raises its own when it is set.

        Args:
            placed: The element being placed.
            context: The report as it stands.
            names: The environment its expression is evaluated in.

        """
        field = placed.element
        assert isinstance(field, Field)
        metrics = self.metrics(placed, context)
        if field.evaltime is None:
            text = self.content(field, context, names)
        else:
            assert field.expr is not None
            text = self.literal(field)
            placed.deferral = Deferral(
                field,
                scope_of(field.evaltime),
                snapshot(field.expr, names),
                context.record_index,
            )
        wrapped = wrap(text, placed.width, metrics)
        if placed.deferral is None:
            for character in wrapped.missing:
                self.missing(placed.style.font or "", character, field.path)
        placed.lines = tuple(wrapped.lines)
        if field.stretch:
            placed.content_height = round_points(len(wrapped.lines) * metrics.leading)

    def content(
        self, element: Field | Barcode, context: Context, names: dict[str, Any]
    ) -> str:
        """Return the string an element holds, where it is not deferred.

        ``format`` applies to what ``expr`` resolves to and to
        nothing else: doc/template.md#content-sources.
        An element with no ``expr`` holds its literal,
        and its ``format`` is never applied, so it cannot refuse it.

        Args:
            element: The node.
            context: The report as it stands.
            names: The environment its expression is evaluated in.

        Raises:
            BuildError: The expression would not evaluate,
                the format does not take its value,
                or the `data` node it names holds no text.

        """
        if element.expr is None:
            return self.literal(element)
        value = evaluate(element.expr, names, element.path, context, "expr")
        return self.formatted(element, value, context.record_index)

    def resolved(self, deferral: Deferral, final: Namespace) -> str:
        """Return the string a deferred element holds once its scope ends.

        Its ``expr``, called with its snapshot and ``FINAL``,
        with ``format`` applied, as `content` applies it.

        Args:
            deferral: The element, and what it read where it sat.
            final: The ``FINAL`` of the scope that ended.

        Raises:
            BuildError: The expression would not evaluate,
                or the format does not take its value.

        """
        element = deferral.element
        expr = element.expr
        assert expr is not None
        names = {**deferral.names, "FINAL": final}
        record = deferral.record
        path = element.path
        value = evaluate_at(expr, names, self.file, path, "expr", record)
        return self.formatted(element, value, record)

    def literal(self, element: Field | Barcode) -> str:
        """Return an element's ``text``, or its ``data`` node's content.

        Both are taken as written.  This is the whole content
        of an element with no ``expr``, and the placeholder of one
        that is deferred, which stands in for the formatted result
        rather than feeding ``format``.  A field with neither is
        empty text, one empty line; a barcode always has one,
        since validation requires it.

        Args:
            element: The node.

        Raises:
            BuildError: The `data` node it names holds no text.

        """
        if element.text is not None:
            return element.text
        if element.data is not None:
            return self.blob(element)
        return ""

    def blob(self, field: Field | Barcode) -> str:
        """Return the text of the `data` node an element names.

        Args:
            field: The node, which names one.

        Raises:
            BuildError: The node holds no text.

        """
        assert field.data is not None
        content = self.blobs.get(field.data)
        if content is None:
            raise BuildError(
                f"the data node {field.data!r} holds no text",
                Location(file=self.file, path=field.path, prop="data"),
            )
        return content

    def formatted(
        self,
        field: Field | Barcode,
        value: Any,
        record: int | None,
    ) -> str:
        """Return a value with the element's ``format`` applied.

        Args:
            field: The node.
            value: What its ``expr`` resolved to.
            record: The record it was built for, for a diagnostic.

        Raises:
            BuildError: The format does not take that value.

        """
        try:
            return apply_format(field.format, value)
        except Exception as refused:
            raise BuildError(
                str(refused),
                Location(file=self.file, path=field.path, prop="format", record=record),
            ) from None

    # -- barcodes ---------------------------------------------------------

    def encode_content(
        self, placed: Placement, context: Context, names: dict[str, Any]
    ) -> None:
        """Resolve a `barcode`'s value, encode it, and size its symbol.

        ``format`` applies to what ``expr`` resolves to and to nothing
        else: doc/template.md#barcode.  A deferred barcode is encoded
        from its placeholder instead, as written, and keeps what its
        ``expr`` reads where it sits.  The symbol at the declared module is
        the element's content height, whether or not ``grow`` will expand it.

        Args:
            placed: The element being placed.
            context: The report as it stands.
            names: The environment its expression is evaluated in.

        Raises:
            BuildError: The expression would not evaluate, the format
                does not take its value, or the type cannot encode the result.

        """
        barcode = placed.element
        assert isinstance(barcode, Barcode)
        record = context.record_index
        if barcode.evaltime is not None:
            assert barcode.expr is not None
            value = self.literal(barcode)
            placed.deferral = Deferral(
                barcode,
                scope_of(barcode.evaltime),
                snapshot(barcode.expr, names),
                record,
            )
        else:
            value = self.content(barcode, context, names)
        placed.value = value
        placed.symbol = self.encoded(barcode, value, record)
        size = symbol_size(placed.symbol, barcode.module, barcode.vertical)
        placed.content_height = size[1]

    def encoded(
        self,
        barcode: Barcode,
        value: str,
        record: int | None,
    ) -> Symbol:
        """Return the symbol a value encodes to, or refuse the value.

        Args:
            barcode: The node, for how to encode and for a diagnostic.
            value: The string to encode.
            record: The record it was built for, for a diagnostic.

        Raises:
            BuildError: The type cannot encode it.

        """
        try:
            return encode(barcode.kind, value, barcode.charset, barcode.eci)
        except Unencodable as refused:
            kind = barcode.kind
            raise BuildError(
                f"barcode {kind}: cannot encode {quote(value)}: {refused}",
                Location(file=self.file, path=barcode.path, record=record),
            ) from None

    def barcode_mark(self, placed: Placement, barcode: Barcode) -> BarcodeMark:
        """Return the mark a `barcode` produces.

        The symbol is drawn at its declared module unless ``grow``
        expands it to the box: a 2-D symbol's module becomes what fills
        the box's shorter side, and a 1-D symbol's bars reach across
        the box, never less than they would be without it.  Then it
        is placed in the box by ``halign`` and ``valign``, and a symbol
        larger than its box overhangs it on the side they do not name.

        Args:
            placed: The element, fully resolved.
            barcode: The node.

        """
        symbol = placed.symbol
        assert symbol is not None
        box = Box(placed.x, placed.top, placed.width, placed.height)
        return symbol_mark(barcode, placed.value, symbol, box, barcode.grow)

    def resolve_barcode(
        self, deferral: Deferral, final: Namespace, mark: BarcodeMark
    ) -> BarcodeMark:
        """Return a deferred barcode's mark, with its value encoded in it.

        doc/layout.md#re-measurement.  The value is set in the room
        the placeholder's symbol took, which is the placeholder's mark:
        its length along the coding direction must fit there, or the
        build fails.  A 1-D symbol's bars keep the room's extent across,
        and a 2-D symbol keeps its module, or with ``grow`` fills the room
        as the placeholder did.  The new symbol sits in the room by
        ``halign`` and ``valign``.

        Args:
            deferral: The element, and what it read where it sat.
            final: The ``FINAL`` of the scope that ended.
            mark: The placeholder's mark, where it is now.

        Raises:
            BuildError: The expression would not evaluate, the format does
                not take its value, the type cannot encode the result, or
                the symbol needs more room than the placeholder reserved.

        """
        barcode = deferral.element
        assert isinstance(barcode, Barcode)
        record = deferral.record
        value = self.resolved(deferral, final)
        symbol = self.encoded(barcode, value, record)
        width, height = symbol_size(symbol, barcode.module, barcode.vertical)
        room = mark.box
        needs = height if barcode.vertical else width
        reserved = room.height if barcode.vertical else room.width
        if not fits(needs, reserved):
            raise BuildError(
                f"the deferred value {quote(value)} needs {number(needs)} pt"
                f" and its placeholder{described(barcode)} reserved"
                f" {number(reserved)} pt; size the placeholder"
                " for the worst case",
                Location(file=self.file, path=barcode.path, record=record),
            )
        # A 1-D symbol's bars reach across the room as if it grew: the room
        # is the placeholder's symbol, and its bars are what they should be.
        grow = symbol.linear or barcode.grow
        return symbol_mark(barcode, value, symbol, room, grow)

    # -- deferred values --------------------------------------------------

    def resolve(
        self,
        deferral: Deferral,
        final: Namespace,
        mark: Mark,
    ) -> Mark:
        """Return a deferred element's mark, with its value set in it.

        Args:
            deferral: The element, and what it read where it sat.
            final: The ``FINAL`` of the scope that ended.
            mark: The placeholder's mark, where it is now.

        Raises:
            BuildError: The value could not be set.

        """
        if isinstance(deferral.element, Barcode):
            assert isinstance(mark, BarcodeMark)
            return self.resolve_barcode(deferral, final, mark)
        assert isinstance(mark, Text)
        return self.resolve_field(deferral, final, mark)

    def resolve_field(
        self,
        deferral: Deferral,
        final: Namespace,
        mark: Text,
    ) -> Text:
        """Return a deferred field's mark, with its value set in it.

        doc/layout.md#re-measurement.  The expression is called with its
        snapshot and ``FINAL``, formatted, and wrapped to the box's width,
        and the lines are set in the room the placeholder reserved, which
        is the placeholder's own mark: wherever the band was committed,
        split or balanced, that mark went with it.

        A field that does not stretch drops the lines beyond the room,
        as it drops lines beyond its box, and keeps one at least.
        One that stretches drops none, so a value that needs more room
        than the placeholder reserved is an error, whether or not a
        ``maxheight`` clamps the field.  What is left sits in the room
        by ``valign``, measured from the room's top edge.

        Args:
            deferral: The element, and what it read where it sat.
            final: The ``FINAL`` of the scope that ended.
            mark: The placeholder's mark, where it is now.

        Raises:
            BuildError: The expression would not evaluate,
                the format does not take its value, or the value needs
                more room than a stretch field's placeholder reserved.

        """
        field = deferral.element
        assert isinstance(field, Field)
        record = deferral.record
        text = self.resolved(deferral, final)
        metrics = self.fonts[mark.font]
        wrapped = wrap(text, mark.box.width, metrics)
        for character in wrapped.missing:
            self.missing(mark.font, character, field.path)
        lines = tuple(wrapped.lines)
        room = mark.box.height
        if not field.stretch:
            lines = lines[: fitting_lines(lines, room, metrics.leading)]
        height = round_points(len(lines) * metrics.leading)
        if field.stretch and not fits(height, room):
            raise BuildError(
                f"the deferred value {quote(text)} needs {number(height)} pt"
                f" and its placeholder{described(field)} reserved"
                f" {number(room)} pt; size the placeholder for the worst case",
                Location(file=self.file, path=field.path, record=record),
            )
        down = round_points(mark.box.y + VALIGN[field.valign] * (room - height))
        return replace(
            mark, box=Box(mark.box.x, down, mark.box.width, height), lines=lines
        )

    def metrics(self, placed: Placement, context: Context) -> Metrics:
        """Return the face and size a field is set in.

        Args:
            placed: The element being placed.
            context: The report as it stands, for a diagnostic.

        Raises:
            BuildError: No `style` in the walk named a font.

        """
        name = placed.style.font
        found = self.fonts.get(name) if name is not None else None
        if found is None:
            raise BuildError(
                "no style names a font for this element"
                if name is None
                else f"no font node is named {name!r}",
                Location(
                    file=self.file,
                    path=placed.element.path,
                    record=context.record_index,
                ),
            )
        return found

    def styling(
        self,
        walk: tuple[Style, ...],
        names: dict[str, Any],
        context: Context,
        outer: Callable[[], Styling] | None = None,
    ) -> Styling:
        """Return the formatting an outward walk of `style` nodes gives.

        doc/layout.md#building-a-band: every node whose ``when`` is true
        supplies whichever of its properties are still unset, so the first
        match wins each property separately.  A scope is not a unit of it.
        The next match may be a later node of the same scope as easily as
        one further out, which is why a layout's second style still fills
        in a ``bgcolor`` its first one left unset.

        Args:
            walk: The `style` nodes, innermost scope first and each scope
                in document order.
            names: The environment a ``when`` is evaluated in.
            context: The report as it stands.
            outer: The rest of the walk, already resolved,
                for whatever ``walk`` leaves unset.

        """
        font = color = bgcolor = None
        for style in walk:
            if font is not None and color is not None and bgcolor is not None:
                break
            if not condition(style.when, names, style.path, context, "when"):
                continue
            font = font if font is not None else style.font
            color = color if color is not None else style.color
            bgcolor = bgcolor if bgcolor is not None else style.bgcolor
        if outer is not None and None in (font, color, bgcolor):
            rest = outer()
            font = font if font is not None else rest.font
            color = color if color is not None else rest.color
            bgcolor = bgcolor if bgcolor is not None else rest.bgcolor
        return Styling(font, color, bgcolor)

    # -- marks ------------------------------------------------------------

    def mark(self, placed: Placement) -> Mark:
        """Return the mark an element produces.

        The box is the content box of doc/layout.md#building-a-band,
        not the declared one: ``halign`` and ``valign`` have placed
        the content inside the resolved box by the time a mark is made.
        A line and a rectangle have no content but their box, and an
        xref's content is its children, which carry their own alignment.

        Args:
            placed: The element, fully resolved.

        """
        element = placed.element
        box = Box(placed.x, placed.top, placed.width, placed.height)
        if isinstance(element, Xref):
            return XrefMark(
                box,
                element.kind,
                placed.target,
                placed.caption,
                tuple(one.moved(placed.x, placed.top) for one in placed.marks),
            )
        if isinstance(element, Field):
            return self.text_mark(placed, element)
        if isinstance(element, Barcode):
            return self.barcode_mark(placed, element)
        if isinstance(element, LineElement):
            return Line(
                box,
                element.stroke,
                element.dash,
                placed.style.color or DEFAULT_COLOR,
                element.backslant,
            )
        assert isinstance(element, RectangleElement)
        return Rectangle(
            box,
            element.stroke,
            element.dash,
            placed.style.color if element.outlined else None,
            placed.style.bgcolor if element.opaque else None,
            element.radius,
        )

    def text_mark(self, placed: Placement, field: Field) -> Text:
        """Return the mark a `field` produces.

        The box keeps the resolved width, so that ``align`` has a width
        to place each line in, and takes the height of the lines it holds,
        placed down the resolved box by ``valign``.

        Lines beyond the box are dropped at a line boundary unless the
        field stretches and nothing clamps it.  Such a field's box is
        at least its text wherever the box is its own, so this keeps
        every line of it; where the box is the container's, the text
        overflows rather than losing lines.  A ``maxheight`` is what
        says a stretched field may be cut, and then the box it cuts to
        is the box the field ended with.

        Args:
            placed: The element, fully resolved.
            field: The node, for its alignment and its font.

        """
        metrics = self.fonts[placed.style.font or ""]
        kept = len(placed.lines)
        if not field.stretch or field.box.down.limit is not None:
            kept = fitting_lines(placed.lines, placed.height, metrics.leading)
        lines = placed.lines[:kept]
        height = round_points(len(lines) * metrics.leading)
        offset = VALIGN[field.valign] * (placed.height - height)
        box = Box(placed.x, round_points(placed.top + offset), placed.width, height)
        return Text(
            box,
            placed.style.font or "",
            placed.style.color or DEFAULT_COLOR,
            field.align or field.halign,
            metrics.leading,
            lines,
        )

    # -- diagnostics ------------------------------------------------------

    def missing(self, font: str, character: str, path: NodePath) -> None:
        """Record that a face has no glyph for a character, once.

        One warning per font and character however many marks are
        set in it: the warning is about the face, and repeating it
        per row would bury the document's other diagnostics.

        Args:
            font: The name the template gave the `font` node.
            character: The character with no glyph.
            path: The node being set.

        """
        key = (font, character)
        if key in self.reported:
            return
        self.reported.add(key)
        self.warnings.append(missing_glyph(font, character, str(path)))

    def refuse_unsupported(self, section: Section) -> None:
        """Refuse a band that asks for work a later milestone brings.

        Args:
            section: The band.

        Raises:
            Unsupported: It carries one of them.

        """
        where = Location(file=self.file, path=section.path)
        if section.outlines:
            raise Unsupported("an outline entry arrives in M13", where)
        if section.subreports:
            raise Unsupported("a subreport arrives in M12", where)

    def refuse_unsupported_element(self, element: Element | Xref) -> None:
        """Refuse an element of a kind a later milestone brings.

        Args:
            element: The node.

        Raises:
            Unsupported: It is one of them.

        """
        where = Location(file=self.file, path=element.path)
        if isinstance(element, Xref):
            if element.kind == "outline":
                raise Unsupported(
                    "an xref to an outline entry needs the outline, which"
                    " arrives in M13",
                    Location(file=self.file, path=element.path, prop="type"),
                )
            return
        if isinstance(element, DRAWN):
            return
        kind = type(element).__name__
        milestone = ELEMENT_MILESTONE.get(kind, "a later milestone")
        raise Unsupported(f"a {kind.lower()} element arrives in {milestone}", where)


def deferrals(
    placements: list[Placement], within: tuple[int, ...] = ()
) -> Iterator[Deferral]:
    """Yield the deferred elements among these, in document order.

    Each carries the path of its mark: its index among the container's
    marks, after the path of an `xref` that holds it.  A container's marks
    are its placements', one for one, so the indices are the same.

    Args:
        placements: A container's elements, settled.
        within: The path of the container's own mark, for an xref's.

    """
    for index, placed in enumerate(placements):
        path = (*within, index)
        if placed.deferral is not None:
            yield placed.deferral.at(path)
        if isinstance(placed.element, Xref):
            yield from deferrals(placed.children, path)


def described(field: Field | Barcode) -> str:
    """Return how an error names a deferred element's placeholder.

    Only a field that stretches and a barcode can outgrow their placeholders,
    and validation refuses either without ``text`` or ``data``.

    Args:
        field: The node.

    """
    if field.text is not None:
        return f" {quote(field.text)}"
    assert field.data is not None
    return f", the data node {quote(field.data)},"


def extent(placed: Placement, mark: Mark) -> Extent:
    """Return how far down an element's marks reach.

    Args:
        placed: The element, settled.
        mark: The mark it produced.

    """
    box = mark.box
    if isinstance(mark, XrefMark):
        tops = [box.y, *(one.box.y for one in mark.marks)]
        return Extent(
            round_points(min(tops)),
            round_points(max(box.bottom, placed.top + placed.reach)),
        )
    # A deferred field's lines are its placeholder's, replaced when its
    # scope ends, so a cut between them would divide lines that are not the
    # ones printed.  It blocks a cut as an element that cannot split does.
    if (
        isinstance(placed.element, Field)
        and placed.element.stretch
        and placed.deferral is None
        and isinstance(mark, Text)
    ):
        return Extent(box.y, round_points(box.bottom), len(mark.lines), mark.leading)
    return Extent(box.y, round_points(box.bottom))


def drawn_whole(element: Element | Xref) -> bool:
    """Report whether an element's content is drawn whole, whatever its box.

    A clamp cannot cut such content, so it clamps the box the element
    declared and the box then grows to the content: doc/template.md,
    under maxwidth and maxheight.  A barcode's symbol is drawn whole,
    and so is an image with ``scale="grow"``, which M11 adds here.

    Args:
        element: The element.

    """
    return isinstance(element, Barcode)


def symbol_size(
    symbol: Symbol,
    module: float,
    vertical: bool,
) -> tuple[float, float]:
    """Return how wide and how tall a symbol is drawn at a module.

    A 1-D symbol's bars reach across the coding direction for 15%
    of its length, or a quarter of an inch where that is more.
    A 2-D symbol is a module per row as well as per column.

    Args:
        symbol: The symbol, quiet zone included.
        module: The narrow element's width in points.
        vertical: Whether the coding direction runs down the page.

    """
    along = round_points(module * symbol.length)
    if symbol.linear:
        across = max(round_points(along * BAR_SHARE), BAR_MINIMUM)
    else:
        across = round_points(module * symbol.depth)
    return (across, along) if vertical else (along, across)


def symbol_mark(
    barcode: Barcode, value: str, symbol: Symbol, box: Box, grow: bool
) -> BarcodeMark:
    """Return a symbol's mark, sized for a box and placed in it.

    Args:
        barcode: The node, for its module, direction, alignment, and colours.
        value: The string the symbol encodes.
        symbol: The symbol.
        box: The box it is drawn in.
        grow: Whether it expands to what the box offers.

    """
    module = barcode.module
    vertical = barcode.vertical
    if grow and not symbol.linear:
        wide, tall = (
            (symbol.depth, symbol.length)
            if vertical
            else (
                symbol.length,
                symbol.depth,
            )
        )
        fill = min(fill_points(box.width, wide), fill_points(box.height, tall))
        module = max(module, fill)
    width, height = symbol_size(symbol, module, vertical)
    if grow and symbol.linear:
        if vertical:
            width = max(width, box.width)
        else:
            height = max(height, box.height)
    x = round_points(box.x + HALIGN[barcode.halign] * (box.width - width))
    y = round_points(box.y + VALIGN[barcode.valign] * (box.height - height))
    return BarcodeMark(
        Box(x, y, width, height),
        barcode.kind,
        value,
        module,
        vertical,
        barcode.ink,
        barcode.paper,
        symbol.stripes() if symbol.linear else (),
        () if symbol.linear else symbol.rows(),
    )
