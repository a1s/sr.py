"""One face: the tables a report engine reads, and nothing else.

A face here is a family name, a style, and a way to ask what a codepoint
advances the pen by.  That is the whole of what measurement needs, and
keeping it to that is what lets one class stand for a face inside a
collection, a face read out of a ``data`` blob, and a face on the host.

Four rules from doc/template.md#host-enumeration live here rather than
in the enumerator, because each is a property of one face:

* **Family is name ID 16 where the font has one and name ID 1 otherwise.**
  The typographic family answers "what family is this", while the legacy
  one splits a large family up to fit a four-style model -- which is why
  `Arial Narrow` calls itself `Arial` under ID 16 and is therefore not
  a family a template can ask for by name.
* **Style comes from the style bits, in a fixed order of sources.**
  ``OS/2.fsSelection`` first, then ``head.macStyle``.  The first table
  the face has decides; a later source is not consulted to break a
  disagreement.  The third source that section names, the subfamily
  string, is for a face with neither table and is unreachable here,
  because ``head`` is required of every face.
* **A file is classified before it is parsed**, from its first bytes
  rather than from its name.  :func:`sniff` is what says whether a file
  is an sfnt at all, so that a bitmap face is skipped as unsupported
  while a file claiming to be an sfnt and failing to parse is a warning.
  One unsupported format cannot be told from the first bytes and is named
  from its tables instead: see :data:`BITMAP_SFNT`.  Which of the two
  a refusal is travels as :class:`UnsupportedFont`, since by the time
  the enumerator has the message the first bytes are no longer in hand.
* **A collection is several faces**, each addressed by index.

The advance of a codepoint the face does not have is ``.notdef``'s,
which is glyph 0.  doc/layout.md#text-metrics makes that a measurement
rule rather than a fallback: text keeps its metrics so that nothing
shifts, and the missing character is reported as a warning instead.

"""

from __future__ import annotations

import io
import logging
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont

from sr.errors import FontError

# `fontTools` logs about faces it finds odd -- a `head.created` before
# the epoch is the common one, and a stock Windows has several.  With no
# handler anywhere on the chain, `logging` prints those to standard error,
# which would put a library's opinion of a font in the middle of this
# engine's own report.  A null handler stops that without setting a level,
# so an application that configures logging still sees every one of them.
logging.getLogger("fontTools").addHandler(logging.NullHandler())

__all__ = [
    "CMAP_PREFERENCE",
    "LATIN1",
    "REQUIRED_TABLES",
    "SFNT_FORMATS",
    "Face",
    "Origin",
    "UnsupportedFont",
    "faces_in",
    "near",
    "open_bytes",
    "open_face",
    "read_faces",
    "sniff",
]


class UnsupportedFont(FontError):
    """A font this engine does not read, as opposed to one that is broken.

    doc/template.md#host-enumeration draws that line and spells the two
    sides differently: a format this engine does not read is classified
    and **skipped**, while a file that presents itself as an sfnt and
    then fails to parse is a **warning**.  A reader sorting the diagnostics
    of a whole machine needs to know which pile a line belongs in, and for
    every other unsupported format :func:`sniff` says so from the first
    bytes.  :data:`BITMAP_SFNT` cannot be told that way, so it says so
    by its class instead.

    """


# The sfnt version tags, as the first four bytes of a file.
# `ttcf` is a collection; the rest each hold one face.
SFNT_FORMATS = {
    b"\x00\x01\x00\x00": "truetype",
    b"true": "truetype",
    b"typ1": "truetype",
    b"OTTO": "opentype",
    b"ttcf": "collection",
}

