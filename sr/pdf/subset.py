"""Fonts in the file: one Type0 font per face, carrying a subset of it.

doc/render.md#fonts-in-the-file is the contract, and
four of its rules are the shape of this module.

* **One font per face, not one per entry.**  Entries that read
  the same bytes at the same face index share one :class:`Embedded`,
  whatever their names and sizes, and whether they reached the face
  by file, by data or through the host.  Size is given where the text
  is shown, and underline is a rule drawn beside it, so nothing an
  entry adds belongs in the font.
* **A code per character, not per glyph.**  Code 0 is the face's
  empty glyph.  Every other character the document sets in the face
  gets a code of its own, in code point order, so two characters
  that share a glyph get two codes, and one the face lacks gets a code
  that draws glyph 0.  The text then reads back out of the file as the
  printout said it, through a `ToUnicode` map from each code to its
  character.  That is why the codes are known only once every page
  has been read: :meth:`Embedded.freeze` assigns them, between
  collecting the characters and drawing them.
* **Widths are the advances the engine measured**, in thousandths
  of an em to three decimals, so the pen inside a shown string
  lands where the printout measured it.
* **The program is a subset**: `glyf`, `loca`, `head`, `hhea`, `hmtx`
  and `maxp` rebuilt for the glyphs the codes draw, and the hinting
  programs where the face has them.  `fontTools` does the subsetting
  and the closure over composite glyphs, renumbering what it keeps;
  the codes reach the renumbered glyphs through a `CIDToGIDMap`.
  The layout tables are dropped before subsetting, since nothing is
  closed over them and reading them is most of what subsetting costs.
  Every other table is deleted after subsetting and not before,
  since the subsetter reads a few of them on the way.

A face with PostScript outlines is refused, as doc/render.md#boundaries
decides, and so is one with no outlines at all.

"""

from __future__ import annotations

import hashlib
import io
import struct
from dataclasses import dataclass, field
from typing import Any

from fontTools import subset
from fontTools.ttLib import TTFont

from sr.errors import RenderError
from sr.fonts.face import Face, name_record
from sr.pdf.objects import Name, Ref, Stream, Writer
from sr.units import round_points

__all__ = [
    "CODE_LIMIT",
    "KEPT_TABLES",
    "LAYOUT_TABLES",
    "Embedded",
    "postscript_name",
    "subset_tag",
]

# The tables the embedded program carries.  The first six
# are what a CIDFontType2 program needs; the last three
# are the hinting programs, kept where the face has them.
KEPT_TABLES = (
    "glyf",
    "loca",
    "head",
    "hhea",
    "hmtx",
    "maxp",
    "cvt ",
    "fpgm",
    "prep",
)

# The tables the subsetter is told to drop before it starts.
# These are the glyph substitution, positioning, and kerning tables,
# and none of them reaches the program.  The subsetter would otherwise
# read each of them whole to prune it, and a large face's GSUB and GPOS
# take longer to read than everything else in a render.
LAYOUT_TABLES = (
    "GSUB",
    "GPOS",
    "GDEF",
    "BASE",
    "JSTF",
    "MATH",
    "kern",
    "kerx",
    "morx",
    "mort",
)

# A code is two bytes under Identity-H, and code 0 is the empty glyph's.
CODE_LIMIT = 0xFFFF

# How many `bfchar` lines a ToUnicode map may carry in one block.
BFCHAR_BLOCK = 100

# What a PostScript name may hold: the printable ASCII range,
# less the characters that delimit a token.
POSTSCRIPT_REFUSED = frozenset("[](){}<>/%")

# The `Flags` bits a font descriptor sets: fixed pitch, symbolic,
# and italic.  A CID-keyed font is symbolic, since its codes
# are not a standard Latin encoding.
FIXED_PITCH = 1 << 0
SYMBOLIC = 1 << 2
ITALIC = 1 << 6

# The stem widths a descriptor states, by weight.  Readers use the
# value only to synthesize a face that is missing, and an embedded
# face never is, so two figures are as good as a measurement.
STEM_REGULAR = 80
STEM_BOLD = 120

# The ToUnicode map's fixed opening and closing.
CMAP_OPENING = """\
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
"""
CMAP_CLOSING = """\
endcmap
CMapName currentdict /CMapResource defineresource pop
end
end
"""


