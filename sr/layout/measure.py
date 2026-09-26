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

Stage 3 cannot feed back into stage 2, and that is not a simplification:
resolving the rule against the final height instead would make the two
define each other.  A band whose only content is an overflowing field
therefore grows to the text while a rule inside it keeps the height
the declared boxes gave.

An `xref` is the one container inside a band.  Its box comes from its
own geometry and nothing else, it never grows to what it holds, and its
children are laid out against it the way a band's elements are laid out
against the band, except that the height they resolve against is the
xref's rather than a maximum they take part in.  Their marks still reach
the band's second maximum, so a stretch field inside an xref that does
not hold it pushes the next band down all the same.

"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sr.errors import BuildError, BuildWarning, Location, NodePath, Unsupported
from sr.expr import apply_format
from sr.fonts.text import Metrics, missing_glyph, wrap
from sr.layout.context import Context, condition, evaluate
from sr.layout.frame import Frame
from sr.printout.model import Box, Line, Mark, Rectangle, Text
from sr.printout.model import Xref as XrefMark
from sr.template.model import (
    Element,
    Field,
    Section,
    Span,
    Style,
    Xref,
)
from sr.template.model import Line as LineElement
from sr.template.model import Rectangle as RectangleElement
from sr.units import fits, round_points

__all__ = ["Measurement", "Measurer", "Styling", "lowest", "resolve_span"]

# The colour a mark is drawn in when no `style` in the walk set one.
# doc/template.md leaves a text mark's `color` required and a
# rectangle's `stroke` optional, and the two differ here for that
# reason: text falls back to black, and an outline nothing gave a
# colour to is simply not drawn.
DEFAULT_COLOR = "#000000"

# How far down a box its content starts, per `valign`.
VALIGN = {"top": 0.0, "center": 0.5, "bottom": 1.0}


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
class Measurement:
    """A band, measured but not committed.

    Attributes:
        height: What the band takes from the frame.
        marks: Its marks, at band-relative coordinates.

    """

    height: float
    marks: tuple[Mark, ...] = ()


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
        marks: An `xref`'s children's marks, relative to its box.
        reach: How far down an `xref`'s contents reach, relative to
            its top, which is what it gives the band's second maximum.

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
    marks: tuple[Mark, ...] = ()
    reach: float = 0.0

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


def lowest(marks: Iterable[Mark]) -> float | None:
    """Return the lowest bottom edge among some marks, looking inside xrefs.

    An xref's box is a hit region and never grows to what it holds,
    so what it holds is looked at separately: a field inside it that
    runs past its box still takes room from the band.

    Args:
        marks: The marks, in any coordinates.

    """
    found: float | None = None
    for mark in marks:
        edge = round_points(mark.box.bottom)
        if isinstance(mark, XrefMark):
            inner = lowest(mark.marks)
            if inner is not None:
                edge = max(edge, inner)
        found = edge if found is None else max(found, edge)
    return found


