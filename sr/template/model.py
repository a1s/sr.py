"""One dataclass per node, and the value types the nodes are made of.

doc/template.md is the document model; this is that model in Python.
Nothing here reads a KDL document (``load`` does that), and nothing
here checks a rule (``validate`` does that).  What is here is the shape
a template has once it has been read, together with the three tables
that are properties of the format rather than of any one node:
the enumerations, the page sizes, and how a parameter value
spelled as text becomes a value.

Two rules of doc/template.md#ordering-rules are settled here rather than
in layout, because they are properties of the tree rather than of a page:

* **Paint order is document order**, so ``Section.elements`` holds the
  body elements and the ``xref`` nodes in the order they were written,
  and a renderer draws them in that order.
* **A subreport is ordered by ``seq``**, not by where it sits,
  so ``Section.subreports`` is sorted by ``seq`` with ties broken
  on document order.  Negative runs before the section's own content
  and non-negative after it, which the sign of ``seq`` still says.

The first-win rules (``style``, ``eject`` and ``outline``) need no sorting
at all: the tuples are in document order and the first match wins.

Geometry is half-resolved here.  The rule in
doc/template.md#position-and-size-any-two-of-three fills the missing
values in a fixed order, and that order depends only on which of the
three were written, so :func:`span` applies it at load and a
:class:`Span` always has exactly two of its three parts.  Turning those
two into a position needs the container, which is layout's business.

"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import InvalidOperation
from pathlib import Path
from typing import Any, Final

from sr.errors import BadValue, NodePath, SrError
from sr.expr import Decimal, Expression, FrozenList, Record
from sr.expr.builtins import parse_time
from sr.expr.golayout import RFC3339
from sr.expr.values import parse_decimal
from sr.units import POINTS_PER_UNIT, parse_number, round_points

__all__ = [
    "ALIGNS",
    "BARCODE_TYPES",
    "CALCS",
    "COMPRESSIONS",
    "DASHES",
    "EJECT_TYPES",
    "ENCODINGS",
    "EVALTIME_SCOPES",
    "HALIGNS",
    "IMAGE_SCALES",
    "IMAGE_TYPES",
    "PAGE_SIZES",
    "RFC3339_DATE",
    "SCOPES",
    "SECTIONS",
    "VALIGNS",
    "VALUE_TYPES",
    "XREF_TYPES",
    "Arg",
    "Barcode",
    "Blob",
    "Box",
    "Columns",
    "Eject",
    "Element",
    "Embedded",
    "Field",
    "Font",
    "Group",
    "Image",
    "Layout",
    "Line",
    "Member",
    "Nesting",
    "Outline",
    "Paper",
    "Parameter",
    "Records",
    "Rectangle",
    "Report",
    "Section",
    "Span",
    "Style",
    "Subreport",
    "Xref",
    "freeze",
    "page_size",
    "parse_text",
    "span",
]

# The enumerations of doc/template.md#enumerations, each in the order
# the specification lists it, because that order is what a diagnostic
# naming the alternatives reads back.
ALIGNS: Final = ("left", "center", "right", "justified")
HALIGNS: Final = ("left", "center", "right")
VALIGNS: Final = ("top", "center", "bottom")
CALCS: Final = (
    "count",
    "list",
    "set",
    "chain",
    "first",
    "last",
    "sum",
    "avg",
    "min",
    "max",
    "std",
    "var",
)
SCOPES: Final = ("report", "page", "column", "group", "detail", "item")
EJECT_TYPES: Final = ("page", "column")
BARCODE_TYPES: Final = (
    "Code128",
    "Code39",
    "Code93",
    "2of5i",
    "DataMatrix",
    "Aztec",
    "QR-L",
    "QR-M",
    "QR-Q",
    "QR-H",
)
IMAGE_SCALES: Final = ("cut", "fill", "grow")
IMAGE_TYPES: Final = ("png", "jpeg", "gif")
XREF_TYPES: Final = ("outline", "url")
DASHES: Final = ("solid", "dot", "dash", "dashdot")
ENCODINGS: Final = ("base64",)
COMPRESSIONS: Final = ("zlib", "gzip")
VALUE_TYPES: Final = (
    "string",
    "int",
    "decimal",
    "float",
    "bool",
    "datetime",
    "date",
    "object",
    "list",
)

# The five band names, in the order doc/template.md lists them.
SECTIONS: Final = ("title", "summary", "header", "footer", "detail")

# The two `evaltime` scopes that are not a group name.  A group name
# is the third spelling, and which names are groups is not known here.
EVALTIME_SCOPES: Final = ("report", "page", "column")

# What doc/template.md#parameter-values-as-text takes for a `decimal`:
# a sign, digits, and an optional fractional part.  None of the spellings
# a host decimal reader adds on its own.  No exponent, either; exponents
# are for floats only, and floats are NOT decimals.
DECIMAL: Final = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")

# What doc/template.md#parameter-values-as-text calls "RFC 3339 date".
# RFC3339 itself is the timestamp; this is its date half, in Go spelling.
RFC3339_DATE: Final = "2006-01-02"

# The page sizes of doc/template.md#enumerations, portrait, each as
# the dimension its standard states rather than as points: the ISO series
# in millimetres and the North American ones in inches, converted by the
# same rule a `width="210mm"` goes through.  `landscape=#true` swaps them.
PAGE_SIZES: Final[dict[str, tuple[float, float, str]]] = {
    # ISO 216
    "A1": (594, 841, "mm"),
    "A2": (420, 594, "mm"),
    "A3": (297, 420, "mm"),
    "A4": (210, 297, "mm"),
    "A5": (148, 210, "mm"),
    "A6": (105, 148, "mm"),
    "B3": (353, 500, "mm"),
    "B4": (250, 353, "mm"),
    "B5": (176, 250, "mm"),
    "B6": (125, 176, "mm"),
    # North American
    "Letter": (8.5, 11, "in"),
    "Legal": (8.5, 14, "in"),
    "Ledger": (11, 17, "in"),
    "Executive": (7.25, 10.5, "in"),
    "Statement": (5.5, 8.5, "in"),
    "Quatro": (8, 10, "in"),
    "Royal": (20, 25, "in"),
    # ISO 7810 ID-1, whose 53.975 x 85.598 mm is exactly 2.125 x 3.37 in.
    "BusinessCard": (2.125, 3.37, "in"),
    # ISO 269
    "EnvelopeC3": (324, 458, "mm"),
    "EnvelopeC4": (229, 324, "mm"),
    "EnvelopeC5": (162, 229, "mm"),
    "EnvelopeC6": (114, 162, "mm"),
    "EnvelopeDL": (110, 220, "mm"),
    "EnvelopeB4": (250, 353, "mm"),
    "EnvelopeB5": (176, 250, "mm"),
    # North American envelopes
    "Envelope#10": (4.125, 9.5, "in"),
    "EnvelopeA2": (4.375, 5.75, "in"),
    "EnvelopeA6": (4.75, 6.5, "in"),
    "EnvelopeA7": (5.25, 7.25, "in"),
}


def page_size(name: str) -> tuple[float, float]:
    """Return a named page size in points, portrait.

    Args:
        name: One of :data:`PAGE_SIZES`.

    Raises:
        KeyError: The name is not a page size.

    """
    width, height, unit = PAGE_SIZES[name]
    factor = POINTS_PER_UNIT[unit]
    return round_points(width * factor), round_points(height * factor)


# -- geometry ---------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """One axis of a box: two of three, and a clamp that is not part of it.

    ``start`` is ``left`` or ``top``, ``end`` is ``right`` or ``bottom``
    measured **inward** from the container's far edge, and ``size``
    is ``width`` or ``height``.  Exactly two of the three are set once
    :func:`span` has filled the rest in, so the third follows from the
    container and nothing downstream has to remember the fill order.

    Filling a value in loses whether it was written, and one rule needs
    that back: doc/layout.md#building-a-band makes an element that
    *declared* a ``bottom`` container-dependent whatever its content,
    while one whose ``bottom`` was filled in is container-dependent only
    when it has no height of its own.  ``end_written`` keeps the answer.

    Attributes:
        start: Offset from the container's near edge.
        end: Offset inward from the container's far edge.
        size: The extent.
        limit: ``maxwidth`` or ``maxheight``, which clamps a resolved
            extent and does not count toward the two of three.
        end_written: Whether ``end`` was written rather than filled in.

    """

    start: float | None = None
    end: float | None = None
    size: float | None = None
    limit: float | None = None
    end_written: bool = False

    @property
    def given(self) -> int:
        """Count the parts that are set."""
        return sum(part is not None for part in (self.start, self.end, self.size))


def span(
    start: float | None,
    end: float | None,
    size: float | None,
    limit: float | None = None,
) -> Span:
    """Return an axis with its missing values filled in.

    The table in doc/template.md#position-and-size-any-two-of-three fills
    ``start`` first and then ``end``, until two of the three are known.
    That one order gives every row of it: nothing becomes ``start=0
    end=0``, a lone ``size`` gains a ``start=0`` beside it, and a lone
    ``end`` the same.

    Args:
        start: ``left`` or ``top``, where the node gave one.
        end: ``right`` or ``bottom``, where the node gave one.
        size: ``width`` or ``height``, where the node gave one.
        limit: ``maxwidth`` or ``maxheight``, where the node gave one.

    """
    written = end is not None
    filled = Span(start, end, size, limit, written)
    if filled.given >= 2:
        return filled
    if filled.start is None:
        filled = Span(0.0, filled.end, filled.size, limit, written)
    if filled.given < 2:
        filled = Span(filled.start, 0.0, filled.size, limit, written)
    return filled


@dataclass(frozen=True)
class Box:
    """An element's declared box, one :class:`Span` per axis.

    Attributes:
        across: The horizontal axis: ``left``, ``right``, ``width``.
        down: The vertical axis: ``top``, ``bottom``, ``height``.

    """

    across: Span
    down: Span


# -- declarations -----------------------------------------------------


@dataclass(frozen=True)
class Parameter:
    """A value supplied by the caller.

    Attributes:
        name: The name expressions reach it by.
        kind: One of :data:`VALUE_TYPES`.
        default: The default as the template wrote it, unparsed.
        value: That default parsed per ``kind``, where it parsed.
        defaultexpr: An expression computing the default.
        format: The Go layout a ``date`` or ``datetime`` default is read with.
        prompt: Whether a front end should ask; the engine ignores it.
        path: Where the node sits.

    """

    name: str
    kind: str
    default: str | None
    value: Any
    defaultexpr: Expression | None
    format: str | None
    prompt: bool
    path: NodePath

    @property
    def required(self) -> bool:
        """Report whether a caller has to supply this parameter."""
        return self.default is None and self.defaultexpr is None


@dataclass(frozen=True)
class Member:
    """One declared member of the input records.

    Attributes:
        name: The name expressions reach it by.
        kind: One of :data:`VALUE_TYPES`.
        nullable: Whether a JSON ``null`` is allowed here.
        format: The Go layout a ``date`` or ``datetime`` member is read with.
        path: Where the node sits.

    """

    name: str
    kind: str
    nullable: bool
    format: str | None
    path: NodePath


@dataclass(frozen=True)
class Records:
    """The declared shape of the input records.

    Attributes:
        members: The declarations, in document order.
        path: Where the node sits.

    """

    members: tuple[Member, ...]
    path: NodePath


@dataclass(frozen=True)
class Variable:
    """An accumulator updated as data is consumed.

    Attributes:
        name: The name expressions reach it by.
        expr: What is folded in.
        init: What seeds the accumulator, where the node gave one.
        calc: One of :data:`CALCS`.
        iterate: When ``expr`` is folded in, one of :data:`SCOPES`.
        itergrp: The group ``iter="group"`` names.
        reset: When the accumulator is cleared, one of :data:`SCOPES`.
        resetgrp: The group ``reset="group"`` names.
        path: Where the node sits.

    """

    name: str
    expr: Expression | None
    init: Expression | None
    calc: str
    iterate: str
    itergrp: str | None
    reset: str
    resetgrp: str | None
    path: NodePath


@dataclass(frozen=True)
class Font:
    """A named font definition.

    Attributes:
        name: The name ``style font=`` reaches it by.
        typeface: The family to resolve, where resolution is by family.
        file: A path to a face, relative to the report's ``basedir``.
        data: The name of a ``data`` node holding a face.
        size: Points.
        bold: The declared weight, which also selects within a collection.
        italic: The declared slant, likewise.
        underline: Drawn by the renderer; it does not affect metrics.
        path: Where the node sits.

    """

    name: str
    typeface: str | None
    file: str | None
    data: str | None
    size: int
    bold: bool
    italic: bool
    underline: bool
    path: NodePath


@dataclass(frozen=True)
class Blob:
    """A named literal blob: image bytes, a font file, or field text.

    The bytes are decoded at load rather than at build time, so that
    a ``content`` that is not the encoding it claims is a load error
    naming the node instead of a failure halfway through a report.

    Attributes:
        name: The name a ``font``, an ``image`` or a ``field`` reaches it by.
        encoding: What the ``content`` was written in, or ``None`` for text.
        compress: What it was compressed with, or ``None``.
        expr: An expression producing the blob at build time.
        content: The decoded bytes, where a ``content`` child gave them
            and they decoded.
        has_content: Whether a ``content`` child was written at all,
            which is what "exactly one of expr or content" is asked of:
            a blob whose base64 was corrupt wrote one and is already
            reported, and saying it has no content would report it twice.
        path: Where the node sits.

    """

    name: str
    encoding: str | None
    compress: str | None
    expr: Expression | None
    content: bytes | None
    has_content: bool
    path: NodePath


# -- formatting -------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """One conditional formatting rule.

    Attributes:
        when: What selects it; ``None`` means always.
        font: The name of a ``font`` node, where it sets one.
        color: ``"#RRGGBB"``, where it sets one.
        bgcolor: ``"#RRGGBB"``, where it sets one.
        path: Where the node sits.

    """

    when: Expression | None
    font: str | None
    color: str | None
    bgcolor: str | None
    path: NodePath


@dataclass(frozen=True)
class Eject:
    """A forced page or column break.

    Attributes:
        kind: ``page`` or ``column``.
        when: What selects the node; ``None`` means always.
        require: Eject only when less than this remains in the frame.
        path: Where the node sits.

    """

    kind: str
    when: Expression | None
    require: float | None
    path: NodePath


@dataclass(frozen=True)
class Outline:
    """An entry in the document's outline tree.

    Attributes:
        title: The entry's text.
        level: Its depth, from 1.
        name: What an ``xref type="outline"`` targets, where it has one.
        when: What selects it among the section's alternatives.
        closed: Whether it renders collapsed.
        path: Where the node sits.

    """

    title: Expression | None
    level: int
    name: Expression | None
    when: Expression | None
    closed: bool
    path: NodePath


# -- body elements ----------------------------------------------------


@dataclass(frozen=True)
class Element:
    """What every body element has.

    Attributes:
        path: Where the node sits.
        box: Its declared geometry.
        halign: How its content sits horizontally in the box.
        valign: How its content sits vertically in the box.
        floating: Whether its vertical position follows what is above it.
        printwhen: What suppresses it; ``None`` means always print.
        styles: Its own ``style`` nodes, in first-win order.

    """

    path: NodePath
    box: Box
    halign: str
    valign: str
    floating: bool
    printwhen: Expression | None
    styles: tuple[Style, ...]


@dataclass(frozen=True)
class Field(Element):
    """Text.

    Attributes:
        expr: The content, where it is computed.
        text: The content, where it is literal.
        data: The name of a ``data`` node holding the content.
        evaltime: The scope whose end ``FINAL`` reads, where deferred.
        align: How each line sits in the box, one of :data:`ALIGNS`;
            ``None`` where the node left it to ``halign``.
        format: The ``%`` format applied to ``expr``.
        stretch: Whether the box grows to fit the wrapped text.

    """

    expr: Expression | None
    text: str | None
    data: str | None
    evaltime: str | None
    align: str | None
    format: str
    stretch: bool


@dataclass(frozen=True)
class Line(Element):
    """A line from one corner of its box to the other.

    Attributes:
        stroke: The pen width; 0 is a hairline.
        dash: One of :data:`DASHES`.
        backslant: Whether it runs bottom-left to top-right instead.

    """

    stroke: float
    dash: str
    backslant: bool


@dataclass(frozen=True)
class Rectangle(Element):
    """A rectangle, filled, outlined, or both.

    Attributes:
        stroke: The pen width; 0 is a hairline.
        dash: One of :data:`DASHES`.
        radius: The corner radius.
        opaque: Whether the fill is drawn.
        outlined: Whether the outline is drawn; the ``stroke`` property.

    """

    stroke: float
    dash: str
    radius: float
    opaque: bool
    outlined: bool


@dataclass(frozen=True)
class Image(Element):
    """A bitmap.

    Attributes:
        file: A path relative to the report's ``basedir``.
        data: The name of a ``data`` node holding the bytes.
        content: A ``content`` child, as the template wrote it.
        kind: ``png``, ``jpeg`` or ``gif``; ``None`` means sniff it.
        scale: One of :data:`IMAGE_SCALES`.
        proportional: Whether ``fill`` preserves the aspect ratio.
        embed: Whether the bytes travel in the printout.

    """

    file: str | None
    data: str | None
    content: str | None
    kind: str | None
    scale: str
    proportional: bool
    embed: bool


@dataclass(frozen=True)
class Barcode(Element):
    """A symbol.

    Attributes:
        kind: One of :data:`BARCODE_TYPES`.
        expr: The content, where it is computed.
        text: The content, where it is literal.
        data: The name of a ``data`` node holding the content.
        evaltime: The scope whose end ``FINAL`` reads, where deferred.
        format: The ``%`` format applied to ``expr`` before encoding.
        module: The narrow bar width.
        vertical: Whether the coding direction is vertical.
        grow: Whether the symbol expands to use the box.
        ink: The bars' colour.
        paper: The background's colour, where one is painted.

    """

    kind: str
    expr: Expression | None
    text: str | None
    data: str | None
    evaltime: str | None
    format: str
    module: float
    vertical: bool
    grow: bool
    ink: str
    paper: str | None


@dataclass(frozen=True)
class Xref:
    """A link region holding body elements of its own.

    It is not an :class:`Element`: it puts no mark on the page and takes
    no ``printwhen`` and no ``style`` of its own.  What it has is a box,
    the alignment that positions content inside that box, and children.

    Attributes:
        path: Where the node sits.
        box: Its declared geometry.
        halign: How its content sits horizontally in the box.
        valign: How its content sits vertically in the box.
        kind: ``outline`` or ``url``.
        target: The outline name, or the URL.
        caption: A description, where the node gives one.
        elements: Its children, in paint order.

    """

    path: NodePath
    box: Box
    halign: str
    valign: str
    kind: str
    target: Expression | None
    caption: Expression | None
    elements: tuple[Element | Xref, ...]


@dataclass(frozen=True)
class Arg:
    """A value for one of a subreport's parameters.

    Attributes:
        name: The parameter it supplies.
        value: The expression, evaluated in the host's context.
        path: Where the node sits.

    """

    name: str
    value: Expression | None
    path: NodePath


@dataclass(frozen=True)
class Subreport:
    """Another template, run over a nested sequence.

    Attributes:
        template: The path as the template wrote it.
        file: That path resolved against the host's ``basedir``.
        report: The loaded document, where ``template=`` named one.
        embedded: The name of an ``embedded`` layout.
        scope: The qualified name ``embedded`` resolved to, which is the
            chain of ``embedded`` names from the ``layout`` down to it.
        seq: What orders it against the host band.
        data: The expression yielding the sequence.
        when: What suppresses the invocation.
        inline: Whether its bands go into the host's frame.
        ownpageno: Whether page numbering restarts inside it.
        args: Its ``arg`` nodes, in document order.
        order: Its position among the host band's subreports, which is
            what breaks a tie in ``seq``.
        path: Where the node sits.

    """

    template: str | None
    file: Path | None
    report: Report | None
    embedded: str | None
    scope: tuple[str, ...] | None
    seq: int
    data: Expression | None
    when: Expression | None
    inline: bool
    ownpageno: bool
    args: tuple[Arg, ...]
    order: int
    path: NodePath


@dataclass(frozen=True)
class Section:
    """One band.

    Attributes:
        kind: One of :data:`SECTIONS`.
        height: A **minimum**; ``None`` is ``height="auto"``, a minimum of 0.
        printwhen: What suppresses it; ``None`` means always print.
        split: Whether it may break across frames.
        orphans: The minimum text lines left behind at a break.
        widows: The minimum text lines carried forward.
        swapheader: On a ``title``, whether it sits above the page header.
        swapfooter: On a ``summary``, whether it sits below the page footer.
        styles: Its ``style`` nodes, in first-win order.
        ejects: Its ``eject`` nodes, in first-win order.
        outlines: Its ``outline`` nodes, in first-win order.
        elements: Its body elements and ``xref`` nodes, in paint order.
        subreports: Its subreports, by ``seq`` then document order.
        path: Where the node sits.

    """

    kind: str
    height: float | None
    printwhen: Expression | None
    split: bool
    orphans: int
    widows: int
    swapheader: bool
    swapfooter: bool
    styles: tuple[Style, ...]
    ejects: tuple[Eject, ...]
    outlines: tuple[Outline, ...]
    elements: tuple[Element | Xref, ...]
    subreports: tuple[Subreport, ...]
    path: NodePath


@dataclass(frozen=True)
class Columns:
    """A frame split into columns.

    Attributes:
        count: How many.
        gap: The space between two of them.
        balance: Whether a page's bands are spread over them.
        styles: Its ``style`` nodes, in first-win order.
        header: The per-column header, where it has one.
        footer: The per-column footer, where it has one.
        path: Where the node sits.

    """

    count: int
    gap: float
    balance: bool
    styles: tuple[Style, ...]
    header: Section | None
    footer: Section | None
    path: NodePath


@dataclass(frozen=True)
class Nesting:
    """What every level that can hold bands has.

    ``layout``, ``group`` and ``embedded`` all carry the same five, and
    exactly one of ``group`` and ``detail`` is required at each of them.

    Attributes:
        styles: Its ``style`` nodes, in first-win order.
        title: The level's title band.
        summary: The level's summary band.
        columns: A ``columns`` block opened here.
        group: The next group down, where this level nests one.
        detail: The detail band, where this level is the innermost.
        path: Where the node sits.

    """

    styles: tuple[Style, ...]
    title: Section | None
    summary: Section | None
    columns: Columns | None
    group: Group | None
    detail: Section | None
    path: NodePath


@dataclass(frozen=True)
class Group(Nesting):
    """A data-driven grouping level.

    Attributes:
        name: The name ``X_COUNT`` and ``X_PAGE_NUMBER`` are built from.
        expr: The key; the group breaks when it changes.
        keeptogether: Whether the whole group goes on one frame when it fits.
        minrows: The detail rows that must follow the title in one frame.
        mintailrows: The detail rows that must precede the summary.

    """

    name: str
    expr: Expression | None
    keeptogether: bool
    minrows: int
    mintailrows: int


@dataclass(frozen=True)
class Paper:
    """The page a report prints on, in points.

    Attributes:
        width: The page width, with ``landscape`` already applied.
        height: The page height, likewise.
        left: The left margin.
        right: The right margin.
        top: The top margin.
        bottom: The bottom margin.

    """

    width: float
    height: float
    left: float
    right: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Layout(Nesting):
    """Page geometry and the root of the band tree.

    Attributes:
        paper: The page and its margins.
        header: The per-frame header.
        footer: The per-frame footer.
        embedded: The layouts declared directly here, in document order.

    """

    paper: Paper
    header: Section | None
    footer: Section | None
    embedded: tuple[Embedded, ...]


@dataclass(frozen=True)
class Embedded(Nesting):
    """A subreport layout defined inline.

    It is its own namespace for parameters, records, variables and groups,
    and shares the enclosing report's fonts, data, ``basedir`` and page.

    Attributes:
        name: What a ``subreport embedded=`` names it by.
        qualname: The chain of ``embedded`` names from the ``layout`` down
            to this one, which is what a resolved reference records.
        parameters: Its own parameters, in document order.
        records: Its own record declaration.
        variables: Its own accumulators, in document order.
        header: A per-page header, which only a paginating subreport has.
        footer: A per-page footer, likewise.
        embedded: The layouts declared directly here, in document order.

    """

    name: str
    qualname: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    records: Records | None
    variables: tuple[Variable, ...]
    header: Section | None
    footer: Section | None
    embedded: tuple[Embedded, ...]


@dataclass(frozen=True)
class Report:
    """One template document.

    Attributes:
        file: The path it was read from, as the caller named it.
        basedir: What ``image file=``, ``font file=`` and
            ``subreport template=`` resolve against.
        name: Its title.
        description: Its subtitle.
        version: Its version, as a string.
        author: Who wrote it.
        parameters: Its parameters, in document order.
        records: Its record declaration.
        variables: Its accumulators, in document order.
        fonts: Its font definitions, in document order.
        data: Its literal blobs, in document order.
        layout: Its layout, unless the document was too broken to build one.
        layouts: Every ``embedded`` layout in it, by qualified name.

    """

    file: str
    basedir: Path
    name: str | None
    description: str | None
    version: str | None
    author: str | None
    parameters: tuple[Parameter, ...]
    records: Records | None
    variables: tuple[Variable, ...]
    fonts: tuple[Font, ...]
    data: tuple[Blob, ...]
    layout: Layout | None
    layouts: dict[tuple[str, ...], Embedded]


# -- parameter values as text -----------------------------------------


def parse_text(kind: str, text: str, format: str | None = None) -> Any:
    """Return the value some text spells, read as a declared type.

    The table in doc/template.md#parameter-values-as-text is what this
    implements.  It is one function for two callers: a ``default``
    on a `parameter`, read when the template loads, and a ``--param
    NAME=VALUE`` from the command line.  Both have to mean the same
    thing, which is why the reading is here rather than in either.

    Args:
        kind: One of :data:`VALUE_TYPES`.
        text: The value as the caller spelled it.
        format: The Go layout a ``date`` or ``datetime`` is read with;
            ``None`` is RFC 3339.

    Raises:
        BadValue: The text does not spell a value of that type.

    """
    if kind in ("date", "datetime"):
        return read_time(kind, text, format)
    reader = READERS.get(kind)
    if reader is None:
        raise BadValue(f"unknown type {kind!r}")
    return reader(text)


def read_int(text: str) -> int:
    """Return the integer some text spells, at arbitrary precision.

    Args:
        text: The value as the caller spelled it.

    """
    body = text[1:] if text[:1] in "+-" else text
    if not body or not body.isascii() or not body.isdigit():
        raise BadValue(f"not an integer: {text!r}")
    return int(text)


def read_decimal(text: str) -> Decimal:
    """Return the exact decimal some text spells.

    A sign, digits, an optional point and fractional digits, and nothing
    else.  The grammar is written out rather than left to the host's
    reader, which is the same reason doc/template.md#dimension spells
    its own out: Python's ``Decimal`` also takes ``1E3``, ``Infinity``,
    ``NaN`` and underscores, and none of those is a row of this table.

    Args:
        text: The value as the caller spelled it.

    """
    if not DECIMAL.fullmatch(text):
        raise BadValue(f"not a decimal: {text!r}")
    try:
        return Decimal(parse_decimal(text))
    except (SrError, InvalidOperation, ArithmeticError):
        raise BadValue(f"not a decimal: {text!r}") from None


def read_float(text: str) -> float:
    """Return the float some text spells.

    ``inf`` and ``nan`` are taken here and refused in a dimension:
    a parameter is a number a template computes with, while a dimension
    becomes a coordinate, and an infinite coordinate cannot be written.

    One sign is stripped to find those three names, not every leading sign
    there is: ``--inf`` spells no number, and stripping both would hand it
    to a reader that raises something other than :class:`~sr.errors.BadValue`.

    Args:
        text: The value as the caller spelled it.

    """
    body = (text[1:] if text[:1] in "+-" else text).lower()
    if body in ("inf", "infinity", "nan"):
        return float(text)
    try:
        return parse_number(text)
    except BadValue:
        raise BadValue(f"not a number: {text!r}") from None


def read_bool(text: str) -> bool:
    """Return the boolean some text spells, case-insensitively.

    Args:
        text: The value as the caller spelled it.

    """
    folded = text.strip().lower()
    if folded in ("true", "1"):
        return True
    if folded in ("false", "0"):
        return False
    raise BadValue(f"not a boolean: {text!r}; want true, false, 1 or 0")


def read_time(kind: str, text: str, format: str | None) -> Any:
    """Return the time some text spells.

    Args:
        kind: ``date`` or ``datetime``, which picks the default layout.
        text: The value as the caller spelled it.
        format: The Go layout to read it with, where a node gave one.

    """
    layout = format or (RFC3339_DATE if kind == "date" else RFC3339)
    try:
        return parse_time(text, layout)
    except Exception as refused:
        raise BadValue(f"not a {kind}: {text!r} ({refused})") from None


def read_json(kind: str, text: str) -> Any:
    """Return the JSON value some text spells, frozen.

    Args:
        kind: ``object`` or ``list``, which is what the text must hold.
        text: The value as the caller spelled it.

    """
    try:
        value = json.loads(text)
    except ValueError as refused:
        raise BadValue(f"not JSON: {refused}") from None
    wanted = dict if kind == "object" else list
    if not isinstance(value, wanted):
        article = "an object" if kind == "object" else "an array"
        raise BadValue(f"want {article}, got {type(value).__name__}")
    return freeze(value)


def freeze(value: Any) -> Any:
    """Return a JSON value as this engine's frozen equivalent.

    An object becomes a record, so that its members are reachable as
    attributes; an array becomes a frozen list.  Numbers, strings,
    booleans and ``null`` are values already.

    Args:
        value: What ``json.loads`` produced.

    """
    if isinstance(value, dict):
        return Record({name: freeze(member) for name, member in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    return value


# The reader per declared type.  `date` and `datetime` are dispatched
# before this table is reached, being the two that take a layout,
# so a name missing from it is an unknown type and nothing else.
READERS: Final[dict[str, Any]] = {
    "string": lambda text: text,
    "int": read_int,
    "decimal": read_decimal,
    "float": read_float,
    "bool": read_bool,
    "object": lambda text: read_json("object", text),
    "list": lambda text: read_json("list", text),
}
