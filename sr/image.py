"""An image's format and natural size, read from its header.

doc/template.md#image has the engine sniff the format from the content
and read the natural size from it, and doc/layout.md#an-image-in-its-box
does everything else with those two facts.  Nothing here decodes a pixel:
decoding belongs to the renderer, and the engine reads exactly as far
into the bytes as the header that states the size.

Three formats, each recognised by its signature and never by a file name:

* **PNG**: the eight-byte signature, then the `IHDR` chunk, which must
  come first and whose CRC is checked, since a damaged header is a size
  that cannot be trusted.
* **JPEG**: the start-of-image marker, then segments up to the first
  frame header.  Baseline, extended sequential and progressive frames
  are read; lossless, hierarchical and arithmetic-coded ones are refused,
  since a renderer is not required to decode them.
* **GIF**: `GIF87a` or `GIF89a`, then the logical screen descriptor.
  The logical screen is the natural size, not the first frame's.

The natural size is the pixel count on each axis, and it is drawn
at one point per pixel.  Resolution metadata -- a PNG's `pHYs`,
a JFIF density, an EXIF resolution -- is not read, and neither is
an EXIF orientation: what the bytes say the size is, is the size.

"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

__all__ = ["FORMATS", "BadImage", "Header", "read_header", "sniff"]

# The formats a printout's `image` mark can name, in the order doc/ lists them.
FORMATS = ("png", "jpeg", "gif")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"
GIF_SIGNATURES = (b"GIF87a", b"GIF89a")

# The bit depths each PNG colour type admits.
PNG_DEPTHS = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}

# A PNG dimension is a four-byte integer whose top bit must be clear.
PNG_LIMIT = 2**31 - 1

# The JPEG frame headers a renderer can be expected to decode:
# baseline, extended sequential, and progressive, all Huffman-coded.
JPEG_READ = {0xC0, 0xC1, 0xC2}

# Every other frame header, and what it is, for the refusal.
JPEG_REFUSED = {
    0xC3: "lossless",
    0xC5: "hierarchical",
    0xC6: "hierarchical",
    0xC7: "hierarchical",
    0xC9: "arithmetic-coded",
    0xCA: "arithmetic-coded",
    0xCB: "arithmetic-coded",
    0xCD: "arithmetic-coded",
    0xCE: "arithmetic-coded",
    0xCF: "arithmetic-coded",
}

# Markers that stand alone, with no length after them: TEM and RST0-RST7.
JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}

JPEG_SCAN = 0xDA
JPEG_END = 0xD9


class BadImage(ValueError):
    """Bytes that are not an image this engine can size.

    The message says what is wrong with the bytes and nothing
    about where they came from, which the caller adds.

    """


@dataclass(frozen=True)
class Header:
    """What an image's header says.

    Attributes:
        format: One of :data:`FORMATS`.
        width: The natural width, in pixels.
        height: The natural height, in pixels.

    """

    format: str
    width: int
    height: int


def sniff(data: bytes) -> str | None:
    """Return the format an image's signature names, or ``None``.

    Args:
        data: The image's bytes, or at least their start.

    """
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if data.startswith(JPEG_SIGNATURE):
        return "jpeg"
    if data.startswith(GIF_SIGNATURES):
        return "gif"
    return None


def read_header(data: bytes) -> Header:
    """Return an image's format and natural size.

    Args:
        data: The image's bytes.

    Raises:
        BadImage: The bytes are none of the three formats,
            their header is damaged or cut short,
            or the image has no pixels along one of its axes.

    """
    found = sniff(data)
    if found == "png":
        header = png_header(data)
    elif found == "jpeg":
        header = jpeg_header(data)
    elif found == "gif":
        header = gif_header(data)
    else:
        raise BadImage("the content is not a PNG, a JPEG or a GIF")
    if header.width == 0 or header.height == 0:
        raise BadImage(
            f"the {header.format.upper()} is {header.width} by {header.height}"
            " pixels, and an image with no pixels along an axis has no size"
            " to draw at"
        )
    return header


def png_header(data: bytes) -> Header:
    """Return a PNG's natural size, from its `IHDR` chunk.

    Args:
        data: The image's bytes, signature included.

    Raises:
        BadImage: The chunk is missing, damaged, or names
            a combination of colour type and depth PNG does not have.

    """
    start = len(PNG_SIGNATURE)
    chunk = data[start : start + 25]
    if len(chunk) < 25:
        raise BadImage("the PNG ends inside its header")
    length, kind = struct.unpack(">I4s", chunk[:8])
    if kind != b"IHDR" or length != 13:
        raise BadImage("the PNG does not begin with its IHDR chunk")
    body = chunk[8:21]
    (check,) = struct.unpack(">I", chunk[21:25])
    if zlib.crc32(kind + body) != check:
        raise BadImage("the PNG's IHDR chunk is damaged: its CRC does not match")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", body
    )
    if width > PNG_LIMIT or height > PNG_LIMIT:
        raise BadImage("the PNG's IHDR chunk is damaged: a dimension is too large")
    if depth not in PNG_DEPTHS.get(colour, ()):
        raise BadImage(
            f"the PNG's IHDR chunk is damaged: colour type {colour}"
            f" at bit depth {depth} is not a PNG"
        )
    if compression != 0 or filtering != 0 or interlace not in (0, 1):
        raise BadImage("the PNG's IHDR chunk is damaged: it names a method PNG lacks")
    return Header("png", width, height)


def jpeg_header(data: bytes) -> Header:
    """Return a JPEG's natural size, from its first frame header.

    The segments before it are stepped over by their lengths.
    A scan or the end of the image reached first means there is no frame.

    Args:
        data: The image's bytes, start-of-image marker included.

    Raises:
        BadImage: No frame header comes before the first scan,
            the segments run past the end of the bytes,
            or the frame is of a kind a renderer can not decode.

    """
    position = len(JPEG_SIGNATURE)
    end = len(data)
    while True:
        if position >= end or data[position] != 0xFF:
            raise BadImage("the JPEG ends, or is damaged, before its frame header")
        # Any number of 0xFF bytes may pad the space before a marker.
        while position < end and data[position] == 0xFF:
            position += 1
        if position >= end:
            raise BadImage("the JPEG ends before its frame header")
        marker = data[position]
        position += 1
        if marker in JPEG_STANDALONE:
            continue
        if marker in (JPEG_SCAN, JPEG_END):
            raise BadImage("the JPEG has no frame header before its first scan")
        if position + 2 > end:
            raise BadImage("the JPEG ends before its frame header")
        (length,) = struct.unpack(">H", data[position : position + 2])
        if length < 2 or position + length > end:
            raise BadImage("the JPEG ends, or is damaged, before its frame header")
        if marker in JPEG_REFUSED:
            raise BadImage(
                f"the JPEG is {JPEG_REFUSED[marker]}, which a renderer can't"
                " decode; save it as baseline or progressive"
            )
        if marker in JPEG_READ:
            return jpeg_frame(data[position + 2 : position + length])
        position += length


def jpeg_frame(segment: bytes) -> Header:
    """Return the size a JPEG frame header states.

    Args:
        segment: The frame header, after its length.

    Raises:
        BadImage: It is too short, or describes samples of a precision
            or a component count a renderer can not decode.

    """
    if len(segment) < 6:
        raise BadImage("the JPEG's frame header is too short")
    precision, height, width, components = struct.unpack(">BHHB", segment[:6])
    if precision != 8:
        raise BadImage(
            f"the JPEG has {precision}-bit samples, which a renderer can't"
            " decode; save it with 8-bit samples"
        )
    if components not in (1, 3, 4):
        raise BadImage(f"the JPEG has {components} colour components, not 1, 3 or 4")
    return Header("jpeg", width, height)


def gif_header(data: bytes) -> Header:
    """Return a GIF's natural size: its logical screen's.

    Args:
        data: The image's bytes, signature included.

    Raises:
        BadImage: The logical screen descriptor is cut short.

    """
    if len(data) < 13:
        raise BadImage("the GIF ends inside its header")
    width, height = struct.unpack("<HH", data[6:10])
    return Header("gif", width, height)
