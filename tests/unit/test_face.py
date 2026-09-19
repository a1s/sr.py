"""One face: what it is called, what style it is, and what it measures."""

from __future__ import annotations

from pathlib import Path

import pytest

from sr.errors import FontError
from sr.fonts.face import (
    LATIN1,
    Origin,
    faces_in,
    open_bytes,
    open_face,
    read_faces,
    sniff,
)

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "example" / "fonts"
REGULAR = FONTS / "Go-Regular.ttf"
BOLD = FONTS / "Go-Bold.ttf"

# Go-Regular, in font units at 2048 to the em.  Read out of `hmtx`,
# and the numbers the probes in tests/differential/probes/breaking/
# were written around.
ADVANCES = {"x": 1024, "q": 1139, "m": 1706, "W": 1933, " ": 569}
NOTDEF = 1536


# -- names and style --------------------------------------------------


def test_the_family_is_the_name_record_not_the_filename() -> None:
    assert open_face(REGULAR).family == "Go"
    assert open_face(BOLD).family == "Go"


def test_the_subfamily_says_what_the_family_does_not() -> None:
    assert open_face(REGULAR).subfamily == "Regular"
    assert open_face(BOLD).subfamily == "Bold"


def test_style_comes_from_the_style_bits() -> None:
    regular, bold = open_face(REGULAR), open_face(BOLD)
    assert (regular.bold, regular.italic) == (False, False)
    assert (bold.bold, bold.italic) == (True, False)


def test_the_em_is_read_from_head() -> None:
    assert open_face(REGULAR).units_per_em == 2048


def test_a_face_names_itself_in_a_repr_a_failure_can_be_read_from() -> None:
    assert "'Go'" in repr(open_face(BOLD))
    assert "bold" in repr(open_face(BOLD))
    assert "regular" in repr(open_face(REGULAR))


# -- advances ---------------------------------------------------------


@pytest.mark.parametrize(("character", "units"), sorted(ADVANCES.items()))
def test_an_advance_is_what_hmtx_says(character: str, units: int) -> None:
    assert open_face(REGULAR).advance(ord(character)) == units


def test_a_character_the_face_lacks_advances_by_notdef() -> None:
    face = open_face(REGULAR)
    assert not face.covers(ord("\t"))
    assert face.advance(ord("\t")) == NOTDEF
    assert face.advance(ord("\u4e2d")) == NOTDEF


def test_coverage_is_what_the_character_map_holds() -> None:
    face = open_face(REGULAR)
    assert face.covers(ord("\r"))  # a glyph, though not a printing one
    assert face.covers(ord("\u00a0"))
    assert face.covers(ord("\u00ad"))
    assert not face.covers(ord("\u200b"))
    assert not face.covers(ord("\U0001f600"))


def test_an_advance_is_cached_and_the_cache_does_not_change_the_answer() -> None:
    face = open_face(REGULAR)
    first = face.advance(ord("x"))
    assert face.advance(ord("x")) == first
    assert len(face.widths) == 1


# -- the monospace check ----------------------------------------------


def test_a_proportional_face_fails_the_monospace_check() -> None:
    uneven = open_face(REGULAR).uneven_advances()
    assert len(uneven) > 1
    assert uneven == tuple(sorted(uneven))


def test_the_check_covers_latin_1_and_no_more() -> None:
    assert list(LATIN1) == list(range(256))


# -- collections ------------------------------------------------------


def test_a_plain_file_holds_one_face() -> None:
    assert faces_in(REGULAR.read_bytes()) == 1


def test_a_collection_says_how_many_faces_it_holds(collection_bytes: bytes) -> None:
    assert faces_in(collection_bytes) == 2


def test_each_face_of_a_collection_is_read_separately(collection: Path) -> None:
    faces = list(read_faces(collection.read_bytes(), Origin(path=collection)))
    assert [one.subfamily for one in faces] == ["Regular", "Bold"]
    assert [one.origin.index for one in faces] == [0, 1]


def test_a_face_beyond_the_end_of_a_collection_is_refused(collection: Path) -> None:
    with pytest.raises(FontError, match="holds 2 faces"):
        open_face(collection, 2)


# -- where a face came from -------------------------------------------


def test_an_origin_shows_its_index_only_when_it_is_not_zero() -> None:
    assert str(Origin(path=Path("/fonts/Go.ttc"))) == "/fonts/Go.ttc"
    assert str(Origin(path=Path("/fonts/Go.ttc"), index=3)) == "/fonts/Go.ttc face 3"


def test_an_origin_in_a_blob_names_the_blob() -> None:
    assert str(Origin(data="face")) == "data face"


def test_a_face_read_from_bytes_knows_it_came_from_a_blob() -> None:
    face = open_bytes(REGULAR.read_bytes(), "body")
    assert face.origin.data == "body"
    assert face.origin.path is None
    assert face.family == "Go"


# -- classifying a file -----------------------------------------------


@pytest.mark.parametrize(
    ("head", "kind"),
    [
        (b"\x00\x01\x00\x00", "truetype"),
        (b"true", "truetype"),
        (b"OTTO", "opentype"),
        (b"ttcf", "collection"),
    ],
)
def test_an_sfnt_is_recognised_by_its_first_bytes(head: bytes, kind: str) -> None:
    assert sniff(head) == (kind, "")


@pytest.mark.parametrize(
    ("head", "says"),
    [
        (b"wOFF\x00\x01", "WOFF"),
        (b"wOF2\x00\x01", "WOFF2"),
        (b"%!PS-AdobeFont-1.0", "Type 1"),
        (b"\x80\x01\x00\x00", "Type 1"),
        (b"MZ\x90\x00", "bitmap"),
        (b"STARTFONT 2.1", "BDF"),
        (b"\x1f\x8b\x08\x00", "gzip"),
        (b"\x00\x00\x01\x00", "datafork"),
        (b"nothing at all", "not an sfnt"),
    ],
)
def test_anything_else_is_classified_rather_than_dropped(
    head: bytes, says: str
) -> None:
    kind, description = sniff(head)
    assert kind is None
    assert says in description


def test_a_file_that_is_not_there_is_a_font_error(tmp_path: Path) -> None:
    with pytest.raises(FontError, match="cannot read"):
        open_face(tmp_path / "absent.ttf")


def test_a_file_that_is_not_a_font_is_a_font_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"this is not a font")
    with pytest.raises(FontError, match="not an sfnt"):
        open_face(path)


def test_a_file_that_claims_to_be_an_sfnt_and_is_not_is_a_font_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.ttf"
    path.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 20)
    with pytest.raises(FontError):
        open_face(path)
