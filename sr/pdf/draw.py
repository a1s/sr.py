"""Drawing: each mark as the operators that paint it.

A printout's marks are already placed, wrapped and encoded,
so drawing one is a translation.  What is left is the four things
doc/render.md#what-the-renderer-decides leaves to a renderer,
and each is one function here.

=====================  ==================================================
:func:`baseline`       where a line sits inside the leading it reserved
:func:`placed`         where a line starts, and how a justified one
                       spends its slack
:data:`DASHES`         what a `dash` enumeration measures
:meth:`Setting.rule`   where an underline sits and how thick it is
=====================  ==================================================

Two rules hold throughout.

* **Y is flipped as it is written**, ``y' = height - y``,
  rather than by a mirroring matrix, which would mirror the glyphs
  with it: doc/render.md#coordinates.
* **A computed length is rounded once, as it is written**,
  to three decimals.  An edge two shapes share is computed once
  and written once, so a barcode's bars meet where the runs say
  they meet rather than a thousandth of a point apart.

Each mark's operators leave nothing behind for the next one
to inherit unknowingly: a mark sets every colour, width and dash
it paints with, and a clip or a matrix is undone before it ends.

"""

from __future__ import annotations

from dataclasses import dataclass

from sr.fonts.text import BREAK, Metrics, chunks
from sr.pdf.images import Picture
from sr.pdf.objects import number
from sr.pdf.subset import Embedded
from sr.printout.model import Barcode, Box, Image, Line, Rectangle, Text
from sr.units import round_points

__all__ = [
    "DASHES",
    "KAPPA",
    "Setting",
    "barcode",
    "baseline",
    "colour",
    "draws",
    "image",
    "line",
    "placed",
    "rectangle",
    "segments_of",
    "text",
]

# doc/render.md#lines-and-rectangles: in points, and absolute
# rather than a multiple of the stroke width, so a hairline
# and a two-point rule dash on the same rhythm.
DASHES = {
    "solid": "[] 0 d",
    "dot": "[1 2] 0 d",
    "dash": "[3 2] 0 d",
    "dashdot": "[3 2 1 2] 0 d",
}

# How far along its tangent a cubic Bézier's control point sits
# for the curve to approximate a quarter circle of unit radius.
KAPPA = 0.5522847498307936


@dataclass(frozen=True)
class Setting:
    """A font entry, as text is set in it.

    Attributes:
        font: The face, as the file carries it.
        size: The size, in points.
        metrics: The face at that size, measuring as the engine did.
        underline: Whether the entry asks for a rule under each line.

    """

    font: Embedded
    size: int
    metrics: Metrics
    underline: bool

    def rule(self, start: float, width: float, base: float) -> Box:
        """Return the underline under one line, in page coordinates.

        Its top is the face's underline position below the baseline,
        and it is the face's underline thickness tall.

        Args:
            start: Where the line starts.
            width: How far the rule runs.
            base: Where the line's baseline is, measured down the page.

        """
        down, thick = self.font.underline(self.size)
        return Box(start, base + down, width, thick)


def colour(text: str) -> str:
    """Return a ``#RRGGBB`` colour as the three operands of `rg` or `RG`.

    Three decimals separate every eight-bit level from its neighbours,
    whose difference is a little under four thousandths.

    Args:
        text: The colour, as the printout spells it.

    """
    levels = (int(text[at : at + 2], 16) for at in (1, 3, 5))
    return " ".join(number(level / 255) for level in levels)


def rect(
    left: float,
    top: float,
    right: float,
    bottom: float,
    height: float,
) -> str:
    """Return a rectangle's `re`, from its edges in page coordinates.

    Each edge is rounded before anything is derived from it,
    so two rectangles that share an edge write it as one number.

    Args:
        left: Its left edge.
        top: Its top edge, measured down the page.
        right: Its right edge.
        bottom: Its bottom edge, measured down the page.
        height: The page's height, which the edges are flipped against.

    """
    left, top, right, bottom = (
        round_points(edge) for edge in (left, top, right, bottom)
    )
    return (
        f"{number(left)} {number(height - bottom)} "
        f"{number(right - left)} {number(bottom - top)} re"
    )


