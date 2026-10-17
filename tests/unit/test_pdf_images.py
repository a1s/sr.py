"""Images in the file, held to doc/render.md#images.

A baseline JPEG is embedded as it stands; everything else is decoded
and re-encoded as eight-bit samples, grey where the source is grey,
with a soft mask where a pixel is not opaque.  The sources are made
here with Pillow where the committed images do not cover a case,
which keeps the bytes each test reads visible beside it.

"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image as Pillow

from sr.errors import RenderError
from sr.pdf.images import channels, picture_of
from sr.printout.model import Box, Crop, Image
from tests.pdfcheck import entry, references, stream_of
from tests.unit.documents import document, rendered

ROOT = Path(__file__).resolve().parents[2]
LARCH = ROOT / "example" / "sakila" / "thelarch.jpg"
PROBES = ROOT / "tests" / "differential" / "probes" / "image"


def encoded(image: Pillow.Image, format_name: str, **options: object) -> bytes:
    """Return an image saved in a format.

    Args:
        image: The image.
        format_name: What Pillow calls the format.
        **options: What the format's writer takes.

    """
    out = io.BytesIO()
    image.save(out, format_name, **options)
    return out.getvalue()


def chunk(kind: bytes, body: bytes) -> bytes:
    """Return a PNG chunk, its length and checksum around its body.

    Args:
        kind: The chunk's four-letter type.
        body: What it holds.

    """
    checksum = struct.pack(">I", zlib.crc32(kind + body))
    return struct.pack(">I", len(body)) + kind + body + checksum


def test_a_baseline_jpeg_is_embedded_as_it_stands() -> None:
    data = LARCH.read_bytes()
    found = picture_of(data, "Im1", "larch")
    assert found.stream.data == data
    assert found.stream.compress is False
    assert found.stream.entries["Filter"] == "DCTDecode"
    assert found.stream.entries["ColorSpace"] == "DeviceRGB"
    assert found.mask is None


def test_a_grey_baseline_jpeg_is_embedded_grey() -> None:
    data = (PROBES / "wide.jpg").read_bytes()
    found = picture_of(data, "Im1", "wide")
    assert found.stream.data == data
    assert found.stream.entries["ColorSpace"] == "DeviceGray"
    assert (found.width, found.height) == (24, 12)


@pytest.mark.parametrize(
    ("mode", "options"),
    [("L", {"progressive": True}), ("CMYK", {})],
    ids=["progressive", "cmyk"],
)
def test_a_jpeg_readers_may_misread_is_decoded(
    mode: str, options: dict[str, object]
) -> None:
    source = Pillow.new(mode, (4, 2), 0 if mode == "L" else (0, 255, 0, 0))
    found = picture_of(encoded(source, "JPEG", **options), "Im1", "made")
    assert "Filter" not in found.stream.entries
    assert found.stream.compress is True
    space = "DeviceGray" if mode == "L" else "DeviceRGB"
    assert found.stream.entries["ColorSpace"] == space
    assert len(found.stream.data) == 4 * 2 * (1 if mode == "L" else 3)


def test_a_cmyk_jpeg_keeps_its_colours() -> None:
    """Magenta stays magenta, which an uninverted decode would not give."""
    source = Pillow.new("CMYK", (2, 2), (0, 255, 0, 0))
    found = picture_of(encoded(source, "JPEG", quality=100), "Im1", "made")
    red, green, blue = found.stream.data[:3]
    assert red > 240 and green < 15 and blue > 240


def test_an_opaque_png_has_no_mask() -> None:
    source = Pillow.new("RGBA", (2, 1), (10, 20, 30, 255))
    found = picture_of(encoded(source, "PNG"), "Im1", "made")
    assert found.mask is None
    assert found.stream.data == bytes([10, 20, 30, 10, 20, 30])


def test_a_png_with_a_clear_pixel_has_a_soft_mask() -> None:
    source = Pillow.new("RGBA", (2, 1))
    source.putpixel((0, 0), (10, 20, 30, 255))
    source.putpixel((1, 0), (40, 50, 60, 128))
    found = picture_of(encoded(source, "PNG"), "Im1", "made")
    assert found.stream.data == bytes([10, 20, 30, 40, 50, 60])
    assert found.mask is not None
    assert found.mask.data == bytes([255, 128])
    assert found.mask.entries["ColorSpace"] == "DeviceGray"


def test_grey_stays_grey_and_a_transparent_grey_is_masked() -> None:
    source = Pillow.new("L", (3, 1))
    for at, level in enumerate((0, 128, 255)):
        source.putpixel((at, 0), level)
    plain = picture_of(encoded(source, "PNG"), "Im1", "made")
    assert plain.stream.entries["ColorSpace"] == "DeviceGray"
    assert plain.stream.data == bytes([0, 128, 255])
    clear = picture_of(encoded(source, "PNG", transparency=128), "Im1", "made")
    assert clear.mask is not None and clear.mask.data == bytes([255, 0, 255])


def test_sixteen_bit_grey_keeps_its_high_byte() -> None:
    """Converting through the library would clip 256 and up to 255."""
    source = Pillow.new("I;16", (4, 1))
    for at, level in enumerate((0, 256, 32768, 65535)):
        source.putpixel((at, 0), level)
    found = picture_of(encoded(source, "PNG"), "Im1", "made")
    assert found.stream.entries["ColorSpace"] == "DeviceGray"
    assert found.stream.data == bytes([0, 1, 128, 255])


def test_thirty_two_bit_grey_is_read_as_sixteen() -> None:
    """How some versions of the library hand over a sixteen-bit PNG.

    The clear value is a sixteen-bit one, so it is compared with
    the samples before they lose their low byte.
    """
    source = Pillow.new("I", (3, 1))
    for at, level in enumerate((0, 4660, 65535)):
        source.putpixel((at, 0), level)
    source.info["transparency"] = 4660
    samples, alpha, space = channels(source)
    assert samples == bytes([0, 0x12, 0xFF])
    assert alpha == bytes([255, 0, 255])
    assert space == "DeviceGray"


def test_a_gif_is_decoded_from_its_palette_to_rgb() -> None:
    source = Pillow.new("P", (2, 1))
    source.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    source.putpixel((0, 0), 0)
    source.putpixel((1, 0), 1)
    found = picture_of(encoded(source, "GIF", transparency=0), "Im1", "made")
    assert found.stream.entries["ColorSpace"] == "DeviceRGB"
    assert found.stream.data[3:] == bytes([255, 0, 0])
    assert found.mask is not None and found.mask.data == bytes([0, 255])


def test_a_gif_whose_data_runs_on_past_its_pixels_is_drawn() -> None:
    """doc/render.md#images: what follows the last pixel is not read."""
    found = picture_of((PROBES / "screen.gif").read_bytes(), "Im1", "screen")
    assert (found.width, found.height) == (30, 20)
    assert len(found.stream.data) == 30 * 20 * 3


