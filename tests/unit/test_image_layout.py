"""An image in a band: its size, its box, its `crop`, and its `data` entry.

The differential suite holds all of this to the reference over
the `image/` and `data/` probes.  What is here is each rule of
doc/layout.md#an-image-in-its-box and doc/printout.md#data on its own,
and the places this engine and the reference part company -- which pixels
an aligned `cut` keeps, `valign` under `grow`, `type`, which an image
here does not take, an image of no pixels, and the entry of an image
never drawn -- are tested for this engine's answer.

"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image as Picture

from sr.api import Options, build
from sr.errors import BuildError, TemplateError
from sr.printout.inspect import mark_line
from sr.printout.model import Box, Crop, Image, Printout, Text, Xref
from sr.printout.write import mark_object

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

HEAD = """
report name="Images" {
  font "body" file="FACE" size=10
DATA
  layout width=500 height=HEIGHT leftmargin=0 rightmargin=0 topmargin=0 \\
         bottommargin=0 {
    style font="body" color="black"
BANDS
  }
}
"""


def picture(path: Path, size: tuple[int, int] = (24, 12), fmt: str = "PNG") -> bytes:
    """Write an image of this size, and return its bytes."""
    buffer = io.BytesIO()
    Picture.new("RGB", size, (200, 100, 50)).save(buffer, format=fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def built(
    tmp_path: Path,
    bands: str,
    data: str = "",
    rows: list[dict[str, Any]] | None = None,
    height: int = 800,
) -> Printout:
    """Build a report beside the images in ``tmp_path``, and return it.

    Args:
        tmp_path: Where the template, the data, and the images are.
        bands: What goes under `layout`, after its style.
        data: What goes between the font and the layout.
        rows: The records, or ``None`` for one record nothing reads.
        height: The page's height.

    """
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("HEIGHT", str(height))
    text = text.replace("DATA", data).replace("BANDS", bands)
    template.write_text(text, encoding="utf-8")
    records = tmp_path / "rows.jsonl"
    lines = [json.dumps(one) for one in (rows if rows is not None else [{"n": 1}])]
    records.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    options = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(template, records, options).printout


def detail(*elements: str, height: str = "") -> str:
    """Return a detail band holding these elements."""
    inside = "\n".join(f"      {one}" for one in elements)
    return f"    detail {height} {{\n{inside}\n    }}"


def images(printout: Printout, page: int = 0) -> list[Image]:
    """Return the image marks of a page, in paint order."""
    return [one for one in printout.pages[page].marks if isinstance(one, Image)]


def box(mark: Image) -> tuple[float, float, float, float]:
    """Return a mark's box as a tuple."""
    return (mark.box.x, mark.box.y, mark.box.width, mark.box.height)


@pytest.fixture
def wide(tmp_path: Path) -> bytes:
    """Write a PNG 24 by 12 at ``wide.png``, and return its bytes."""
    return picture(tmp_path / "wide.png")


# -- cut -----------------------------------------------------------------


@pytest.mark.usefixtures("wide")
def test_cut_draws_a_small_image_at_its_size_where_alignment_puts_it(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" left=0 top=0 width=40 height=40',
            'image file="wide.png" left=50 top=0 width=40 height=40'
            ' halign="right" valign="center"',
        ),
    )
    first, second = images(printout)
    assert box(first) == (0, 0, 24, 12)
    assert box(second) == (66, 14, 24, 12)
    assert first.crop is None and second.crop is None


@pytest.mark.usefixtures("wide")
def test_cut_clips_a_large_image_to_its_box(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" left=0 top=0 width=10 height=5',
            'image file="wide.png" left=50 top=0 width=10 height=40 valign="bottom"',
            'image file="wide.png" left=100 top=0 width=10.5 height=5.25',
        ),
    )
    clipped, across, fraction = images(printout)
    assert box(clipped) == (0, 0, 10, 5)
    assert clipped.crop == Crop(0, 0, 10, 5)
    assert box(across) == (50, 28, 10, 12)
    assert across.crop == Crop(0, 0, 10, 12), "the unclipped axis spans the image"
    assert fraction.crop == Crop(0, 0, 10.5, 5.25)


