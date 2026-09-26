"""The printout in memory: a header, pages, and the marks on them.

doc/printout.md calls this "the primary artifact" -- the engine hands it
to a renderer directly and serializes only when asked -- so the model is
what the layout produces and the writer is a second, separate step.

Three rules of the format are visible in the shapes here.

* **Nothing is evaluable.**  Every expression has been evaluated,
  every box resolved to absolute coordinates, every string wrapped
  to lines.  A mark holds numbers and strings and nothing that could
  be asked a question.
* **A mark carries no record of where it came from.**  The band,
  the record and the element that produced it are gone by the time
  it is here, which is what lets a page be a flat list in paint order.
* **Paths stay as the engine resolved them.**  A `font file=` is held
  absolute and is written relative to the printout at serialization,
  since that is when the destination is known.  :attr:`FontEntry.step`
  is what the writer reads to decide, per doc/printout.md#paths.

The kinds produced so far are :class:`Text`, :class:`Line`,
:class:`Rectangle` and :class:`Xref`.  Images, barcodes and outline
entries arrive with the elements that make them.

"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from sr.errors import BuildWarning
from sr.units import round_points

__all__ = [
    "VERSION",
    "Box",
    "FontEntry",
    "Line",
    "Mark",
    "Page",
    "Paper",
    "Printout",
    "Rectangle",
    "Report",
    "Text",
    "Xref",
]

# The format version the `sr` field carries.
VERSION = 1


@dataclass(frozen=True)
class Box:
    """A mark's rectangle, absolute and in points.

    ``x`` and ``y`` are the top-left corner measured from the
    top-left of the page, and Y grows downward.  Width and height
    are non-negative, which doc/printout.md#invariants asserts.

    Attributes:
        x: Distance from the page's left edge.
        y: Distance from the page's top edge.
        width: The extent across.
        height: The extent down.

    """

    x: float
    y: float
    width: float
    height: float

    @property
    def bottom(self) -> float:
        """Return the edge below the box."""
        return self.y + self.height

    def moved(self, across: float, down: float) -> Box:
        """Return this box translated.

        Marks are built band-relative and translated once, on commit,
        which is what makes splitting and splicing translations of
        already-built marks rather than re-measurements.

        A translated corner is a computed coordinate like any other,
        so doc/layout.md#coordinates-and-rounding rounds it at once.

        Args:
            across: What to add to ``x``.
            down: What to add to ``y``.

        """
        return Box(
            round_points(self.x + across),
            round_points(self.y + down),
            self.width,
            self.height,
        )


@dataclass(frozen=True)
class Mark:
    """What every mark has: a kind, and a box.

    Attributes:
        box: Where it is drawn.

    """

    box: Box

    @property
    def kind(self) -> str:
        """Return the `kind` field the printout writes."""
        return type(self).__name__.lower()

    def moved(self, across: float, down: float) -> Mark:
        """Return this mark with its box translated.

        Args:
            across: What to add to the box's ``x``.
            down: What to add to the box's ``y``.

        """
        return replace(self, box=self.box.moved(across, down))


@dataclass(frozen=True)
class Text(Mark):
    """Wrapped text, already broken into the lines that will be drawn.

    A renderer must not re-wrap, and needs font metrics only to place
    glyphs within a line.

    Attributes:
        font: The name of a header `fonts` entry.
        color: The stroke colour of the glyphs.
        align: ``left``, ``center``, ``right`` or ``justified``.
        leading: The baseline-to-baseline distance.
        lines: The wrapped lines, in order; never empty.
        last_line_justified: Whether the final line is the middle
            of a paragraph a split band carried onto the next frame.

    """

    font: str
    color: str
    align: str
    leading: float
    lines: tuple[str, ...]
    last_line_justified: bool = False


@dataclass(frozen=True)
class Line(Mark):
    """A rule, drawn corner to corner of its box.

    Attributes:
        width: The stroke width; 0 means a hairline.
        dash: The dash pattern's name.
        color: The stroke colour.
        backslant: Whether it runs bottom-left to top-right instead.

    """

    width: float
    dash: str
    color: str
    backslant: bool


@dataclass(frozen=True)
class Rectangle(Mark):
    """A box, outlined, filled, or both.

    ``stroke`` and ``fill`` are independently optional: an absent
    ``stroke`` draws no outline whatever ``width`` says, and an
    absent ``fill`` leaves the interior untouched.

    Attributes:
        width: The stroke width; 0 means a hairline.
        dash: The dash pattern's name.
        stroke: The outline colour, where there is an outline.
        fill: The interior colour, where the interior is painted.
        radius: The corner radius; 0 for square corners.

    """

    width: float
    dash: str
    stroke: str | None
    fill: str | None
    radius: float


@dataclass(frozen=True)
class Xref(Mark):
    """A link region, and the marks drawn inside it.

    The box is purely a hit region.  doc/printout.md#xref puts the nested
    marks in **page** coordinates rather than relative to that box,
    so a renderer can flatten them recursively and draw in one pass,
    which is why translating an xref translates everything inside it.

    Attributes:
        link: ``url`` or ``outline``, which the printout calls `type`.
        target: The URL, or the `name` of the outline entry it points at.
        caption: The hover text, where one was given.
        marks: What is drawn inside it, in paint order.

    """

    link: str
    target: str
    caption: str | None
    marks: tuple[Mark, ...]

    def moved(self, across: float, down: float) -> Mark:
        """Return this link region translated, with everything in it.

        Args:
            across: What to add to every ``x``.
            down: What to add to every ``y``.

        """
        return replace(
            self,
            box=self.box.moved(across, down),
            marks=tuple(one.moved(across, down) for one in self.marks),
        )


@dataclass(frozen=True)
class FontEntry:
    """One resolved font, as the header's `fonts` table records it.

    ``file`` is held as the engine resolved it, which is absolute.
    Whether it reaches the printout that way is the writer's decision
    and follows ``step``: a font the template named travels with the
    document and is written relative to it.

    Attributes:
        name: The name the template gave the `font` node.
        size: The size in points.
        bold: Whether the node declared bold.
        italic: Whether the node declared italic.
        underline: Whether the node declared underline.
        face: The family of the face that was measured.
        step: Which step of the resolution chain produced it.
        requested: The `typeface` asked for, where one was.
        file: The file the face was read from.
        data: The `data` entry it was read from instead.
        index: The face's position inside a collection.

    """

    name: str
    size: int
    bold: bool
    italic: bool
    underline: bool
    face: str
    step: str
    requested: str | None = None
    file: Path | None = None
    data: str | None = None
    index: int = 0


@dataclass(frozen=True)
class Paper:
    """The page geometry a page runs at.

    Attributes:
        width: The paper's width in points.
        height: Its height.
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
class Report:
    """What the template's `report` node said about itself.

    Attributes:
        name: Its title.
        description: Its subtitle.
        version: Its version, as a string.
        author: Who wrote it.

    """

    name: str | None = None
    description: str | None = None
    version: str | None = None
    author: str | None = None