def stroke_state(width: float, dash: str, ink: str) -> list[str]:
    """Return the operators that set up a stroke.

    The width is passed through as it is, a zero included:
    PDF draws a zero-width line as the thinnest the device can,
    which is the format's hairline.

    Args:
        width: The stroke width.
        dash: The dash pattern's name.
        ink: The stroke colour.

    """
    return [f"{number(width)} w", DASHES[dash], f"{colour(ink)} RG"]


def baseline(mark: Text, setting: Setting, index: int) -> float:
    """Return where line ``index`` of a text mark sits, measured down the page.

    doc/render.md#text: the face's own em extent, `hhea`'s ascender
    and descender at the size, is centred in the leading the printout
    reserved, and the baseline measured down from the top of it.
    A face taller than its leading overhangs evenly, above and below.

    Args:
        mark: The text mark.
        setting: Its font.
        index: Which line, from 0.

    """
    ascender = setting.font.ascender(setting.size)
    descender = setting.font.descender(setting.size)
    return (
        mark.box.y
        + (mark.leading - (ascender + descender)) / 2
        + ascender
        + index * mark.leading
    )


def segments_of(line: str) -> list[str]:
    """Return a justified line cut into segments.

    A segment is one word and the whitespace after it, so the spaces
    are drawn as text and stay in what a reader extracts.  That is
    a chunk as doc/layout.md#line-breaking defines one, except that
    leading whitespace stays with the first word rather than being
    a gap of its own, so an indent does not stretch.

    Args:
        line: The line.

    """
    found = chunks(line)
    if len(found) > 1 and not found[0].strip(BREAK):
        found[:2] = [found[0] + found[1]]
    return found


def placed(
    mark: Text,
    setting: Setting,
    line: str,
    last: bool,
) -> tuple[list[tuple[float, str]], float]:
    """Return where a line's text is drawn, and how far its underline runs.

    The width a line is placed by is measured as the engine measured it,
    from `hmtx` advances with no kerning, which is the one measurement
    doc/render.md#text allows a renderer.

    A justified line is drawn as segments, each at an exact distance
    from the box's edge, with the slack shared evenly among the joins,
    so the last one ends on the far edge.  The slack is the box's width
    less the segments' own widths, each rounded as the engine rounds a chunk,
    because it is their sum that has to reach the edge.  A line with
    no room to grow, or no join to grow at, is set flush left; so is
    the last line of a paragraph, unless a split band said it continues.

    Args:
        mark: The text mark.
        setting: Its font.
        line: One of its lines.
        last: Whether it is the mark's last line.

    Returns:
        Each run of text with the left edge it starts at, and the length
        of the underline: the box's width for a line that was stretched,
        and the line's own width otherwise.

    """
    box = mark.box
    width = setting.metrics.width(line)
    if mark.align == "justified" and (not last or mark.last_line_justified):
        segments = segments_of(line)
        widths = [setting.metrics.width(one) for one in segments]
        slack = round_points(box.width - sum(widths))
        if len(segments) > 1 and slack > 0:
            gap = slack / (len(segments) - 1)
            runs = []
            advanced = 0.0
            pairs = zip(segments, widths, strict=True)
            for index, (segment, measured) in enumerate(pairs):
                runs.append((box.x + advanced + index * gap, segment))
                advanced += measured
            return runs, box.width
    if mark.align == "center":
        return [(box.x + (box.width - width) / 2, line)], width
    if mark.align == "right":
        return [(box.x + box.width - width, line)], width
    return [(box.x, line)], width


