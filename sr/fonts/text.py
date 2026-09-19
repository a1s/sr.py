"""Measurement and line breaking, as doc/layout.md writes them down.

Two rules from doc/layout.md#text-metrics govern every measurement here:

* **Advance means `hmtx`.**  Per-glyph advances are summed and nothing
  is shaped, so nothing is kerned.  A renderer that kerns disagrees with
  the document exactly where it is hardest to notice -- an ordinary
  sentence measures identically and a kerning-heavy line does not.
* **Leading is 1.2 times the font size.**  A constant multiplier rather
  than the face's own metrics, because line spacing must not change
  when a typeface is substituted: that would change how many lines fit
  and therefore where every page after it breaks.

Line breaking is doc/layout.md#line-breaking, and it is normative to the
character: a text mark's `lines` array decides the height of a stretch
field, the height of its band, and where the page breaks.  Four parts
of the rule are easy to get subtly wrong and each is pinned here.

* **Chunks, not words.**  A chunk is a maximal run of characters that
  are neither space nor tab, *together with the run of spaces and tabs
  after it*.  Either run may be empty, which is what makes a paragraph's
  leading whitespace a chunk of its own.  The whitespace inside a chunk
  counts toward the fit, so a line takes another word only when that word
  and the whitespace before the next one still fit.
* **A line's width is accumulated and rounded at every step**,
  not measured once at the end.  The two give different documents,
  and doc/layout.md#fitting works the difference out to the unit.
* **Each chunk contributes its own width, rounded once** -- not the total
  of a walk through its codepoints, which can differ by a unit in the
  last place.  The walk is used to test and to cut an overlong chunk,
  and its total is then forgotten.
* **What decides whether a chunk is cut is how much of it the walk
  consumed**, not whether the chunk fits.  A chunk that begins a line
  is therefore walked whether or not its own width is within the box:
  the two figures differ, and doc/layout.md#overlong-runs settles
  the case on the walk.  They also come apart in a box narrower than
  one codepoint, where the forced first codepoint may be the whole
  of what remains.

Trimming follows from the breaks and from nothing else.  A line ended
at a break opportunity loses its trailing run of spaces and tabs,
because the break consumed them, and so does the last line of a paragraph.
A line ended by a cut is not trimmed, because a cut consumes nothing:
it falls between two codepoints, wherever they happen to be.

"""

from __future__ import annotations

from dataclasses import dataclass, field

from sr.errors import BuildWarning
from sr.expr.values import quote
from sr.fonts.face import Face
from sr.units import TOLERANCE, round_points

__all__ = [
    "BREAK",
    "LEADING",
    "NEWLINE",
    "Metrics",
    "Wrapped",
    "missing_glyph",
    "quote_char",
    "wrap",
]

# doc/layout.md#text-metrics: a constant multiplier,
# not the face's own line spacing.
LEADING = 1.2

# The only mandatory break.  U+000D, U+000B, U+000C, U+0085 and U+2028
# are ordinary characters, so a CRLF leaves its carriage return at the
# end of the line before it.
NEWLINE = "\n"

# The only two break opportunities.  Not a hyphen, a soft hyphen,
# a solidus or an em dash, and not a no-break space, a zero-width space,
# an em space, a figure space or an ideographic space.  Nothing happens
# between CJK characters either.
#
# That this set has two members rather than a Unicode line-breaking
# algorithm is a choice, and its cost is that a language which does not
# separate its words with spaces does not wrap at its own boundaries.
BREAK = " \t"

# How Go's `%q` spells a rune, which is how the missing-glyph warning
# names the character it is about.  Everything not here and not printable
# is escaped by codepoint.
GO_ESCAPES = {
    0x07: r"\a",
    0x08: r"\b",
    0x09: r"\t",
    0x0A: r"\n",
    0x0B: r"\v",
    0x0C: r"\f",
    0x0D: r"\r",
    0x27: r"\'",
    0x5C: "\\\\",
}

# Below this a character is escaped as `\xNN`, above it as `\uNNNN` or
# `\UNNNNNNNN`.  Go's own boundary, since the warning is a rune quoted
# the way Go quotes one.
ASCII_LIMIT = 0x80
BMP_LIMIT = 0x10000


