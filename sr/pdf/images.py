"""Images in the file: a JPEG as it stands, and everything else re-encoded.

doc/render.md#images decides how bytes are embedded from what they are,
read from their own signature as the engine read them.  The printout
does not record the format, so neither does anything here take one.

* **A baseline JPEG is embedded as it stands**, keeping its own
  compression, behind a `DCTDecode` filter.  Grey or RGB only:
  a CMYK JPEG needs an inverted decode array that is easy to get
  wrong, and a progressive one is not universally supported by
  readers, so both are decoded instead.
* **Everything else is decoded and re-encoded** as eight-bit samples,
  grey where the source is grey and RGB otherwise, Flate-compressed,
  with a soft mask where any pixel is not opaque.  Sixteen-bit samples
  keep their high byte, which is what scaling them to eight bits is.

The size an image is drawn from is its natural size in pixels, which is
also what the engine read from the header, so a `crop` in source pixels
means the same thing here as it did there.

"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image as Pillow

from sr.errors import RenderError
from sr.image import BadImage, jpeg_frame, jpeg_frame_segment, sniff
from sr.pdf.objects import Name, Ref, Stream, Writer

__all__ = ["BASELINE", "Picture", "picture_of"]

# The frame marker of a baseline JPEG, the one kind embedded as it stands.
BASELINE = 0xC0

# A JPEG's colour space, by its number of components.
JPEG_SPACES = {1: "DeviceGray", 3: "DeviceRGB"}

# The decoded modes that are grey.  A palette is not among them:
# a GIF's colours are a palette whatever they are.
GREY_MODES = frozenset(
    {"1", "L", "LA", "La", "I", "I;16", "I;16B", "I;16L", "F"},
)

# The sixteen-bit grey modes, whose samples keep their high byte.
WIDE_GREY = frozenset({"I;16", "I;16B", "I;16L"})

# What scales a 32-bit grey sample of a sixteen-bit image to eight bits.
WIDE_SCALE = 1 / 256

# A sample that is wholly opaque.
OPAQUE = 255


@dataclass(frozen=True)
class Picture:
    """One image, as an XObject the pages show.

    Attributes:
        resource: The name pages show it under: ``Im1``, ``Im2``.
        width: Its natural width, in pixels.
        height: Its natural height, in pixels.
        stream: The image XObject.
        mask: Its soft mask, where a pixel is not opaque.

    """

    resource: str
    width: int
    height: int
    stream: Stream
    mask: Stream | None = None

    def write(self, writer: Writer) -> Ref:
        """Write the XObject, and its mask before it, and return its reference.

        Args:
            writer: The file's object table.

        """
        entries = dict(self.stream.entries)
        if self.mask is not None:
            entries["SMask"] = writer.add(self.mask)
        stream = Stream(entries, self.stream.data, self.stream.compress)
        return writer.add(stream)


def picture_of(data: bytes, resource: str, shown: str) -> Picture:
    """Return an image's XObject, embedded as its format decides.

    Args:
        data: The image's bytes.
        resource: The name pages will show it under.
        shown: What a diagnostic calls the image.

    Raises:
        RenderError: The bytes are not an image this renderer draws.

    """
    if sniff(data) == "jpeg":
        try:
            marker, segment = jpeg_frame_segment(data)
            header = jpeg_frame(segment)
        except BadImage as refused:
            raise RenderError(f"{shown}: {refused}") from None
        components = segment[5]
        if marker == BASELINE and components in JPEG_SPACES:
            return Picture(
                resource,
                header.width,
                header.height,
                Stream(
                    image_entries(
                        header.width,
                        header.height,
                        JPEG_SPACES[components],
                    )
                    | {"Filter": Name("DCTDecode")},
                    data,
                    compress=False,
                ),
            )
    return decoded(data, resource, shown)


def image_entries(width: int, height: int, space: str) -> dict[str, object]:
    """Return what every image XObject's dictionary opens with.

    Args:
        width: In pixels.
        height: In pixels.
        space: The colour space's name.

    """
    return {
        "Type": Name("XObject"),
        "Subtype": Name("Image"),
        "Width": width,
        "Height": height,
        "ColorSpace": Name(space),
        "BitsPerComponent": 8,
    }


def decoded(data: bytes, resource: str, shown: str) -> Picture:
    """Return an image decoded and re-encoded as eight-bit samples.

    Args:
        data: The image's bytes.
        resource: The name pages will show it under.
        shown: What a diagnostic calls the image.

    Raises:
        RenderError: The bytes will not decode.

    """
    try:
        with warnings.catch_warnings():
            # A decompression-bomb warning is about trust,
            # and the printout's own images are the ones asked for.
            warnings.simplefilter("ignore", Pillow.DecompressionBombWarning)
            with Pillow.open(io.BytesIO(data)) as source:
                source.load()
                samples, alpha, space = channels(source)
                width, height = source.size
    except (OSError, ValueError, Pillow.DecompressionBombError) as refused:
        said = f"{shown}: the image will not decode: {refused}"
        raise RenderError(said) from None
    mask = None
    if alpha is not None and min(alpha, default=OPAQUE) < OPAQUE:
        mask = Stream(image_entries(width, height, "DeviceGray"), alpha)
    return Picture(
        resource,
        width,
        height,
        Stream(image_entries(width, height, space), samples),
        mask,
    )


def channels(source: Pillow.Image) -> tuple[bytes, bytes | None, str]:
    """Return an image's colour samples, its alpha, and its colour space.

    Args:
        source: The decoded image, at its first frame.

    """
    clear = "A" in source.getbands() or "transparency" in source.info
    if source.mode in WIDE_GREY:
        return wide_grey(source)
    if source.mode == "I":
        # Thirty-two-bit grey is how some readers hand over sixteen-bit
        # samples; scaled first, it is an eight-bit grey like any other.
        source = source.point(lambda value: value * WIDE_SCALE)
    if source.mode in GREY_MODES:
        if not clear:
            return source.convert("L").tobytes(), None, "DeviceGray"
        both = source.convert("LA")
        return (
            both.getchannel("L").tobytes(),
            both.getchannel("A").tobytes(),
            "DeviceGray",
        )
    if not clear:
        return source.convert("RGB").tobytes(), None, "DeviceRGB"
    colour = source.convert("RGBA")
    return (
        colour.convert("RGB").tobytes(),
        colour.getchannel("A").tobytes(),
        "DeviceRGB",
    )


def wide_grey(source: Pillow.Image) -> tuple[bytes, bytes | None, str]:
    """Return a sixteen-bit grey image's high bytes, and its alpha.

    Converting to eight bits through the library clips every sample
    above 255 rather than scaling it, so the samples are read raw.
    A transparent sample value, where the image names one, is the
    one whole sixteen-bit value that is clear.

    Args:
        source: The decoded image.

    """
    raw = source.tobytes("raw", "I;16B")
    samples = raw[0::2]
    clear = source.info.get("transparency")
    if not isinstance(clear, int):
        return samples, None, "DeviceGray"
    alpha = bytes(
        0 if raw[at] << 8 | raw[at + 1] == clear else OPAQUE
        for at in range(0, len(raw), 2)
    )
    return samples, alpha, "DeviceGray"