# Formats this engine does not read, each named so that an enumeration
# diagnostic says what the file is rather than only what it is not.
# doc/template.md#host-enumeration asks for that: a file that cannot
# be used is classified and skipped, never dropped silently.
#
# The last entry is the one that needs saying out loud.  A Macintosh
# datafork font and a Windows icon open with the same two bytes, and
# `.dfont` files sit in the very directories the macOS enumeration walks.
# Neither is a face this engine can read, so one description covers both
# rather than a sniffer guessing between them.
OTHER_FORMATS = (
    (b"wOFF", "a WOFF web font"),
    (b"wOF2", "a WOFF2 web font"),
    (b"%!PS", "a PostScript Type 1 font"),
    (b"%!Fo", "a PostScript Type 1 font"),
    (b"\x80\x01", "a PostScript Type 1 font"),
    (b"\x01\x00\x04", "a bare CFF font"),
    (b"MZ", "a Windows bitmap font"),
    (b"\x01fcp", "an X11 bitmap font"),
    (b"STARTFONT", "a BDF bitmap font"),
    # Gzip, which on a machine with X11 fonts on it is nearly always
    # `.pcf.gz`: Debian's `xfonts-base` puts 480 of them under
    # /usr/share/fonts/X11, and Alpine has 50 without that package.
    # Naming the compression is the most that can be said without
    # decompressing, and it beats "not an sfnt font" on every line
    # of an enumeration report.
    (b"\x1f\x8b", "a gzip-compressed file"),
    (b"\x00\x00\x01\x00", "a Windows icon or a datafork font"),
)

# One more of those, which the first bytes cannot tell apart from a face
# this engine can use.  A bitmap-only sfnt carries its header as `bhed`
# rather than `head` -- the same twelve fields under another tag, which
# says the strikes are the whole of the font and there are no outlines
# to draw.  It is a valid font and the reference engine resolves one;
# this engine does not, because the renderer has nothing to draw from it,
# and a face that resolves and prints nothing is worse than a face
# that says why.  Refusing it is therefore deliberate, and the reason
# a description sits here rather than a missing-table message: `bhed`
# is not damage.  It is raised as an :class:`UnsupportedFont` for
# the same reason -- so that the enumerator files it with the formats
# :data:`OTHER_FORMATS` names rather than with the broken files.
BITMAP_SFNT = "a bitmap-only sfnt font, with `bhed` in place of `head`"

# What a face must have before it can be measured with.  `head` gives
# the em, `hhea` and `hmtx` the advances, `cmap` the characters and `name`
# the family.  A face missing one of them is not a face this engine can use,
# and saying which one is missing beats whatever the parser would raise
# three calls later.
REQUIRED_TABLES = ("head", "hhea", "hmtx", "cmap", "name")

# The most faces a collection may claim, per doc/template.md#font.
#
# A bound is needed because the count is four bytes of a file that may be
# damaged, and every face it claims is one parse: without this, a `ttcf`
# header whose count reads as 4294967040 enumerates for the rest of the
# afternoon.  The number is the reference engine's and is a limit on what
# is admitted, not on what exists -- the largest collections shipped with
# an operating system hold a few dozen faces.
COLLECTION_LIMIT = 2048

# The `cmap` subtables to look a codepoint up in, best first.
#
# The Unicode tables come first, ordered so that a full repertoire beats
# a BMP-only one and an astral codepoint is found where the face has it.
# (3, 0) is the Microsoft symbol encoding and is last: a symbol face such
# as Symbol or Wingdings has no Unicode table at all, and without this
# every character in one would measure as `.notdef`.  Which subtable
# to read is a choice the specification does not make, and this is it.
CMAP_PREFERENCE = (
    (3, 10),
    (0, 6),
    (0, 4),
    (3, 1),
    (0, 3),
    (0, 2),
    (0, 1),
    (0, 0),
    (3, 0),
)

# Where a symbol subtable puts the characters a text font puts at 0x20.
SYMBOL_BASE = 0xF000

# The name records a family and a subfamily are read from, in order.
FAMILY_IDS = (16, 1)
SUBFAMILY_IDS = (17, 2)

# The platform and language a name record is preferred from: Windows
# English, then any Windows record, then Macintosh English, then anything.
# A name is only ever shown or matched against, so this is about which
# spelling reads best rather than about which one is right.
NAME_PREFERENCE = ((3, 1, 0x409), (3, None, None), (1, 0, 0), (None, None, None))

# The range doc/template.md#the-substitute-face checks a monospaced face
# over.  Beyond it a genuinely monospaced face carries characters whose
# advances are legitimately not the common one.
LATIN1 = range(0x100)