@dataclass(frozen=True)
class Page:
    """One page, and the marks on it in paint order.

    The four margins are independently optional and are present
    only where they differ from the document's, which is why
    a zero is written rather than omitted: a page flush to the
    paper edge under a header that insets is an override.

    Attributes:
        number: The 1-based page number as printed.
        marks: The marks, in paint order.
        paper: The geometry this page runs at,
            where it is not the document's own.

    """

    number: int
    marks: tuple[Mark, ...] = ()
    paper: Paper | None = None


@dataclass
class Printout:
    """A whole document: the header's fields, and the pages.

    ``pages`` is not a field: doc/printout.md#header-line says
    the header's is the number of page lines that follow, so it
    is counted at serialization rather than stored and kept in step.

    Attributes:
        report: The template's metadata.
        built: The run's ``BUILD_TIME``, as RFC 3339.
        engine: The name and version of the producing engine.
        strict_fonts: Whether font guessing was disabled for this run.
        paper: The default page geometry.
        fonts: The resolved fonts, in the order they were declared.
        data: The shared blobs, by name.
        pages: The pages, in output order.
        warnings: What the build had to say about the document.
        group_runs: Per group name, how many times it opened.
        group_keys: Per group name, how many distinct keys it saw.

    """

    report: Report
    built: str
    engine: str
    strict_fonts: bool
    paper: Paper
    fonts: tuple[FontEntry, ...] = ()
    data: dict[str, bytes | str] = field(default_factory=dict)
    pages: tuple[Page, ...] = ()
    warnings: tuple[BuildWarning, ...] = ()
    group_runs: dict[str, int] = field(default_factory=dict)
    group_keys: dict[str, int] = field(default_factory=dict)