@dataclass
class Embedded:
    """One face, as the file carries it.

    Built when the fonts are read, given characters while the pages
    are read, frozen before they are drawn, and written last.

    Attributes:
        face: The face, as the engine measured with it.
        data: The whole font file the face is in.
        index: The face's position in that file.
        resource: The name pages show it under: ``F1``, ``F2``.
        characters: What the document sets in it, under any entry.
        codes: Each character's code, once frozen.
        operands: Each character's code as the four hexadecimal digits
            a string operand spells it with, once frozen.

    """

    face: Face
    data: bytes
    index: int
    resource: str
    characters: set[str] = field(default_factory=set)
    codes: dict[str, int] = field(default_factory=dict)
    operands: dict[str, str] = field(default_factory=dict)

    def take(self, text: str) -> None:
        """Note the characters a line sets in this face.

        Args:
            text: The line.

        """
        self.characters.update(text)

    def freeze(self) -> None:
        """Assign the codes, in code point order, from 1.

        Raises:
            RenderError: More characters than two bytes can number.

        """
        ordered = sorted(self.characters, key=ord)
        if len(ordered) > CODE_LIMIT:
            raise RenderError(
                f"{self.face.origin}: the document sets {len(ordered)} "
                f"characters in one face, and a font numbers {CODE_LIMIT}"
            )
        numbered = enumerate(ordered, start=1)
        self.codes = {character: code for code, character in numbered}
        self.operands = {
            character: f"{code:04X}" for character, code in self.codes.items()
        }

    def encode(self, text: str) -> str:
        """Return a string of codes, as a hexadecimal string operand.

        Args:
            text: Characters this face was given, after it was frozen.

        """
        return "<" + "".join(map(self.operands.__getitem__, text)) + ">"

    def ascender(self, size: float) -> float:
        """Return `hhea`'s ascender at a size, above the baseline.

        Args:
            size: The font size, in points.

        """
        return abs(self.face.ascent) * size / self.face.units_per_em

    def descender(self, size: float) -> float:
        """Return `hhea`'s descender at a size, counted down from the baseline.

        A face that stores its descender as a magnitude rather than
        as a negative number means the same distance, so the sign
        it used is not the one read.

        Args:
            size: The font size, in points.

        """
        return abs(self.face.descent) * size / self.face.units_per_em

    def underline(self, size: float) -> tuple[float, float]:
        """Return where an underline's top sits, and how thick it is.

        From the `post` table, scaled to the size.  The position
        is measured down from the baseline, which is the opposite
        of the table's own sign: `post` counts a position below
        the baseline as negative.  A face that states neither,
        or has no `post` table, gets doc/render.md#underline's
        tenth of an em down and twentieth of an em thick.

        Args:
            size: The font size, in points.

        """
        font = self.face.font
        em = self.face.units_per_em
        position = thickness = 0
        if "post" in font:
            post = font["post"]
            position = int(post.underlinePosition)
            thickness = int(post.underlineThickness)
        if not position and not thickness:
            return size / 10, size / 20
        return -position * size / em, thickness * size / em

    def glyph(self, character: str) -> str:
        """Return the glyph a character draws: its own, or glyph 0.

        Args:
            character: One codepoint.

        """
        return self.face.glyph(ord(character)) or self.face.notdef

    def width(self, character: str | None) -> float:
        """Return a character's advance in thousandths of an em.

        Args:
            character: One codepoint, or ``None`` for code 0.

        """
        units = (
            self.face.advance(ord(character))
            if character is not None
            else int(self.face.metrics[self.face.notdef][0])
        )
        return round_points(units * 1000 / self.face.units_per_em)

    def program(self) -> tuple[bytes, list[int]]:
        """Return the subset program, and the glyph each code draws.

        Raises:
            RenderError: The font will not subset.

        """
        ordered = sorted(self.codes, key=self.codes.__getitem__)
        try:
            font = TTFont(
                io.BytesIO(self.data),
                fontNumber=self.index,
                recalcTimestamp=False,
            )
            order = font.getGlyphOrder()
            wanted = [order[0], *(self.glyph(one) for one in ordered)]
            subsetter = subset.Subsetter(subset_options())
            subsetter.populate(glyphs=wanted)
            subsetter.subset(font)
            kept = enumerate(font.getGlyphOrder())
            renumbered = {glyph: index for index, glyph in kept}
            gids = [renumbered[one] for one in wanted]
            for tag in list(font.keys()):
                if tag != "GlyphOrder" and tag not in KEPT_TABLES:
                    del font[tag]
            out = io.BytesIO()
            font.save(out, reorderTables=True)
        except Exception as refused:
            raise RenderError(
                f"{self.face.origin}: the face will not subset: {refused}"
            ) from None
        return out.getvalue(), gids

    def write(self, writer: Writer) -> Ref:
        """Write the font's objects, and return the Type0 font's reference.

        Args:
            writer: The file's object table.

        """
        program, gids = self.program()
        postscript = postscript_name(self.face)
        base = Name(f"{subset_tag(postscript, program)}+{postscript}")
        ordered: list[str | None] = [
            None,
            *sorted(self.codes, key=self.codes.__getitem__),
        ]
        widths = [self.width(one) for one in ordered]
        font_file = writer.add(
            Stream({"Length1": len(program)}, program),
        )
        descriptor = writer.add(self.descriptor(base, font_file))
        cid_map = writer.add(
            Stream({}, struct.pack(f">{len(gids)}H", *gids)),
        )
        descendant = writer.add(
            {
                "Type": Name("Font"),
                "Subtype": Name("CIDFontType2"),
                "BaseFont": base,
                "CIDSystemInfo": {
                    "Registry": b"Adobe",
                    "Ordering": b"Identity",
                    "Supplement": 0,
                },
                "FontDescriptor": descriptor,
                "W": [0, widths],
                "CIDToGIDMap": cid_map,
            }
        )
        to_unicode = writer.add(Stream({}, self.to_unicode()))
        return writer.add(
            {
                "Type": Name("Font"),
                "Subtype": Name("Type0"),
                "BaseFont": base,
                "Encoding": Name("Identity-H"),
                "DescendantFonts": [descendant],
                "ToUnicode": to_unicode,
            }
        )

    def descriptor(self, base: Name, font_file: Ref) -> dict[str, Any]:
        """Return the font descriptor.

        `Ascent` and `Descent` are `hhea`'s, the pair the baseline was
        measured from, signed as PDF signs them whatever the face did.

        Args:
            base: The subset's name, tag included.
            font_file: The embedded program.

        """
        font = self.face.font
        scale = 1000 / self.face.units_per_em
        head = font["head"]
        angle = float(font["post"].italicAngle) if "post" in font else 0.0
        fixed = "post" in font and bool(font["post"].isFixedPitch)
        ascent = self.ascender(1000)
        cap = ascent
        if "OS/2" in font:
            table = font["OS/2"]
            capital = int(getattr(table, "sCapHeight", 0) or 0)
            if table.version >= 2 and capital:
                cap = capital * scale
        flags = SYMBOLIC
        if fixed:
            flags |= FIXED_PITCH
        if angle or self.face.italic:
            flags |= ITALIC
        return {
            "Type": Name("FontDescriptor"),
            "FontName": base,
            "Flags": flags,
            "FontBBox": [
                int(head.xMin) * scale,
                int(head.yMin) * scale,
                int(head.xMax) * scale,
                int(head.yMax) * scale,
            ],
            "ItalicAngle": angle,
            "Ascent": ascent,
            "Descent": -self.descender(1000),
            "CapHeight": cap,
            "StemV": STEM_BOLD if self.face.bold else STEM_REGULAR,
            "FontFile2": font_file,
        }

    def to_unicode(self) -> bytes:
        """Return the map from each code back to its character."""
        lines = [CMAP_OPENING]
        pairs = sorted(self.codes.items(), key=lambda pair: pair[1])
        for start in range(0, len(pairs), BFCHAR_BLOCK):
            block = pairs[start : start + BFCHAR_BLOCK]
            lines.append(f"{len(block)} beginbfchar\n")
            for character, code in block:
                unit = character.encode("utf-16-be", "surrogatepass").hex()
                lines.append(f"<{code:04X}> <{unit.upper()}>\n")
            lines.append("endbfchar\n")
        lines.append(CMAP_CLOSING)
        return "".join(lines).encode("ascii")