def test_an_image_whose_data_ends_before_its_last_pixel_is_refused() -> None:
    source = Pillow.new("P", (50, 50))
    source.putpalette(list(range(256)) * 3)
    cells = [(across, down) for down in range(50) for across in range(50)]
    source.putdata([(across * 7 + down * 13) % 256 for across, down in cells])
    whole = encoded(source, "GIF")
    with pytest.raises(RenderError, match="cut: the image will not decode"):
        picture_of(whole[: len(whole) // 2], "Im1", "cut")


def test_codes_that_mean_nothing_before_the_last_pixel_are_refused() -> None:
    """`screen.gif`'s junk, inside a frame two rows taller than its pixels."""
    data = bytearray((PROBES / "screen.gif").read_bytes())
    descriptor = data.index(b"\x2c")
    data[descriptor + 7 : descriptor + 9] = (14).to_bytes(2, "little")
    with pytest.raises(RenderError, match="tall: the image will not decode"):
        picture_of(bytes(data), "Im1", "tall")


def test_a_png_damaged_after_its_header_is_refused() -> None:
    """The engine read only the header, so the build let it through."""
    whole = encoded(Pillow.effect_noise((16, 16), 50), "PNG")
    start = whole.index(b"IDAT") - 4
    (size,) = struct.unpack(">I", whole[start : start + 4])
    payload = whole[start + 8 : start + 8 + size]
    half = len(payload) // 2
    damaged = (
        whole[:start]
        + chunk(b"IDAT", payload[:half])
        + chunk(b"\0\0\0\0", payload[half:])
        + whole[start + 12 + size :]
    )
    with pytest.raises(RenderError, match=r"broken: .* broken PNG file"):
        picture_of(damaged, "Im1", "broken")


def test_a_gif_frame_past_its_logical_screen_is_refused() -> None:
    """The engine sized it by its screen, and the library by its frame."""
    data = bytearray(encoded(Pillow.new("P", (8, 8)), "GIF"))
    data[6:10] = struct.pack("<HH", 4, 4)
    said = "past the 4 by 4 its header states"
    with pytest.raises(RenderError, match=said):
        picture_of(bytes(data), "Im1", "frame")


def test_bytes_that_are_no_image_are_refused() -> None:
    said = "junk: the content is not a PNG, a JPEG or a GIF"
    with pytest.raises(RenderError, match=said):
        picture_of(b"not an image at all", "Im1", "junk")


@pytest.mark.parametrize(
    "data",
    [
        encoded(Pillow.new("RGB", (4, 2)), "BMP"),
        encoded(Pillow.new("RGB", (4, 2)), "TIFF"),
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 4 2\n",
    ],
    ids=["bmp", "tiff", "eps"],
)
def test_a_format_the_engine_does_not_read_is_refused(data: bytes) -> None:
    """The library would decode each, and the engine never sized one."""
    said = "other: the content is not a PNG, a JPEG or a GIF"
    with pytest.raises(RenderError, match=said):
        picture_of(data, "Im1", "other")


def test_two_marks_reading_one_source_share_one_object(tmp_path: Path) -> None:
    """And so do two sources holding the same bytes."""
    copy = tmp_path / "logo.png"
    swatch = ROOT / "example" / "sakila" / "swatch.png"
    copy.write_bytes(swatch.read_bytes())
    printout = document(
        Image(Box(0, 0, 16, 16), data="swatch"),
        Image(Box(20, 0, 16, 16), data="swatch"),
        Image(Box(40, 0, 16, 16), file=copy),
        Image(Box(60, 0, 24, 12), file=PROBES / "wide.jpg"),
        data={"swatch": swatch.read_bytes()},
    )
    _, parsed = rendered(printout)
    bodies = list(parsed.objects.values())
    images = [body for body in bodies if b"/Subtype /Image" in body]
    assert len(images) == 2
    (page,) = parsed.kind("Page")
    (drawn,) = references(entry(parsed.objects[page], "Contents"))
    content = stream_of(parsed.objects[drawn])
    assert content.count(b"/Im1 Do") == 3
    assert content.count(b"/Im2 Do") == 1


def test_an_image_nothing_draws_is_checked_and_not_embedded() -> None:
    """A box or a crop with no area draws nothing, so holds no object.

    The image is still decoded, so one that will not decode
    fails the render as it would anywhere else on the page.
    """
    swatch = (ROOT / "example" / "sakila" / "swatch.png").read_bytes()
    wide = (PROBES / "wide.jpg").read_bytes()
    printout = document(
        Image(Box(0, 0, 0, 16), data="swatch"),
        Image(Box(0, 0, 16, 16), data="swatch", crop=Crop(0, 0, 0, 4)),
        Image(Box(20, 0, 24, 12), data="wide"),
        Image(Box(40, 0, 16, 16), data="swatch"),
        data={"swatch": swatch, "wide": wide},
    )
    _, parsed = rendered(printout)
    bodies = list(parsed.objects.values())
    images = [body for body in bodies if b"/Subtype /Image" in body]
    assert len(images) == 2
    (page,) = parsed.kind("Page")
    (drawn,) = references(entry(parsed.objects[page], "Contents"))
    content = stream_of(parsed.objects[drawn])
    assert content.count(b"/Im1 Do") == 1
    assert content.count(b"/Im2 Do") == 1
    assert content.index(b"/Im1 Do") < content.index(b"/Im2 Do")
    broken = document(
        Image(Box(0, 0, 0, 16), data="junk"),
        data={"junk": swatch[:-40]},
    )
    with pytest.raises(RenderError, match="mark 1: data 'junk'"):
        rendered(broken)


def test_a_mask_is_its_own_object_the_image_names(tmp_path: Path) -> None:
    source = Pillow.new("RGBA", (2, 1), (0, 0, 0, 0))
    printout = document(
        Image(Box(0, 0, 2, 1), data="clear"),
        data={"clear": encoded(source, "PNG")},
    )
    _, parsed = rendered(printout)
    (image,) = [
        body
        for body in parsed.objects.values()
        if b"/Subtype /Image" in body and b"/SMask" in body
    ]
    mask = parsed.objects[int(entry(image, "SMask").split()[0])]
    assert stream_of(mask) == bytes([0, 0])


def test_an_image_data_entry_the_header_lacks_is_named() -> None:
    printout = document(Image(Box(0, 0, 2, 1), data="gone"))
    said = "page 1, mark 1: the header holds no data 'gone'"
    with pytest.raises(RenderError, match=said):
        rendered(printout)


def test_an_image_file_that_will_not_read_is_named(tmp_path: Path) -> None:
    printout = document(Image(Box(0, 0, 2, 1), file=tmp_path / "gone.png"))
    said = r"page 1, mark 1: cannot read .*gone\.png"
    with pytest.raises(RenderError, match=said):
        rendered(printout)
