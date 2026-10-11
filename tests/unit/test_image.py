"""Reading an image's format and natural size from its header.

The bytes are built by hand here rather than by an encoder,
so that each case says exactly what its header holds: a size,
a chunk out of order, a frame type, a damaged checksum.
A few real files written by Pillow check that what an encoder
writes is read the same way.

"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from sr.image import BadImage, Header, read_header, sniff

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, body: bytes) -> bytes:
    """Return one PNG chunk, its CRC computed."""
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


def png(
    width: int = 24,
    height: int = 12,
    depth: int = 8,
    colour: int = 2,
    interlace: int = 0,
) -> bytes:
    """Return a PNG's signature and `IHDR`, which is all the engine reads."""
    body = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace)
    return SIGNATURE + chunk(b"IHDR", body) + chunk(b"IEND", b"")


def segment(marker: int, body: bytes) -> bytes:
    """Return one JPEG marker segment, its length counting itself."""
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def frame(
    marker: int = 0xC0,
    width: int = 24,
    height: int = 12,
    precision: int = 8,
    components: int = 3,
) -> bytes:
    """Return a JPEG frame header segment."""
    body = struct.pack(">BHHB", precision, height, width, components)
    body += b"\x01\x11\x00" * components
    return segment(marker, body)


def jpeg(*segments: bytes) -> bytes:
    """Return a JPEG's start-of-image marker, then these, then a scan."""
    return b"\xff\xd8" + b"".join(segments) + segment(0xDA, b"\x00" * 8) + b"\xff\xd9"


def gif(screen: tuple[int, int] = (24, 12), version: bytes = b"89a") -> bytes:
    """Return a GIF's header and logical screen descriptor, then its trailer."""
    return b"GIF" + version + struct.pack("<HHBBB", *screen, 0, 0, 0) + b"\x3b"


def encoded(fmt: str, **options: object) -> bytes:
    """Return a 24 by 12 image as Pillow writes it."""
    buffer = io.BytesIO()
    Image.new("RGB", (24, 12), (200, 100, 50)).save(buffer, format=fmt, **options)
    return buffer.getvalue()


# -- sniffing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "found"),
    [
        (png(), "png"),
        (jpeg(frame()), "jpeg"),
        (gif(), "gif"),
        (gif(version=b"87a"), "gif"),
        (b"GIF88a", None),
        (b"BM\x00\x00", None),
        (b"", None),
    ],
)
def test_the_format_is_the_signature(data: bytes, found: str | None) -> None:
    assert sniff(data) == found


def test_bytes_with_no_signature_are_refused() -> None:
    with pytest.raises(BadImage, match="not a PNG, a JPEG or a GIF"):
        read_header(b"not an image at all")


# -- PNG -----------------------------------------------------------------


def test_a_png_is_sized_by_its_ihdr() -> None:
    assert read_header(png(24, 12)) == Header("png", 24, 12)


def test_an_interlaced_png_is_sized_the_same() -> None:
    assert read_header(png(interlace=1)) == Header("png", 24, 12)


def test_a_png_with_a_resolution_is_still_a_point_per_pixel() -> None:
    assert read_header(encoded("PNG", dpi=(144, 144))) == Header("png", 24, 12)


def test_a_png_whose_ihdr_is_damaged_is_refused() -> None:
    data = bytearray(png())
    data[20] ^= 0x01  # inside the width, after the CRC was computed
    with pytest.raises(BadImage, match="CRC"):
        read_header(bytes(data))


def test_a_png_that_does_not_open_with_its_ihdr_is_refused() -> None:
    data = SIGNATURE + chunk(b"gAMA", b"\x00\x00\xb1\x8f") + png()[8:]
    with pytest.raises(BadImage, match="does not begin with its IHDR"):
        read_header(data)


@pytest.mark.parametrize(("depth", "colour"), [(16, 3), (4, 2), (2, 6), (8, 5)])
def test_a_png_depth_its_colour_type_lacks_is_refused(depth: int, colour: int) -> None:
    with pytest.raises(BadImage, match="is not a PNG"):
        read_header(png(depth=depth, colour=colour))


def test_a_png_cut_short_is_refused() -> None:
    with pytest.raises(BadImage, match="ends inside its header"):
        read_header(png()[:20])


# -- JPEG ----------------------------------------------------------------


@pytest.mark.parametrize("marker", [0xC0, 0xC1, 0xC2])
def test_a_jpeg_is_sized_by_its_frame_header(marker: int) -> None:
    assert read_header(jpeg(frame(marker, 24, 12))) == Header("jpeg", 24, 12)


def test_the_segments_before_the_frame_are_stepped_over() -> None:
    data = jpeg(
        segment(0xE0, b"JFIF\x00" + b"\x00" * 9),
        segment(0xE1, b"Exif\x00\x00" + b"\x00" * 300),
        segment(0xDB, b"\x00" * 65),
        frame(width=640, height=480),
    )
    assert read_header(data) == Header("jpeg", 640, 480)


def test_padding_and_standalone_markers_are_stepped_over() -> None:
    data = jpeg(b"\xff\xff\xff", b"\xff\x01", frame(width=7, height=5))
    assert read_header(data) == Header("jpeg", 7, 5)


@pytest.mark.parametrize("options", [{}, {"progressive": True}, {"dpi": (300, 300)}])
def test_what_an_encoder_writes_is_read(options: dict[str, object]) -> None:
    assert read_header(encoded("JPEG", **options)) == Header("jpeg", 24, 12)


@pytest.mark.parametrize(
    ("marker", "kind"),
    [(0xC3, "lossless"), (0xC5, "hierarchical"), (0xC9, "arithmetic-coded")],
)
def test_a_jpeg_a_renderer_need_not_decode_is_refused(marker: int, kind: str) -> None:
    with pytest.raises(BadImage, match=kind):
        read_header(jpeg(frame(marker)))


def test_a_jpeg_of_twelve_bit_samples_is_refused() -> None:
    with pytest.raises(BadImage, match="12-bit"):
        read_header(jpeg(frame(precision=12)))


def test_a_jpeg_of_two_components_is_refused() -> None:
    with pytest.raises(BadImage, match="2 colour components"):
        read_header(jpeg(frame(components=2)))


def test_a_jpeg_with_no_frame_before_its_scan_is_refused() -> None:
    with pytest.raises(BadImage, match="no frame header"):
        read_header(jpeg(segment(0xDB, b"\x00" * 65)))


@pytest.mark.parametrize("cut", [3, 6, 30])
def test_a_jpeg_cut_short_is_refused(cut: int) -> None:
    data = jpeg(segment(0xE0, b"\x00" * 40), frame())
    with pytest.raises(BadImage, match="ends"):
        read_header(data[:cut])


# -- GIF -----------------------------------------------------------------


def test_a_gif_is_sized_by_its_logical_screen() -> None:
    assert read_header(gif((30, 20))) == Header("gif", 30, 20)


def test_what_an_encoder_writes_as_gif_is_read() -> None:
    assert read_header(encoded("GIF")) == Header("gif", 24, 12)


def test_a_gif_cut_short_is_refused() -> None:
    with pytest.raises(BadImage, match="ends inside its header"):
        read_header(gif()[:10])


# -- no pixels -----------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [gif((0, 0)), gif((24, 0)), png(0, 12), jpeg(frame(width=24, height=0))],
)
def test_an_image_with_no_pixels_along_an_axis_is_refused(data: bytes) -> None:
    with pytest.raises(BadImage, match="no pixels along an axis"):
        read_header(data)