@pytest.mark.usefixtures("wide")
def test_a_clipped_image_keeps_the_pixels_its_alignment_leaves(
    tmp_path: Path,
) -> None:
    """This engine's answer; the reference keeps the top-left corner.

    See `cut-crop-ignores-alignment` in divergences.toml.

    """
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" left=0 top=0 width=10 height=5'
            ' halign="center" valign="center"',
            'image file="wide.png" left=50 top=0 width=10 height=5'
            ' halign="right" valign="bottom"',
            'image file="wide.png" left=100 top=0 width=10.5 height=5.25'
            ' halign="center" valign="center"',
        ),
    )
    middle, corner, fraction = images(printout)
    assert middle.crop == Crop(7, 3.5, 10, 5)
    assert corner.crop == Crop(14, 7, 10, 5)
    assert fraction.crop == Crop(6.75, 3.375, 10.5, 5.25)
    assert box(corner) == (50, 0, 10, 5), "the box is the box"


# -- fill ----------------------------------------------------------------


@pytest.mark.usefixtures("wide")
def test_fill_keeps_the_proportions_along_the_limiting_axis(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" scale="fill" left=0 top=0 width=40 height=40'
            ' valign="bottom"',
            'image file="wide.png" scale="fill" left=50 top=0 width=100 height=10'
            ' halign="center"',
        ),
    )
    by_width, by_height = images(printout)
    assert box(by_width) == (0, 20, 40, 20)
    assert box(by_height) == (90, 0, 20, 10)
    assert by_width.crop is None


def test_fill_rounds_the_axis_that_follows(tmp_path: Path) -> None:
    picture(tmp_path / "odd.png", (7, 5))
    printout = built(
        tmp_path,
        detail(
            'image file="odd.png" scale="fill" left=0 top=0 width=10 height=3.333',
            'image file="odd.png" scale="fill" left=50 top=0 width=10 height=10'
            ' valign="center"',
        ),
    )
    low, square = images(printout)
    assert box(low) == (0, 0, 4.666, 3.333)
    assert box(square) == (50, 1.429, 10, 7.143)


@pytest.mark.usefixtures("wide")
def test_fill_without_proportions_is_the_box(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" scale="fill" proportional=#false left=0 top=0'
            ' width=40 height=40 halign="right"',
            'image file="wide.png" scale="fill" left=50 top=0 width=0 height=40',
        ),
    )
    stretched, nothing = images(printout)
    assert box(stretched) == (0, 0, 40, 40)
    assert box(nothing) == (50, 0, 0, 0)


# -- grow ----------------------------------------------------------------


@pytest.mark.usefixtures("wide")
def test_grow_expands_the_box_right_and_down(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" scale="grow" left=0 top=0 width=10 height=5'
            ' halign="right" valign="bottom"',
            'image file="wide.png" scale="grow" right=300 width=10 top=0 height=5'
            ' halign="right"',
            'image file="wide.png" scale="grow" left=250 top=0 width=40 maxwidth=10'
            " height=40 maxheight=5",
        ),
    )
    small, derived, clamped = images(printout)
    assert box(small) == (0, 0, 24, 12)
    assert box(derived) == (190, 0, 24, 12), "it grows from the left edge it has"
    assert box(clamped) == (250, 0, 24, 12), "a clamp does not clamp a grow image"


@pytest.mark.usefixtures("wide")
def test_grow_aligns_an_image_smaller_than_its_box(tmp_path: Path) -> None:
    """This engine's answer down the page; the reference ignores `valign`.

    See `grow-ignores-valign` in divergences.toml.

    """
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" scale="grow" left=0 top=0 width=40 height=40'
            ' halign="center" valign="center"',
            'image file="wide.png" scale="grow" left=50 top=0 width=40 height=40'
            ' valign="bottom"',
        ),
    )
    centred, bottom = images(printout)
    assert box(centred) == (8, 14, 24, 12)
    assert box(bottom) == (50, 28, 24, 12)


@pytest.mark.usefixtures("wide")
def test_the_natural_height_of_a_grow_image_sizes_the_band(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" scale="grow" left=0 top=30 height=5',
            'image file="wide.png" scale="grow" left=50 top=40 bottom=0',
        ),
        rows=[{"n": 1}, {"n": 2}],
    )
    marks = images(printout)
    assert box(marks[0]) == (0, 30, 24, 12)
    assert box(marks[1]) == (50, 40, 24, 12), "it grows past the band's edge"
    assert marks[2].box.y == 52 + 30, "and the next band starts below it"


@pytest.mark.usefixtures("wide")
def test_an_image_with_no_height_under_cut_takes_the_band(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'image file="wide.png" left=0 top=2 width=40 valign="bottom"',
            height="height=30",
        ),
    )
    assert box(images(printout)[0]) == (0, 18, 24, 12)


