"""Fonts in the file, held to doc/render.md#fonts-in-the-file.

One Type0 font per face, a code per character in code point order,
the engine's advances as widths, a subset carrying the tables
the specification names, and a six-letter tag that is stable.
Each is read back out of a rendered file, through the objects
that carry it, and the embedded program through `fontTools`,
which can open it although it has no character map.

"""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from sr.errors import RenderError
from sr.pdf import render
from sr.pdf.subset import KEPT_TABLES
from sr.printout.model import Box, Text, Xref
from sr.units import round_points
from tests.pdfcheck import Parsed, entry, references, stream_of
from tests.unit.documents import BOLD, REGULAR, document, rendered, text
from tests.unit.documents import entry as font_entry
from tests.unit.madefonts import made_font

GO = TTFont(REGULAR)


@dataclass(frozen=True)
class Font:
    """One Type0 font, read back out of a file.

    Attributes:
        base: Its `BaseFont`.
        widths: Its `W` array, from code 0.
        gids: Its `CIDToGIDMap`, from code 0.
        unicode: Its `ToUnicode` map, code to character.
        descriptor: Its font descriptor's body.
        program: The embedded program, opened.

    """

    base: str
    widths: list[float]
    gids: list[int]
    unicode: dict[int, str]
    descriptor: bytes
    program: TTFont


def fonts_of(parsed: Parsed) -> list[Font]:
    """Return every Type0 font in a file, in object order.

    Args:
        parsed: The file's objects.

    """
    found = []
    for number, body in parsed.objects.items():
        if b"/Subtype /Type0" not in body:
            continue
        (descendant,) = references(entry(body, "DescendantFonts"))
        cid = parsed.objects[descendant]
        listed = re.search(rb"/W \[0 \[([^\[\]]*)\]\]", cid)
        assert listed is not None, f"font {number} has no W array from code 0"
        mapped = stream_of(held(parsed, cid, "CIDToGIDMap"))
        cmap = stream_of(held(parsed, body, "ToUnicode"))
        descriptor = held(parsed, cid, "FontDescriptor")
        program = stream_of(
            parsed.objects[references(entry(descriptor, "FontFile2"))[0]]
        )
        found.append(
            Font(
                base=entry(body, "BaseFont").decode("ascii")[1:],
                widths=[float(one) for one in listed.group(1).split()],
                gids=list(struct.unpack(f">{len(mapped) // 2}H", mapped)),
                unicode={
                    int(code, 16): bytes.fromhex(unit.decode("ascii")).decode(
                        "utf-16-be", "surrogatepass"
                    )
                    for code, unit in re.findall(
                        rb"<([0-9A-F]{4})> <([0-9A-F]+)>", cmap
                    )
                    if code != b"0000" or unit != b"FFFF"
                },
                descriptor=descriptor,
                program=TTFont(io.BytesIO(program)),
            )
        )
    return found


def held(parsed: Parsed, body: bytes, key: str) -> bytes:
    """Return the object one key of an object refers to.

    Args:
        parsed: The file's objects.
        body: The object holding the reference.
        key: The key it is under.

    """
    return parsed.objects[references(entry(body, key))[0]]


def test_one_face_is_one_font_whatever_its_entries_say() -> None:
    """Two sizes, two names, a file and a blob: one face, one font."""
    fonts = (
        font_entry("body", size=9),
        font_entry("title", size=14),
        font_entry("copy", data="go"),
        font_entry("bold", BOLD),
    )
    printout = document(
        text("a", "body"),
        text("b", "title", 20),
        text("c", "copy", 40),
        text("d", "bold", 60),
        fonts=fonts,
        data={"go": REGULAR.read_bytes()},
    )
    found = {one.base[7:]: one for one in fonts_of(rendered(printout)[1])}
    assert sorted(found) == ["Go-Bold", "GoRegular"]
    assert sorted(found["GoRegular"].unicode.values()) == ["a", "b", "c"]
    assert list(found["Go-Bold"].unicode.values()) == ["d"]


def test_a_code_per_character_in_code_point_order() -> None:
    printout = document(text("baA"), text("c", y=20))
    (font,) = fonts_of(rendered(printout)[1])
    assert font.unicode == {1: "A", 2: "a", 3: "b", 4: "c"}
    content = render(printout, compress=False)
    assert b"<000300020001> Tj" in content


def test_two_characters_that_share_a_glyph_get_two_codes(
    tmp_path: Path,
) -> None:
    """A space and a no-break space share a glyph in the made face."""
    face = tmp_path / "made.ttf"
    face.write_bytes(made_font())
    printout = document(text("A\xa0A A"), fonts=(font_entry("body", face),))
    (font,) = fonts_of(rendered(printout)[1])
    codes = {character: code for code, character in font.unicode.items()}
    assert codes[" "] != codes["\xa0"]
    assert font.gids[codes[" "]] == font.gids[codes["\xa0"]]


def test_a_character_the_face_lacks_draws_glyph_0_and_reads_back() -> None:
    (font,) = fonts_of(rendered(document(text("a中")))[1])
    codes = {character: code for code, character in font.unicode.items()}
    assert font.gids[codes["中"]] == 0
    assert font.gids[0] == 0
    assert font.widths[codes["中"]] == font.widths[0]