# `OS/2.fsSelection`, the bits that name a style.
ITALIC_BIT = 1 << 0
BOLD_BIT = 1 << 5
OBLIQUE_BIT = 1 << 9

# `head.macStyle`, the same two.
MAC_BOLD_BIT = 1 << 0
MAC_ITALIC_BIT = 1 << 1


@dataclass(frozen=True)
class Origin:
    """Where a face was read from, as the printout records it.

    Exactly one of ``path`` and ``data`` is set.  The pair is what
    doc/printout.md#fonts spells as ``resolvedFile`` or ``resolvedData``,
    and ``index`` is its ``resolvedIndex``.

    Attributes:
        path: The file, where the face came from one.
        data: The name of the ``data`` node, where it came from a blob.
        index: The face's position within a collection;
            0 for a file holding one face.

    """

    path: Path | None = None
    data: str | None = None
    index: int = 0

    def shown(self) -> str:
        """Name the face the way a report and a warning spell it.

        Forward slashes on every platform, and relative to the working
        directory where the file is under it.  The engine holds a `font
        file=` as an absolute path, because a template has to name the
        same file whatever directory it is read from; neither a reader
        nor the printout wants that spelling, and a face found on the
        host is somewhere else entirely and stays absolute.

        The index is shown only when it is not zero, because a file
        holding one face has nothing to disambiguate and `face 0` after
        every path would be noise on every line of the font table.

        """
        if self.path is None:
            where = f"data {self.data}"
        else:
            where = near(self.path).as_posix()
        return where if self.index == 0 else f"{where} face {self.index}"

    def __str__(self) -> str:
        """Name the face as :meth:`shown` does."""
        return self.shown()


def near(path: Path) -> Path:
    """Return a path relative to the working directory, where it is under it.

    Args:
        path: The file, as the engine holds it.

    """
    try:
        return path.relative_to(Path.cwd())
    except (OSError, ValueError):
        return path


def sniff(head: bytes) -> tuple[str | None, str]:
    """Return what a file's first bytes say it is.

    doc/template.md#host-enumeration forbids deciding this from
    the filename, because a face with the wrong extension would be
    dropped while a `.ttf` that is really a bitmap would be opened.

    Args:
        head: The start of the file; nine bytes are enough.

    Returns:
        The sfnt format's name and an empty description,
        or ``None`` and a description of what the file is instead.

    """
    if head[:4] in SFNT_FORMATS:
        return SFNT_FORMATS[head[:4]], ""
    for prefix, description in OTHER_FORMATS:
        if head.startswith(prefix):
            return None, description
    return None, "not an sfnt font"


def faces_in(data: bytes) -> int:
    """Return how many faces a font file holds.

    A collection says so in its header; anything else holds one face.

    Args:
        data: The file, or at least its first twelve bytes.

    Raises:
        FontError: The collection header is truncated,
            or claims more faces than doc/template.md#font admits.

    """
    if data[:4] != b"ttcf":
        return 1
    if len(data) < 12:
        raise FontError("truncated font collection header")
    count = int(struct.unpack(">I", data[8:12])[0])
    if count > COLLECTION_LIMIT:
        raise FontError(
            f"number of fonts ({count}) in collection exceed "
            f"implementation limit ({COLLECTION_LIMIT})"
        )
    return count


def open_face(path: Path, index: int = 0) -> Face:
    """Return one face from a file on disk.

    Args:
        path: The file.
        index: Which face, for a collection.

    Raises:
        FontError: The file is not there, is not an sfnt, or will not parse.

    """
    return build(read_bytes(path), Origin(path=path, index=index))


def open_bytes(data: bytes, name: str, index: int = 0) -> Face:
    """Return one face from the bytes of a ``data`` blob.

    Args:
        data: The font file's contents.
        name: The ``data`` node's name, for the printout and diagnostics.
        index: Which face, for a collection.

    Raises:
        FontError: The bytes are not an sfnt, or will not parse.

    """
    return build(data, Origin(data=name, index=index))


def read_faces(data: bytes, origin: Origin) -> Iterator[Face]:
    """Yield every face a font file holds, in collection order.

    Args:
        data: The whole file.
        origin: Where it came from; its ``index`` is ignored.

    Raises:
        FontError: The bytes are not an sfnt, or will not parse.

    """
    for index in range(faces_in(data)):
        yield build(data, Origin(path=origin.path, data=origin.data, index=index))


