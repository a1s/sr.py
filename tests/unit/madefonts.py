"""Small fonts built at test time, for what the committed faces cannot show.

The two Go faces in `example/fonts/` have no composite glyphs,
no PostScript outlines, and a `post` table that states an underline,
so none of them reaches the parts of the renderer that handle those.
:func:`made_font` builds a face that does, from a few rectangles,
which keeps every binary the tests read visible in the source.

The face holds five glyphs, each a box of its own size
so that a test can tell them apart by their outlines:

=============  =========  ==========================================
``.notdef``    glyph 0    a 500 by 700 box
``space``      U+0020     no outline, and U+00A0 too
``A``          U+0041     a 600 by 700 box
``acute``      U+00B4     a 200 by 100 box, 750 up
``Aacute``     U+00C1     ``A`` and ``acute``, as a composite glyph
=============  =========  ==========================================

"""

from __future__ import annotations

import io

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen

__all__ = ["ADVANCES", "made_font"]

# Each glyph's advance, in units of a 1000-unit em.
ADVANCES = {
    ".notdef": 500,
    "space": 250,
    "A": 600,
    "acute": 300,
    "Aacute": 600,
}

# Each glyph's box: left, bottom, right, top.
BOXES = {
    ".notdef": (50, 0, 550, 700),
    "A": (0, 0, 600, 700),
    "acute": (200, 750, 400, 850),
}

# A no-break space shares the space's glyph, as it does in most faces
# though not in the Go ones.
CHARACTERS = {
    0x20: "space",
    0x41: "A",
    0xA0: "space",
    0xB4: "acute",
    0xC1: "Aacute",
}


def made_font(
    *,
    cff: bool = False,
    descender: int = -200,
    underline: tuple[int, int] = (-100, 50),
    name: str = "Made",
) -> bytes:
    """Return the bytes of a small face.

    Args:
        cff: Whether its outlines are PostScript, in a `CFF ` table.
        descender: `hhea`'s descender, which a face may store
            as a magnitude rather than as a negative number.
        underline: `post`'s underline position and thickness.
        name: Its family name, and its PostScript name.

    """
    builder = FontBuilder(1000, isTTF=not cff)
    builder.setupGlyphOrder(list(ADVANCES))
    builder.setupCharacterMap(CHARACTERS)
    if cff:
        charstrings = {}
        for glyph in ADVANCES:
            pen = T2CharStringPen(ADVANCES[glyph], None)
            for part in parts_of(glyph):
                box(pen, BOXES[part])
            charstrings[glyph] = pen.getCharString()
        builder.setupCFF(name, {"FullName": name}, charstrings, {})
    else:
        glyphs: dict[str, object] = {}
        for glyph in ADVANCES:
            # The glyphs drawn so far, which a component has to name.
            pen = TTGlyphPen(glyphs)
            if glyph == "Aacute":
                pen.addComponent("A", (1, 0, 0, 1, 0, 0))
                pen.addComponent("acute", (1, 0, 0, 1, 0, 0))
            elif glyph in BOXES:
                box(pen, BOXES[glyph])
            glyphs[glyph] = pen.glyph()
        builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(
        {glyph: (width, 0) for glyph, width in ADVANCES.items()}
    )
    builder.setupHorizontalHeader(ascent=800, descent=descender)
    builder.setupNameTable(
        {"familyName": name, "styleName": "Regular", "psName": name},
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    position, thickness = underline
    builder.setupPost(underlinePosition=position, underlineThickness=thickness)
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


def parts_of(glyph: str) -> tuple[str, ...]:
    """Return the boxes a glyph is drawn from.

    Args:
        glyph: The glyph's name.

    """
    if glyph == "Aacute":
        return ("A", "acute")
    return (glyph,) if glyph in BOXES else ()


def box(
    pen: TTGlyphPen | T2CharStringPen,
    edges: tuple[int, int, int, int],
) -> None:
    """Draw one box, clockwise as TrueType winds an outer contour.

    Args:
        pen: The pen.
        edges: Its left, bottom, right and top.

    """
    left, bottom, right, top = edges
    pen.moveTo((left, bottom))
    pen.lineTo((left, top))
    pen.lineTo((right, top))
    pen.lineTo((right, bottom))
    pen.closePath()