def test_widths_are_the_engine_s_advances_to_three_decimals() -> None:
    (font,) = fonts_of(rendered(document(text("Wij.")))[1])
    hmtx = GO["hmtx"]
    cmap = GO.getBestCmap()
    expected = [round_points(hmtx[".notdef"][0] * 1000 / 2048)]
    glyphs = [cmap[ord(one)] for one in ".Wij"]
    expected += [round_points(hmtx[one][0] * 1000 / 2048) for one in glyphs]
    assert font.widths == expected
    assert any(width != round(width) for width in font.widths)


def test_the_program_carries_the_tables_render_md_names_and_no_more() -> None:
    (font,) = fonts_of(rendered(document(text("Hello")))[1])
    tables = set(font.program.keys()) - {"GlyphOrder"}
    assert tables <= set(KEPT_TABLES)
    assert {"glyf", "loca", "head", "hhea", "hmtx", "maxp"} <= tables
    assert {"fpgm", "prep", "cvt "} <= tables, "the Go face is hinted"
    assert "cmap" not in tables
    assert len(font.program.getGlyphOrder()) == len("Helo") + 1


def test_a_composite_glyph_brings_its_components_and_names_them_anew(
    tmp_path: Path,
) -> None:
    face = tmp_path / "made.ttf"
    face.write_bytes(made_font())
    printout = document(text("Á"), fonts=(font_entry("body", face),))
    (font,) = fonts_of(rendered(printout)[1])
    glyf = font.program["glyf"]
    order = font.program.getGlyphOrder()
    assert len(order) == 4, "notdef, the composite, and its two components"
    composite = glyf[order[font.gids[1]]]
    assert composite.isComposite()
    boxes = []
    for component in composite.components:
        part = glyf[component.glyphName]
        part.recalcBounds(glyf)
        boxes.append((part.xMin, part.yMin, part.xMax, part.yMax))
    assert boxes == [(0, 0, 600, 700), (200, 750, 400, 850)]


def test_the_subset_tag_is_six_letters_and_stable() -> None:
    def base(line: str) -> str:
        (font,) = fonts_of(rendered(document(text(line)))[1])
        return font.base

    one = base("abc")
    assert re.fullmatch(r"[A-Z]{6}\+GoRegular", one)
    assert base("abc") == one
    assert base("abd")[:6] != one[:6]


def test_the_descriptor_states_hhea_signed_as_pdf_signs_it(
    tmp_path: Path,
) -> None:
    (font,) = fonts_of(rendered(document(text("a")))[1])
    assert entry(font.descriptor, "Ascent") == b"944.824"
    assert entry(font.descriptor, "Descent") == b"-210.938"
    assert entry(font.descriptor, "CapHeight") == b"722.656"
    assert entry(font.descriptor, "Flags") == b"4"
    assert entry(font.descriptor, "FontName") == f"/{font.base}".encode()
    magnitude = tmp_path / "magnitude.ttf"
    magnitude.write_bytes(made_font(descender=200))
    printout = document(text("A"), fonts=(font_entry("body", magnitude),))
    (made,) = fonts_of(rendered(printout)[1])
    assert entry(made.descriptor, "Descent") == b"-200"


def test_a_face_in_a_collection_is_the_one_its_index_names(
    collection: Path,
) -> None:
    second = font_entry("body", collection, index=1)
    printout = document(text("a"), fonts=(second,))
    (font,) = fonts_of(rendered(printout)[1])
    assert font.base.endswith("+Go-Bold")


def test_a_face_with_postscript_outlines_is_refused(tmp_path: Path) -> None:
    face = tmp_path / "made.otf"
    face.write_bytes(made_font(cff=True))
    printout = document(text("A"), fonts=(font_entry("body", face),))
    with pytest.raises(RenderError, match=r"made\.otf has PostScript"):
        render(printout)


def test_every_face_the_header_names_is_embedded() -> None:
    """A face no text sets is a font of one code, the empty glyph's."""
    printout = document(
        text("a"),
        fonts=(font_entry("body"), font_entry("bold", BOLD)),
    )
    found = fonts_of(rendered(printout)[1])
    assert len(found) == 2
    unused = next(one for one in found if one.base.endswith("+Go-Bold"))
    assert unused.unicode == {}
    assert unused.gids == [0]


def test_text_inside_an_xref_is_set_in_its_font_too() -> None:
    link = Xref(
        Box(0, 0, 100, 12),
        link="url",
        target="https://example.com/",
        caption=None,
        marks=(text("z"),),
    )
    (font,) = fonts_of(rendered(document(link))[1])
    assert list(font.unicode.values()) == ["z"]


def test_a_text_mark_naming_a_font_the_header_lacks_is_refused() -> None:
    printout = document(
        Text(
            Box(0, 0, 10, 12),
            font="nowhere",
            color="#000000",
            align="left",
            leading=12,
            lines=("a",),
        ),
    )
    said = "page 1, mark 1: the header carries no font 'nowhere'"
    with pytest.raises(RenderError, match=said):
        render(printout)


def test_a_font_file_that_will_not_read_is_named(tmp_path: Path) -> None:
    gone = font_entry("body", tmp_path / "gone.ttf")
    printout = document(text("a"), fonts=(gone,))
    said = r"font 'body': cannot read .*gone\.ttf"
    with pytest.raises(RenderError, match=said):
        render(printout)


def test_a_font_data_entry_the_header_lacks_is_named() -> None:
    printout = document(text("a"), fonts=(font_entry("body", data="gone"),))
    said = "font 'body': the header holds no data 'gone'"
    with pytest.raises(RenderError, match=said):
        render(printout)