def read_bytes(path: Path) -> bytes:
    """Return a file's contents, as a :class:`FontError` where it will not read.

    Args:
        path: The file.

    Raises:
        FontError: The file could not be read.

    """
    try:
        return path.read_bytes()
    except OSError as refused:
        raise FontError(
            f"cannot read {near(path).as_posix()}: {refused.strerror or refused}"
        ) from None


def build(data: bytes, origin: Origin) -> Face:
    """Return the face at ``origin``'s index within a font file's bytes.

    Args:
        data: The whole file.
        origin: Where it came from, and which face is wanted.

    Raises:
        FontError: The bytes are not an sfnt, or will not parse.

    """
    # Every failure below is named the same way, by the one handler:
    # a diagnostic that does not say which file it is about cannot be acted
    # on, and the things that go wrong here are found at several depths.
    # So nothing inside names the origin, and nothing outside raises.
    try:
        kind, description = sniff(data[:12])
        if kind is None:
            raise UnsupportedFont(description)
        count = faces_in(data)
        if not 0 <= origin.index < count:
            one = "face" if count == 1 else "faces"
            raise FontError(f"the file holds {count} {one}")
        font = TTFont(io.BytesIO(data), fontNumber=origin.index, lazy=True)
        if "head" not in font and "bhed" in font:
            raise UnsupportedFont(BITMAP_SFNT)
        missing = [one for one in REQUIRED_TABLES if one not in font]
        if missing:
            raise FontError(f"no {missing[0]} table")
        return Face(font, origin)
    except Exception as refused:
        # Any parse failure is one failure, and whatever the font library
        # chose to raise is not a class the rest of this engine knows.
        # The one distinction that survives naming the origin is the one
        # the enumerator sorts on: a format this engine does not read is
        # not a file that would not parse.
        shape = UnsupportedFont if isinstance(refused, UnsupportedFont) else FontError
        raise shape(f"{origin}: {refused}") from None


class Face:
    """One face, and the metrics a report engine asks it for.

    Built through :func:`open_face` or :func:`open_bytes`, which is
    what turns a parse failure into a :class:`FontError` naming the
    file instead of whatever the font library raises.

    Advances are cached per codepoint.  That cache is the whole of this
    class' mutable state, and it is here because wrapping walks the same
    few dozen codepoints over every line of every band.

    Attributes:
        origin: Where the face was read from.
        family: Name ID 16, or name ID 1 where there is no ID 16.
        subfamily: Name ID 17, or name ID 2.
        bold: What the style bits say about weight.
        italic: What they say about slant.
        units_per_em: The em, in font units.
        ascent: ``hhea.ascender``, in font units.
        descent: ``hhea.descender``, in font units, negative below the line.
        line_gap: ``hhea.lineGap``, in font units.

    """

    def __init__(self, font: Any, origin: Origin) -> None:
        """Read the tables that describe one face.

        Args:
            font: The parsed font, as the font library returns it.
            origin: Where it was read from.

        """
        self.font = font
        self.origin = origin
        self.family = name_record(font, FAMILY_IDS) or fallback_family(origin)
        self.subfamily = name_record(font, SUBFAMILY_IDS)
        self.bold, self.italic = style_bits(font)
        self.units_per_em = int(font["head"].unitsPerEm) or 1000
        hhea = font["hhea"]
        self.ascent = int(hhea.ascender)
        self.descent = int(hhea.descender)
        self.line_gap = int(hhea.lineGap)
        self.characters, self.symbol = character_map(font)
        self.notdef = str(font.getGlyphOrder()[0])
        self.metrics = font["hmtx"]
        self.widths: dict[int, int] = {}

    def glyph(self, codepoint: int) -> str | None:
        """Return the glyph a codepoint maps to, or ``None`` where it has none.

        Args:
            codepoint: The character, as an integer.

        """
        found = self.characters.get(codepoint)
        if found is None and self.symbol and codepoint < 0x100:
            found = self.characters.get(SYMBOL_BASE + codepoint)
        return found

    def covers(self, codepoint: int) -> bool:
        """Report whether the face has a glyph for a codepoint.

        Args:
            codepoint: The character, as an integer.

        """
        return self.glyph(codepoint) is not None

    def advance(self, codepoint: int) -> int:
        """Return what a codepoint advances the pen by, in font units.

        A codepoint the face does not have advances by ``.notdef``,
        which doc/layout.md#text-metrics makes a measurement rule rather
        than a fallback: an empty box is drawn and nothing shifts.

        Args:
            codepoint: The character, as an integer.

        """
        known = self.widths.get(codepoint)
        if known is not None:
            return known
        glyph = self.glyph(codepoint) or self.notdef
        try:
            width = int(self.metrics[glyph][0])
        except KeyError:
            width = int(self.metrics[self.notdef][0])
        self.widths[codepoint] = width
        return width

    def uneven_advances(self) -> tuple[int, ...]:
        """Return the Latin-1 advances that make this face not monospaced.

        The check of doc/template.md#the-substitute-face: every Latin-1
        character whose glyph advances the pen must advance it by the
        same amount.  A zero advance is not compared, since a combining
        mark correctly has none.

        Returns:
            The distinct non-zero advances, sorted, or an empty tuple
            where the face passes.

        """
        widths = {self.advance(one) for one in LATIN1}
        widths.discard(0)
        return tuple(sorted(widths)) if len(widths) > 1 else ()

    def __repr__(self) -> str:
        """Name the face the way a test failure should read it."""
        style = " ".join(
            word for word, on in (("bold", self.bold), ("italic", self.italic)) if on
        )
        return f"<Face {self.family!r} {style or 'regular'} from {self.origin}>"