def subset_options() -> subset.Options:
    """Return what the subsetter is told.

    Glyphs are named rather than reached through a character map,
    so no layout table is consulted and nothing is closed over
    but composite glyphs, and the layout tables are dropped unread.
    Hinting is kept, and the timestamp is left as the face has it,
    which keeps the program a function of the face and the glyphs.

    """
    options = subset.Options()
    options.drop_tables += list(LAYOUT_TABLES)
    options.layout_features = []
    options.layout_closure = False
    options.name_IDs = []
    options.name_languages = []
    options.glyph_names = False
    options.notdef_glyph = True
    options.notdef_outline = True
    options.hinting = True
    options.legacy_kern = False
    options.recalc_timestamp = False
    options.prune_unicode_ranges = False
    return options


def postscript_name(face: Face) -> str:
    """Return the face's PostScript name, as a `BaseFont` carries it.

    Name ID 6, with anything a name token cannot hold taken out;
    the family's name where the face has no ID 6.

    Args:
        face: The face.

    """
    for candidate in (name_record(face.font, (6,)), face.family):
        kept = "".join(one for one in candidate if nameable(one))
        if kept:
            return kept
    return "Font"


def nameable(character: str) -> bool:
    """Report whether a PostScript name may hold a character as itself.

    Args:
        character: One codepoint.

    """
    printable = 0x21 <= ord(character) <= 0x7E
    return printable and character not in POSTSCRIPT_REFUSED


def subset_tag(postscript: str, program: bytes) -> str:
    """Return the six upper-case letters in front of a subset's name.

    Derived from the face's name and the subset program,
    which depends on the face and on the glyphs it holds:
    stable across runs, and different for two documents
    that carry different subsets of one face.

    Args:
        postscript: The face's PostScript name.
        program: The subset program.

    """
    digest = hashlib.sha256(postscript.encode("ascii") + program).digest()
    return "".join(chr(ord("A") + byte % 26) for byte in digest[:6])