# -- what is read --------------------------------------------------------


def test_the_natural_size_ignores_resolution_and_orientation(tmp_path: Path) -> None:
    picture(tmp_path / "dpi.png", fmt="PNG")
    buffer = io.BytesIO()
    exif = Picture.Exif()
    exif[0x0112] = 6
    Picture.new("RGB", (24, 12)).save(
        buffer, format="JPEG", dpi=(300, 300), exif=exif.tobytes()
    )
    (tmp_path / "rotated.jpg").write_bytes(buffer.getvalue())
    printout = built(
        tmp_path,
        detail(
            'image file="dpi.png" left=0 top=0 width=100 height=100',
            'image file="rotated.jpg" left=100 top=0 width=100 height=100',
        ),
    )
    assert [box(one)[2:] for one in images(printout)] == [(24, 12), (24, 12)]


def test_the_format_is_sniffed_from_the_bytes(tmp_path: Path) -> None:
    content = picture(tmp_path / "misnamed.jpg")
    printout = built(
        tmp_path,
        detail('image file="misnamed.jpg" left=0 top=0 width=40 height=40'),
    )
    assert box(images(printout)[0]) == (0, 0, 24, 12)
    assert printout.data == {"misnamed.jpg": content}, "written as it is"


def test_type_is_not_an_image_property(tmp_path: Path, wide: bytes) -> None:
    """This engine's answer; the reference takes `type` and writes it.

    See `image-type-removed` in divergences.toml.

    """
    printout = built(
        tmp_path,
        detail('image file="wide.png" type="jpeg" left=0 top=0 width=40 height=40'),
    )
    [warning] = printout.warnings
    assert (warning.kind, warning.message) == (
        "unknown",
        "unknown property `type`, accepted and ignored",
    )
    assert printout.data == {"wide.png": wide}, "nothing converts it"
    assert "type" not in mark_object(images(printout)[0])


# -- where the bytes go --------------------------------------------------


def test_an_embedded_file_is_named_by_its_base_name(tmp_path: Path) -> None:
    content = picture(tmp_path / "assets" / "logo.png")
    printout = built(
        tmp_path, detail('image file="assets/logo.png" left=0 top=0 width=40 height=40')
    )
    assert images(printout)[0].data == "logo.png"
    assert printout.data == {"logo.png": content}


def test_a_referenced_file_is_written_relative_to_the_printout(
    tmp_path: Path, wide: bytes
) -> None:
    printout = built(
        tmp_path,
        detail('image file="wide.png" embed=#false left=0 top=0 width=40 height=40'),
    )
    mark = images(printout)[0]
    assert mark.data is None
    assert mark.file == tmp_path / "wide.png"
    assert printout.data == {}, "its bytes stay in the file"
    written = mark_object(mark, tmp_path / "out")
    assert written["file"] == "../wide.png"
    assert list(written) == ["kind", "box", "file"]


def test_a_content_child_is_base64_named_image(tmp_path: Path) -> None:
    content = picture(tmp_path / "unused.png")
    text = base64.b64encode(content).decode("ascii")
    rows = [text[at : at + 40] for at in range(0, len(text), 40)]
    lines = "\n".join(f"          {row}" for row in rows)
    printout = built(
        tmp_path,
        detail(
            f'image left=0 top=0 width=40 height=40 {{ content "{text}"; }}',
            "image left=50 top=0 width=40 height=40 {\n"
            f'        content """\n{lines}\n          """\n      }}',
        ),
        data='  data "image" { content "reserved"; }',
    )
    assert [one.data for one in images(printout)] == ["image-2", "image-2"]
    assert printout.data == {"image-2": content}


def test_a_content_child_that_is_not_base64_is_refused_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="content is not base64"):
        built(tmp_path, detail('image left=0 top=0 { content "GIF89a"; }'))


def test_a_data_node_keeps_its_name(tmp_path: Path) -> None:
    content = picture(tmp_path / "logo.png")
    text = base64.b64encode(content).decode("ascii")
    printout = built(
        tmp_path,
        detail(
            'image file="logo.png" left=0 top=0 width=40 height=40',
            'image data="mine" left=50 top=0 width=40 height=40',
        ),
        data=f'  data "mine" encoding="base64" {{ content "{text}"; }}',
    )
    assert [one.data for one in images(printout)] == ["mine", "mine"]
    assert printout.data == {"mine": content}


