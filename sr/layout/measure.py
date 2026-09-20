"""Measuring a band: content in, marks and a height out.

doc/layout.md#measure-decide-commit makes this the pure step.
It resolves styles, evaluates expressions, wraps text and computes
the band's height and its marks **at band-relative coordinates**,
and it mutates nothing: no variable folds, no counters, no output.
Translating those marks onto a page is the caller's business, which is
what makes splitting and splicing translations of already-built marks
rather than re-measurements.

The band's height is the part worth reading twice, because
doc/layout.md#building-a-band settles it in two stages and
the difference is visible in every band that holds a rule.

1. The elements whose vertical extent is **their own** give a first
   height: the greater of the band's declared minimum and the lowest
   bottom edge among them.
2. The **container-dependent** ones -- anchored to the band's bottom
   edge, so their extent is that height minus their top -- are resolved
   against it.  A rule written ``top=10`` and nothing else ends up
   exactly there.
3. The band is then as tall as the lowest bottom edge of the marks
   it produced, which is the first height unless a field's text
   overflowed the box it was given.

Stage 3 cannot feed back into stage 2, and that is not a simplification:
resolving the rule against the final height instead would make the two
define each other.  A band whose only content is an overflowing field
therefore grows to the text while a rule inside it keeps the height
the declared boxes gave.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sr.errors import BuildError, BuildWarning, Location, NodePath, Unsupported
from sr.expr import apply_format
from sr.fonts.text import Metrics, missing_glyph, wrap
from sr.layout.context import Context, condition, evaluate
from sr.layout.frame import Frame
from sr.printout.model import Box, Line, Mark, Rectangle, Text
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

__all__ = ["Measurement", "Measurer", "Styling", "resolve_span"]

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
        x: The left edge, band-relative.
        width: The extent across.
        top: The top edge, once it is known.
        height: The extent down, once it is known.
        content: The wrapped lines of a `field`.
        content_height: What those lines measure.
        dependent: Whether its extent comes from the band's height.

    """

    element: Element
    style: Styling
    x: float
    width: float
    top: float = 0.0
    height: float = 0.0
    content: tuple[str, ...] = ()
    content_height: float = 0.0
    dependent: bool = False