@dataclass(frozen=True)
class Metrics:
    """One face at one size: what a codepoint and a string measure.

    The size is an integer because a `font` node's is,
    and points are what everything above this works in.

    Attributes:
        face: The resolved face.
        size: The font size, in points.

    """

    face: Face
    size: int

    @property
    def leading(self) -> float:
        """Return the distance between two baselines, in points."""
        return round_points(self.size * LEADING)

    def advance(self, character: str) -> float:
        """Return what one codepoint advances the pen by, in points.

        Not rounded.  Rounding happens once per chunk and once per step
        of a walk, and a value rounded here as well would be rounded twice.

        Args:
            character: One codepoint.

        """
        return self.face.advance(ord(character)) * self.size / self.face.units_per_em

    def width(self, text: str) -> float:
        """Return what a string measures, rounded once.

        This is a chunk's own width, and what `align="right"`
        and `align="center"` measure a finished line by.

        Args:
            text: The string, which may be empty.

        """
        return round_points(sum(self.advance(one) for one in text))

    def missing(self, text: str) -> tuple[str, ...]:
        """Return the characters the face has no glyph for, first use first.

        doc/template.md#missing-glyphs makes each of these a warning
        and not an error: the engine measures and the renderer draws
        ``.notdef``, so the metrics are unaffected and nothing shifts.

        Args:
            text: The string being set.

        """
        found: dict[str, None] = {}
        for character in text:
            if character not in found and not self.face.covers(ord(character)):
                found[character] = None
        return tuple(found)


@dataclass
class Wrapped:
    """What wrapping a string produced.

    Attributes:
        lines: The lines, in order.  Never empty: text that is empty
            or reduces to nothing is one empty line rather than none.
        missing: The characters the face had no glyph for,
            first use first, for the caller to raise warnings from.

    """

    lines: list[str] = field(default_factory=list)
    missing: tuple[str, ...] = ()


def wrap(text: str, width: float, metrics: Metrics) -> Wrapped:
    """Return the lines a string breaks into inside a box.

    Args:
        text: What the element finally holds.
            ``expr``, ``text`` or ``data`` resolved, and ``format`` applied.
             Where a character came from makes no difference
             to how it is treated.
        width: The box's width in points, already rounded.
        metrics: The face and size to measure with.

    """
    paragraphs = text.split(NEWLINE)
    lines: list[str] = []
    for paragraph in paragraphs:
        lines.extend(paragraph_lines(paragraph, width, metrics))
    # The missing characters are those of the paragraphs rather than of
    # the lines.  A mandatory break is consumed before anything is
    # measured, so U+000A is never looked for in the face; everything
    # else in the string is, whether or not trimming later removes it.
    return Wrapped(lines, metrics.missing("".join(paragraphs)))


def paragraph_lines(text: str, width: float, metrics: Metrics) -> list[str]:
    """Return the lines one paragraph breaks into.

    A paragraph is what lies between two mandatory breaks, and an empty
    one still makes a line -- which is why text beginning with a newline
    starts with an empty line and a run of newlines gives a run of them.

    Args:
        text: The paragraph, with no U+000A in it.
        width: The box's width in points, already rounded.
        metrics: The face and size to measure with.

    """
    if width <= 0:
        # doc/layout.md#a-box-of-zero-width: zero is the width a box has
        # when nothing determined one, and one codepoint per line is not
        # a useful reading of that.  The paragraph is still the last line
        # of itself, so it is still trimmed.
        return [trim(text)]
    lines: list[str] = []
    line = ""
    measured = 0.0
    for chunk in chunks(text):
        if line:
            together = round_points(measured + metrics.width(chunk))
            if fits(together, width):
                line += chunk
                measured = together
                continue
            # The chunk does not fit beside what is there, so the
            # line ends at the break opportunity it ended on, and
            # its trailing run of spaces and tabs went with the break.
            lines.append(trim(line))
            line, measured = "", 0.0
        rest = chunk
        while True:
            taken = walk(rest, width, metrics)
            if taken == rest:
                # The walk consumed the chunk, so the chunk starts
                # the line, contributing its own width rounded once.
                # The walked total is not that number and is forgotten.
                line, measured = rest, metrics.width(rest)
                break
            lines.append(taken)
            rest = rest[len(taken) :]
    lines.append(trim(line))
    return lines