def test_an_image_built_and_never_drawn_takes_its_name_and_no_entry(
    tmp_path: Path,
) -> None:
    """This engine's answer; the reference writes the entry all the same.

    See `unprinted-blobs-written` in divergences.toml.

    """
    picture(tmp_path / "one" / "pic.png", (16, 16))
    picture(tmp_path / "two" / "pic.png", (24, 24))
    printout = built(
        tmp_path,
        detail('image file="one/pic.png" left=0 top=0', height="height=40")
        + '\n    footer height=20 printwhen="REPORT_COUNT == 0" {\n'
        '      image file="two/pic.png" left=0 top=0\n    }',
        height=100,
    )
    assert [one.data for one in images(printout)] == ["pic.png-2"]
    assert list(printout.data) == ["pic.png-2"]


def test_an_image_inside_an_xref_has_its_entry_written(tmp_path: Path) -> None:
    content = picture(tmp_path / "logo.png")
    printout = built(
        tmp_path,
        detail(
            'xref type="url" target="\'https://example.invalid/\'" left=0 top=0'
            " width=40 height=40 {\n"
            '        image file="logo.png" scale="fill"\n      }'
        ),
    )
    link = printout.pages[0].marks[0]
    assert isinstance(link, Xref)
    assert isinstance(link.marks[0], Image)
    assert printout.data == {"logo.png": content}


def test_a_font_read_out_of_a_data_node_has_its_entry(tmp_path: Path) -> None:
    face = Path(REGULAR).read_bytes()
    text = base64.b64encode(face).decode("ascii")
    data = (
        f'  data "one" encoding="base64" {{ content "{text}"; }}\n'
        f'  data "two" encoding="base64" {{ content "{text}"; }}\n'
        '  font "small" data="one" size=10\n'
        '  font "large" data="two" size=12\n'
    )
    printout = built(
        tmp_path,
        detail(
            'field text="x" left=0 top=0 width=40 height=20 { style font="large"; }',
            'field text="y" left=50 top=0 width=40 height=20 { style font="small"; }',
        ),
        data=data,
    )
    entries = {one.name: one.data for one in printout.fonts}
    assert entries == {"small": "two", "large": "two"}
    assert printout.data == {"two": face}
    assert all(isinstance(one, Text) for one in printout.pages[0].marks)


# -- errors --------------------------------------------------------------


@pytest.mark.parametrize("embed", ["#true", "#false"])
def test_a_file_that_will_not_read_is_refused(tmp_path: Path, embed: str) -> None:
    with pytest.raises(BuildError) as refused:
        built(
            tmp_path,
            detail(f'image file="missing.png" embed={embed} left=0 top=0'),
        )
    message = str(refused.value)
    assert "image file=" in message
    assert "missing.png" in message


def test_bytes_that_are_no_image_are_refused(tmp_path: Path) -> None:
    (tmp_path / "notes.png").write_text("not an image", encoding="utf-8")
    with pytest.raises(BuildError, match="not a PNG, a JPEG or a GIF"):
        built(
            tmp_path,
            detail('image file="notes.png" left=0 top=0 width=9 height=9'),
        )


def test_an_image_of_no_pixels_is_refused(tmp_path: Path) -> None:
    """This engine's answer; the reference builds it.

    See `image-without-pixels` in divergences.toml.

    """
    (tmp_path / "nothing.gif").write_bytes(b"GIF89a\x00\x00\x00\x00\x00\x00\x00\x3b")
    with pytest.raises(BuildError, match="no pixels along an axis"):
        built(
            tmp_path,
            detail(
                'image file="nothing.gif" scale="fill" left=0 top=0 width=9 height=9'
            ),
        )


def test_the_mark_writes_its_fields_in_the_documented_order() -> None:
    mark = Image(Box(1, 2, 10, 5), data="logo.png", crop=Crop(7, 3.5, 10, 5))
    written = mark_object(mark)
    assert list(written) == ["kind", "box", "data", "crop"]
    assert written["crop"] == {"x": 7, "y": 3.5, "width": 10, "height": 5}


def test_inspect_shows_an_images_crop() -> None:
    clipped = mark_object(
        Image(Box(1, 2, 10, 5), "logo.png", None, Crop(7, 3.5, 10, 5))
    )
    whole = mark_object(Image(Box(1, 2, 24, 12), "logo.gif"))
    assert mark_line(clipped) == "image  box 1,2 10x5  data logo.png  crop 7,3.5 10x5"
    assert mark_line(whole) == "image  box 1,2 24x12  data logo.gif"