def text(mark: Text, setting: Setting, height: float) -> list[str]:
    """Return a text mark's operators.

    Glyphs are painted in the mark's colour as a non-stroking colour,
    and so is each underline, after the text.  An empty line draws
    nothing, and a line that is only whitespace draws no underline.

    Args:
        mark: The text mark.
        setting: Its font, frozen, so that every character has a code.
        height: The page's height.

    """
    ink = colour(mark.color)
    shown = [
        "BT",
        f"/{setting.font.resource} {setting.size} Tf",
        f"{ink} rg",
    ]
    rules: list[Box] = []
    final = len(mark.lines) - 1
    for index, line in enumerate(mark.lines):
        if not line:
            continue
        base = baseline(mark, setting, index)
        runs, ruled = placed(mark, setting, line, index == final)
        for start, run in runs:
            shown.append(f"1 0 0 1 {number(start)} {number(height - base)} Tm")
            shown.append(f"{setting.font.encode(run)} Tj")
        if setting.underline and line.strip():
            rules.append(setting.rule(runs[0][0], ruled, base))
    shown.append("ET")
    if rules:
        for one in rules:
            right = one.x + one.width
            shown.append(rect(one.x, one.y, right, one.bottom, height))
        shown.append("f")
    return shown


def line(mark: Line, height: float) -> list[str]:
    """Return a line mark's operators.

    It runs from the box's top-left corner to its bottom-right,
    or from bottom-left to top-right when it is a backslant.

    Args:
        mark: The line mark.
        height: The page's height.

    """
    box = mark.box
    right = box.x + box.width
    first, second = box.y, box.bottom
    if mark.backslant:
        first, second = second, first
    return [
        *stroke_state(mark.width, mark.dash, mark.color),
        f"{number(box.x)} {number(height - first)} m",
        f"{number(right)} {number(height - second)} l",
        "S",
    ]


def rectangle(mark: Rectangle, height: float) -> list[str]:
    """Return a rectangle mark's operators.

    Stroked when it carries a `stroke`, filled when it carries a `fill`,
    independently, and nothing at all when it carries neither:
    that is not a zero width but no rectangle.

    Args:
        mark: The rectangle mark.
        height: The page's height.

    """
    if mark.stroke is None and mark.fill is None:
        return []
    painted: list[str] = []
    if mark.fill is not None:
        painted.append(f"{colour(mark.fill)} rg")
    if mark.stroke is not None:
        painted.extend(stroke_state(mark.width, mark.dash, mark.stroke))
    painted.extend(outline_of(mark.box, mark.radius, height))
    if mark.fill is None:
        painted.append("S")
    elif mark.stroke is None:
        painted.append("f")
    else:
        painted.append("B")
    return painted


def outline_of(box: Box, radius: float, height: float) -> list[str]:
    """Return a rectangle's path, its corners rounded where it has a radius.

    The radius is clamped to half the shorter side, and each corner
    is a quarter circle drawn as one cubic Bézier curve.

    Args:
        box: The rectangle.
        radius: The corner radius.
        height: The page's height.

    """
    right = box.x + box.width
    corner = min(radius, box.width / 2, box.height / 2)
    if corner <= 0:
        return [rect(box.x, box.y, right, box.bottom, height)]
    left = box.x
    low = height - box.bottom
    high = height - box.y
    near = corner * (1 - KAPPA)

    def point(across: float, up: float) -> str:
        return f"{number(across)} {number(up)}"

    return [
        f"{point(left + corner, low)} m",
        f"{point(right - corner, low)} l",
        f"{point(right - near, low)} {point(right, low + near)} "
        f"{point(right, low + corner)} c",
        f"{point(right, high - corner)} l",
        f"{point(right, high - near)} {point(right - near, high)} "
        f"{point(right - corner, high)} c",
        f"{point(left + corner, high)} l",
        f"{point(left + near, high)} {point(left, high - near)} "
        f"{point(left, high - corner)} c",
        f"{point(left, low + corner)} l",
        f"{point(left, low + near)} {point(left + near, low)} "
        f"{point(left + corner, low)} c",
        "h",
    ]


def draws(mark: Image) -> bool:
    """Return whether an image mark draws anything.

    It does when its box has area, and its crop where it has one:
    either with none would make a matrix with no inverse.

    Args:
        mark: The image mark.

    """
    box = mark.box
    crop = mark.crop
    if box.width <= 0 or box.height <= 0:
        return False
    return crop is None or (crop.width > 0 and crop.height > 0)


