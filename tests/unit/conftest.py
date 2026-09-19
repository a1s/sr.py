"""Font fixtures the unit tests share.

The two committed faces in `example/fonts/` are the only real fonts
this repository carries, and between them they answer most questions:
one regular, one bold, both TrueType, both 2048 units to the em.
What they cannot answer is anything about collections or about files
that are not fonts, so those are built here at test time rather than
committed.

A generated `.ttc` is better than a committed one for the same reason
a generated fixture usually is: the two faces inside it are the ones
beside it in the tree, so a reader can see what the collection is made of,
and there is no binary in the history that has to be taken on trust.

"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fontTools.ttLib import TTCollection, TTFont

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "example" / "fonts"
REGULAR = FONTS / "Go-Regular.ttf"
BOLD = FONTS / "Go-Bold.ttf"

# Files that are not sfnt fonts, as their first bytes give them away.
# Each is one row of :data:`sr.fonts.face.OTHER_FORMATS`, plus one file
# that claims to be an sfnt and is not one.
NOT_FONTS = {
    "bitmap.fon": b"MZ\x90\x00" + b"\x00" * 60,
    "postscript.pfb": b"\x80\x01\x00\x00" + b"\x00" * 32,
    "type1.pfa": b"%!PS-AdobeFont-1.0: Nothing\n",
    "webfont.woff": b"wOFF\x00\x01\x00\x00" + b"\x00" * 32,
    "notes.txt": b"this is not a font at all\n",
    "truncated.ttf": b"\x00\x01\x00\x00" + b"\x00" * 20,
}


@pytest.fixture(scope="session")
def collection_bytes() -> bytes:
    """Return a two-face collection: Go Regular, then Go Bold."""
    holder = TTCollection()
    holder.fonts = [TTFont(REGULAR), TTFont(BOLD)]
    buffer = io.BytesIO()
    holder.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def collection(collection_bytes: bytes, tmp_path: Path) -> Path:
    """Return a two-face collection on disk."""
    path = tmp_path / "Go.ttc"
    path.write_bytes(collection_bytes)
    return path


@pytest.fixture
def font_directory(tmp_path: Path, collection_bytes: bytes) -> Path:
    """Return a directory holding two faces, a collection, and six non-fonts."""
    directory = tmp_path / "fonts"
    directory.mkdir()
    (directory / "Go-Regular.ttf").write_bytes(REGULAR.read_bytes())
    (directory / "Go-Bold.ttf").write_bytes(BOLD.read_bytes())
    (directory / "Go.ttc").write_bytes(collection_bytes)
    for name, content in NOT_FONTS.items():
        (directory / name).write_bytes(content)
    return directory