def chunks(text: str) -> list[str]:
    """Return a paragraph as chunks, in order.

    A chunk is a maximal run of characters that are neither space nor tab
    together with the run of spaces and tabs immediately after it.
    Either run may be empty: a paragraph beginning with whitespace opens
    with a chunk that is an empty word and the spaces after it, so that
    the whitespace lands at the start of the first line.

    Args:
        text: The paragraph.

    """
    found: list[str] = []
    at = 0
    while at < len(text):
        start = at
        while at < len(text) and text[at] not in BREAK:
            at += 1
        while at < len(text) and text[at] in BREAK:
            at += 1
        found.append(text[start:at])
    return found


def walk(chunk: str, limit: float, metrics: Metrics) -> str:
    """Return how much of a chunk fits on a line of its own.

    The walk is both the test and the cut.  It starts at zero, adds each
    codepoint's advance, rounds the running total at every one, and stops
    at the last codepoint that keeps the total within the limit -- except
    that it always takes its first codepoint, whether or not that one
    fits.  That exception is what keeps wrapping terminating, and it is
    why content overflows a box narrower than a single character rather
    than wrapping forever.

    Consuming the whole chunk means the chunk starts the line; stopping
    short means what was taken is a line of its own.  Those are not
    the same test: in a box narrower than one codepoint the forced first
    codepoint may be the whole of what remains, so the walk consumed the
    chunk while nothing about it fits.

    The unit is the codepoint -- not the byte, not the UTF-16 code unit,
    and not the grapheme cluster.  A cut may therefore fall between
    a letter and a combining mark that follows it.

    Args:
        chunk: What is being walked; never empty.
        limit: The box's rounded width, without the tolerance.
        metrics: The face and size to measure with.

    """
    total = 0.0
    for at, character in enumerate(chunk):
        total = round_points(total + metrics.advance(character))
        if at and not fits(total, limit):
            return chunk[:at]
    return chunk


def fits(extent: float, limit: float) -> bool:
    """Report whether a width is within a box, to the usual tolerance.

    The comparison happens in binary64 and that is normative,
    not an implementation detail: a limit of 11.123 plus 0.001 is
    11.123999999999999, which a walked total of 11.124 is outside of.
    An engine comparing exact decimals here wraps differently.

    Args:
        extent: The accumulated width, already rounded.
        limit: The box's width, already rounded.

    """
    return extent <= limit + TOLERANCE


def trim(line: str) -> str:
    """Return a line without the run of spaces and tabs it ends with.

    Trimming stops at the first character that is neither space nor tab,
    so a trailing no-break space stays and takes any whitespace before it
    with it.  Leading whitespace is untouched: it is at the start of a line
    rather than at a break, so nothing consumed it.

    Args:
        line: The line as it was built.

    """
    return line.rstrip(BREAK)


def quote_char(character: str) -> str:
    """Return a character as the missing-glyph warning names it.

    Go's `%q` for a rune, which is the spelling the reference engine's
    warnings use and therefore the spelling a printout carries.
    A printable character appears as itself; everything else is escaped.

    Args:
        character: One codepoint.

    """
    code = ord(character)
    escape = GO_ESCAPES.get(code)
    if escape is not None:
        return f"'{escape}'"
    if character.isprintable() and code >= 0x20:
        return f"'{character}'"
    if code < ASCII_LIMIT:
        return f"'\\x{code:02x}'"
    if code < BMP_LIMIT:
        return f"'\\u{code:04x}'"
    return f"'\\U{code:08x}'"


def missing_glyph(font: str, character: str, node: str | None = None) -> BuildWarning:
    """Return the warning for a character the resolved font has not got.

    The wording is the printout's, not a diagnostic's: a `glyph` warning
    goes into the header and travels with the document, so it is written
    once here rather than at each of the places that measures text.

    Args:
        font: The name the template gave the `font` node.
        character: The character that has no glyph.
        node: The node path it was met at, where the caller knows one.

    """
    return BuildWarning(
        "glyph",
        f"the font {quote(font)} has no glyph for {quote_char(character)}, "
        "so an empty box is drawn in its place",
        node=node,
    )