def image(mark: Image, picture: Picture, height: float) -> list[str]:
    """Return an image mark's operators.

    With no `crop`, the image is mapped onto the box.  With one,
    the whole image is drawn at the scale that maps the crop
    onto the box, offset so that the crop's corner lands on
    the box's, and the box is the clipping path.  A box with
    no area draws nothing, since its matrix would have no inverse.

    Args:
        mark: The image mark.
        picture: The image it shows.
        height: The page's height.

    """
    if not draws(mark):
        return []
    box = mark.box
    crop = mark.crop
    if crop is None:
        return [
            "q",
            f"{number(box.width)} 0 0 {number(box.height)} "
            f"{number(box.x)} {number(height - box.bottom)} cm",
            f"/{picture.resource} Do",
            "Q",
        ]
    across = box.width / crop.width
    down = box.height / crop.height
    wide = picture.width * across
    tall = picture.height * down
    left = box.x - crop.x * across
    top = box.y - crop.y * down
    return [
        "q",
        rect(box.x, box.y, box.x + box.width, box.bottom, height),
        "W n",
        f"{number(wide)} 0 0 {number(tall)} "
        f"{number(left)} {number(height - (top + tall))} cm",
        f"/{picture.resource} Do",
        "Q",
    ]


def barcode(mark: Barcode, height: float) -> list[str]:
    """Return a barcode mark's operators.

    The paper, where there is one, is filled over the whole box first,
    quiet zones included, and the bars go on top of it in the ink.
    Runs alternate light and dark from a light one, so an odd index
    is a bar and nothing records which comes first.

    Args:
        mark: The barcode mark.
        height: The page's height.

    """
    box = mark.box
    right = box.x + box.width
    painted: list[str] = []
    if mark.paper is not None:
        painted.append(f"{colour(mark.paper)} rg")
        painted.append(rect(box.x, box.y, right, box.bottom, height))
        painted.append("f")
    bars = rows_of(mark, height) if mark.rows else stripes_of(mark, height)
    if bars:
        painted.append(f"{colour(mark.ink)} rg")
        painted.extend(bars)
        painted.append("f")
    return painted


def stripes_of(mark: Barcode, height: float) -> list[str]:
    """Return a 1-D symbol's bars, each across the whole box.

    The coding direction is rightward, or downward for a vertical symbol.

    Args:
        mark: The barcode mark.
        height: The page's height.

    """
    box = mark.box
    bars = []
    at = 0
    for index, run in enumerate(mark.stripes):
        if index % 2 and run:
            start = at * mark.module
            end = (at + run) * mark.module
            if mark.vertical:
                bars.append(
                    rect(
                        box.x,
                        box.y + start,
                        box.x + box.width,
                        box.y + end,
                        height,
                    )
                )
            else:
                bars.append(
                    rect(
                        box.x + start,
                        box.y,
                        box.x + end,
                        box.bottom,
                        height,
                    )
                )
        at += run
    return bars


def rows_of(mark: Barcode, height: float) -> list[str]:
    """Return a 2-D symbol's dark runs, each one module deep.

    Rows go down from the top and runs rightward from the left.
    A vertical symbol is turned a quarter turn clockwise: the runs go
    down the page and the rows advance leftward from the right edge.

    Args:
        mark: The barcode mark.
        height: The page's height.

    """
    box = mark.box
    module = mark.module
    right = box.x + box.width
    bars = []
    for row_index, row in enumerate(mark.rows):
        at = 0
        for index, run in enumerate(row):
            if index % 2 and run:
                if mark.vertical:
                    bars.append(
                        rect(
                            right - (row_index + 1) * module,
                            box.y + at * module,
                            right - row_index * module,
                            box.y + (at + run) * module,
                            height,
                        )
                    )
                else:
                    bars.append(
                        rect(
                            box.x + at * module,
                            box.y + row_index * module,
                            box.x + (at + run) * module,
                            box.y + (row_index + 1) * module,
                            height,
                        )
                    )
            at += run
    return bars