# -- reading the tables -----------------------------------------------


def fallback_family(origin: Origin) -> str:
    """Return a name for a face whose ``name`` table gave none.

    Args:
        origin: Where the face was read from.

    """
    return origin.path.stem if origin.path is not None else origin.data or ""


def name_record(font: Any, wanted: tuple[int, ...]) -> str:
    """Return the first name record present, in the order asked for.

    Args:
        font: The parsed font.
        wanted: The name IDs to try, best first.

    """
    records = font["name"].names
    for name_id in wanted:
        for platform, encoding, language in NAME_PREFERENCE:
            for record in records:
                if record.nameID != name_id:
                    continue
                if platform is not None and record.platformID != platform:
                    continue
                if encoding is not None and record.platEncID != encoding:
                    continue
                if language is not None and record.langID != language:
                    continue
                try:
                    text = str(record).strip()
                except UnicodeDecodeError:
                    continue
                if text:
                    return text
    return ""


def style_bits(font: Any) -> tuple[bool, bool]:
    """Return a face's weight and slant, from the first source it has.

    The order is doc/template.md#host-enumeration's, and it ranks sources
    rather than answers: `head` is read only when `OS/2` is absent, and
    not when it disagrees with it.

    The third rank that section gives, the subfamily string, is not here
    and cannot be reached: it is for a face with neither style table, and
    `head` is required, so such a face is refused before this is called.
    The specification says so where it gives the rank.

    Args:
        font: The parsed font.

    """
    if "OS/2" in font:
        bits = int(font["OS/2"].fsSelection)
        return bool(bits & BOLD_BIT), bool(bits & (ITALIC_BIT | OBLIQUE_BIT))
    bits = int(font["head"].macStyle)
    return bool(bits & MAC_BOLD_BIT), bool(bits & MAC_ITALIC_BIT)


def character_map(font: Any) -> tuple[dict[int, str], bool]:
    """Return the character map to read, and whether it is a symbol table.

    Args:
        font: The parsed font.

    Raises:
        FontError: The face has no subtable this engine can read.

    """
    tables = {(one.platformID, one.platEncID): one for one in font["cmap"].tables}
    for key in CMAP_PREFERENCE:
        table = tables.get(key)
        if table is None:
            continue
        mapping = {
            (ord(char) if isinstance(char, str) else int(char)): str(glyph)
            for char, glyph in table.cmap.items()
        }
        if mapping:
            return mapping, key == (3, 0)
    raise FontError("no character map this engine can read")