def resolve_span(span: Span, container: float) -> tuple[float, float]:
    """Return one axis as a start and an extent.

    Exactly two of ``start``, ``end`` and ``size`` are set once
    the loader has filled the axis in, so the third follows from
    the container.  ``limit`` clamps the extent afterwards and
    keeps whichever edge the declaration anchored: a box given
    a ``bottom`` stays against the container's far edge.

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


class Measurer:
    """Measures bands against one report's fonts and blobs.

    Attributes:
        fonts: The metrics of each `font` node, by name.
        blobs: The report's `data` nodes' contents, by name.
        file: The template, for a diagnostic.
        warnings: What the build has to say, in the order found.

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
            styles: The `style` nodes to walk, innermost first.
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
        placements = [
            placed
            for element in section.elements
            if (placed := self.element(element, frame.width, context, names, styles))
        ]
        first = self.settle(section, placements)
        marks = tuple(self.mark(placed) for placed in placements)
        height = first
        for mark in marks:
            height = max(height, mark.box.bottom)
        return Measurement(round_points(height), marks)

    def settle(self, section: Section, placements: list[Placement]) -> float:
        """Return the height the container-dependent elements resolve against.

        Args:
            section: The band, for its declared minimum.
            placements: Its elements, the independent ones already sized.

        """
        height = section.height or 0.0
        for placed in placements:
            if not placed.dependent:
                height = max(height, round_points(placed.top + placed.height))
        height = round_points(height)
        for placed in placements:
            if placed.dependent:
                self.size_down(placed, height)
        return height

    def element(
        self,
        element: Element | Xref,
        width: float,
        context: Context,
        names: dict[str, Any],
        styles: tuple[tuple[Style, ...], ...],
    ) -> Placement | None:
        """Place one element, or return ``None`` where it does not print.

        A suppressed element contributes no marks and no height,
        so it neither shows nor pushes its followers down.

        Args:
            element: The node.
            width: The band's width, which its box resolves against.
            context: The report as it stands.
            names: The environment its expressions are evaluated in.
            styles: The band's `style` walk, which its own extends.

        """
        self.refuse_unsupported_element(element)
        assert isinstance(element, Element)
        if not condition(element.printwhen, names, element.path, context, "printwhen"):
            return None
        style = self.styling((element.styles, *styles), names, context)
        across = resolve_span(element.box.across, width)
        placed = Placement(element, style, across[0], across[1])
        if isinstance(element, Field):
            self.wrap_content(placed, context, names)
        down = element.box.down
        placed.dependent = down.size is None or down.start is None
        if not placed.dependent:
            self.size_down(placed, 0.0)
        return placed

    def size_down(self, placed: Placement, height: float) -> None:
        """Give an element its vertical extent.

        Called twice over a band: once for the elements whose extent
        is their own, and once for the rest, against the height
        those produced.

        Args:
            placed: The element being placed.
            height: The band's height so far, which a container-dependent
                box is measured against.

        """
        top, size = resolve_span(placed.element.box.down, height)
        element = placed.element
        if isinstance(element, Field) and element.stretch:
            size = max(size, placed.content_height)
            limit = element.box.down.limit
            if limit is not None:
                size = min(size, limit)
        placed.top = top
        placed.height = round_points(size)

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
        placed.content = tuple(wrapped.lines)
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
        walk: tuple[tuple[Style, ...], ...],
        names: dict[str, Any],
        context: Context,
    ) -> Styling:
        """Return the formatting an outward walk of `style` nodes gives.

        doc/template.md#ordering-rules: within each scope the first node
        whose ``when`` is true supplies the formatting, and unset
        properties fall through to the next match in the same walk,
        so a band-level style setting only ``bgcolor`` still inherits
        a ``font`` from `layout`.

        Args:
            walk: The scopes to consult, innermost first.
            names: The environment a ``when`` is evaluated in.
            context: The report as it stands.

        """
        font = color = bgcolor = None
        for scope in walk:
            for style in scope:
                if not condition(style.when, names, style.path, context, "when"):
                    continue
                font = font if font is not None else style.font
                color = color if color is not None else style.color
                bgcolor = bgcolor if bgcolor is not None else style.bgcolor
                break
        return Styling(font, color, bgcolor)

    def mark(self, placed: Placement) -> Mark:
        """Return the mark an element produces.

        The box is the content box of doc/layout.md#building-a-band,
        not the declared one: ``halign`` and ``valign`` have placed
        the content inside the resolved box by the time a mark is made.

        Args:
            placed: The element, fully resolved.

        Raises:
            Unsupported: The element is of a kind a later milestone brings.

        """
        element = placed.element
        if isinstance(element, Field):
            return self.text_mark(placed, element)
        box = Box(placed.x, placed.top, placed.width, placed.height)
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

        Args:
            placed: The element, fully resolved.
            field: The node, for its alignment and its font.

        """
        metrics = self.fonts[placed.style.font or ""]
        lines = placed.content[
            : fitting_lines(placed.content, placed.height, metrics.leading)
        ]
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
        if isinstance(element, Field | LineElement | RectangleElement):
            if element.floating:
                raise Unsupported("a floating element arrives in M7", where)
            return
        kind = type(element).__name__
        milestone = ELEMENT_MILESTONE.get(kind, "a later milestone")
        raise Unsupported(f"a {kind.lower()} element arrives in {milestone}", where)


# Which milestone brings each element kind this one does not draw.
ELEMENT_MILESTONE = {"Barcode": "M10", "Image": "M11", "Xref": "M13"}