def reached(placements: list[Placement], marks: tuple[Mark, ...]) -> float:
    """Return the lowest edge a container's contents reach.

    That is the lowest of its marks, and of how far each xref in it
    reached, which an xref's mark alone does not say: its box is fixed,
    while an element inside it with a box of its own reaches as far as
    that box whether or not the element's mark does.

    Args:
        placements: The container's elements, settled.
        marks: The marks they produced, in the same coordinates.

    """
    found = lowest(marks) or 0.0
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

    def prints(self, section: Section, frame: Frame, context: Context) -> bool:
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

    def names(self, frame: Frame, context: Context) -> dict[str, Any]:
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
        frame: Frame,
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
        walk = tuple(style for scope in styles for style in scope)
        placements = self.placements(
            section.elements, frame.width, context, names, walk
        )
        first = self.arrange(
            placements, context, names, walk, minimum=section.height or 0.0
        )
        marks = tuple(self.mark(placed) for placed in placements)
        height = max(first, reached(placements, marks))
        return Measurement(round_points(height), marks)

    # -- the steps --------------------------------------------------------

    def placements(
        self,
        elements: tuple[Element | Xref, ...],
        width: float,
        context: Context,
        names: dict[str, Any],
        walk: tuple[Style, ...],
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
            walk: The container's `style` nodes, innermost first.

        """
        return [
            placed
            for element in elements
            if (placed := self.element(element, width, context, names, walk))
        ]

    def arrange(
        self,
        placements: list[Placement],
        context: Context,
        names: dict[str, Any],
        walk: tuple[Style, ...],
        minimum: float = 0.0,
        fixed: float | None = None,
    ) -> float:
        """Settle every element's vertical extent, and return the first height.

        Floating comes before the first maximum and the container-dependent
        elements after it, which is the order of doc/layout.md#building-a-band:
        a floated element's new bottom edge is what the band's first height
        reaches, and a rule spanning the band spans the floats as well.

        Args:
            placements: The container's elements, sized where they can be.
            context: The report as it stands.
            names: The environment their expressions are evaluated in.
            walk: The container's `style` nodes, innermost first.
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
                self.fill(placed, context, names, walk)
        return height

    def element(
        self,
        element: Element | Xref,
        width: float,
        context: Context,
        names: dict[str, Any],
        walk: tuple[Style, ...],
    ) -> Placement | None:
        """Place one element, or return ``None`` where it does not print.

        Args:
            element: The node.
            width: The container's width, which its box resolves against.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            walk: The container's `style` walk, which its own extends.

        """
        self.refuse_unsupported_element(element)
        placed = (
            self.xref(element, width, context, names)
            if isinstance(element, Xref)
            else self.body(element, width, context, names, walk)
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
        walk: tuple[Style, ...],
    ) -> Placement | None:
        """Place a body element across, and resolve its content.

        Args:
            element: The node.
            width: The container's width.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            walk: The container's `style` walk.

        """
        if not condition(element.printwhen, names, element.path, context, "printwhen"):
            return None
        style = self.styling((*element.styles, *walk), names, context)
        if style.font is not None:
            self.used.add(style.font)
        x, extent = resolve_span(element.box.across, width)
        placed = Placement(element, style, x, extent)
        if isinstance(element, Field):
            self.wrap_content(placed, context, names)
        return placed

    def xref(
        self,
        xref: Xref,
        width: float,
        context: Context,
        names: dict[str, Any],
    ) -> Placement:
        """Place an `xref` across, and evaluate where it links to.

        Its children wait for :meth:`fill`, since they are laid out
        against its height and that may come from the band's.

        Args:
            xref: The node.
            width: The container's width.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.

        Raises:
            BuildError: The target or the caption is not a string.

        """
        x, extent = resolve_span(xref.box.across, width)
        placed = Placement(xref, Styling(), x, extent)
        assert xref.target is not None
        placed.target = self.string(xref, "target", xref.target, names, context)
        if xref.caption is not None:
            placed.caption = self.string(xref, "caption", xref.caption, names, context)
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

    def fill(
        self,
        placed: Placement,
        context: Context,
        names: dict[str, Any],
        walk: tuple[Style, ...],
    ) -> None:
        """Lay an xref's children out inside its box.

        They are placed against the xref as a band's elements are placed
        against the band, and their own ``halign`` and ``valign`` are
        the only alignment they get: the xref's are not applied to them.
        Their `style` walk is the band's, since an xref has none.

        Args:
            placed: The xref, with its box settled.
            context: The report as it stands.
            names: The environment its children are evaluated in.
            walk: The band's `style` walk.

        """
        xref = placed.element
        assert isinstance(xref, Xref)
        children = self.placements(xref.elements, placed.width, context, names, walk)
        self.arrange(children, context, names, walk, fixed=placed.height)
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

        Args:
            placed: The element being placed.

        """
        down = placed.element.box.down
        assert down.start is not None
        declared = down.size
        size = max(0.0, declared) if declared is not None else 0.0
        if down.limit is not None:
            size = min(size, down.limit)
        if placed.content_height is not None:
            size = max(size, placed.content_height)
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

        Args:
            placed: The element being placed.
            context: The report as it stands.
            names: The environment its expression is evaluated in.

        """
        field = placed.element
        assert isinstance(field, Field)
        metrics = self.metrics(placed, context)
        text = self.content(field, context, names)
        wrapped = wrap(text, placed.width, metrics)
        for character in wrapped.missing:
            self.missing(placed.style.font or "", character, field.path)
        placed.lines = tuple(wrapped.lines)
        if field.stretch:
            placed.content_height = round_points(len(wrapped.lines) * metrics.leading)

    def content(self, field: Field, context: Context, names: dict[str, Any]) -> str:
        """Return the string a `field` finally holds.

        doc/layout.md#line-breaking wraps the string the element holds
        once ``expr``, ``text`` or ``data`` has been resolved and
        ``format`` applied.  Where a character came from makes no
        difference to how it is treated.

        Args:
            field: The node.
            context: The report as it stands.
            names: The environment its expression is evaluated in.

        Raises:
            BuildError: The expression would not evaluate,
                or the `data` node it names holds no text.

        """
        if field.evaltime is not None:
            raise Unsupported(
                "a field with evaltime is deferred, which arrives in M9",
                Location(file=self.file, path=field.path, prop="evaltime"),
            )
        if field.expr is not None:
            value = evaluate(field.expr, names, field.path, context, "expr")
            return self.formatted(field, value, context)
        if field.text is not None:
            return self.formatted(field, field.text, context)
        if field.data is not None:
            content = self.blobs.get(field.data)
            if content is None:
                raise BuildError(
                    f"the data node {field.data!r} holds no text",
                    Location(file=self.file, path=field.path, prop="data"),
                )
            return self.formatted(field, content, context)
        return ""

    def formatted(self, field: Field, value: Any, context: Context) -> str:
        """Return a value with the field's ``format`` applied.

        Args:
            field: The node.
            value: What its content resolved to.
            context: The report as it stands, for a diagnostic.

        Raises:
            BuildError: The format does not take that value.

        """
        try:
            return apply_format(field.format, value)
        except Exception as refused:
            raise BuildError(
                str(refused),
                Location(
                    file=self.file,
                    path=field.path,
                    prop="format",
                    record=context.record_index,
                ),
            ) from None

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
        if section.ejects:
            raise Unsupported(
                "an eject node needs pagination, which arrives in M8", where
            )
        if section.outlines:
            raise Unsupported("an outline entry arrives in M13", where)
        if section.subreports:
            raise Unsupported("a subreport arrives in M12", where)
        if section.swapheader or section.swapfooter:
            raise Unsupported(
                "swapheader and swapfooter need pagination, which arrives in M8",
                where,
            )

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
        if isinstance(element, Field | LineElement | RectangleElement):
            return
        kind = type(element).__name__
        milestone = ELEMENT_MILESTONE.get(kind, "a later milestone")
        raise Unsupported(f"a {kind.lower()} element arrives in {milestone}", where)


# Which milestone brings each element kind this one does not draw.
ELEMENT_MILESTONE = {"Barcode": "M10", "Image": "M11"}
