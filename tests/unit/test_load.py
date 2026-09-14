"""Reading a KDL document into the template model."""

from __future__ import annotations

import base64
import zlib
from pathlib import Path

import pytest

from sr.errors import TemplateError
from sr.template import Options, load, load_text
from sr.template.load import Loaded, elements_of, sections_of
from sr.template.model import Field, Image, Line, Rectangle, Section, Xref

FONT = Path(__file__).resolve().parents[2] / "example" / "fonts" / "Go-Regular.ttf"

SKELETON = """
report name="probe" {{
  {declarations}
  font "body" file="{font}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    {layout}
    detail height=20 {{
      {detail}
    }}
  }}
}}
"""


def built(
    detail: str = 'field text="x"',
    options: Options | None = None,
    **parts: str,
) -> Loaded:
    """Load a template built round one detail band.

    Args:
        detail: What goes in the detail band.
        options: What to ask of the load; the defaults otherwise.
        **parts: ``declarations`` above the layout, ``layout`` inside it.

    """
    text = SKELETON.format(
        font=FONT.as_posix(),
        declarations=parts.get("declarations", ""),
        layout=parts.get("layout", ""),
        detail=detail,
    )
    return load_text(text, file="probe.kdl", options=options)


def joined(loaded: Loaded) -> str:
    """Return every error of a load, one per line, for a failed assertion.

    Args:
        loaded: What the load produced.

    """
    return "\n".join(str(one) for one in loaded.errors)


def band(loaded: Loaded) -> Section:
    """Return the detail band of a template built by :func:`built`.

    Args:
        loaded: What the load produced.

    """
    assert loaded.report is not None
    assert loaded.report.layout is not None
    assert loaded.report.layout.detail is not None
    return loaded.report.layout.detail


# -- ordering ---------------------------------------------------------


def test_elements_keep_document_order_because_that_is_paint_order() -> None:
    loaded = built(
        detail="""
        rectangle
        field text="on top"
        line width=1
        """
    )
    detail = band(loaded)
    assert [type(one) for one in detail.elements] == [Rectangle, Field, Line]


def test_subreports_are_ordered_by_seq_and_not_by_position() -> None:
    loaded = built(
        layout="""
        embedded "a" { detail height=5 { field text="a" } }
        embedded "b" { detail height=5 { field text="b" } }
        """,
        detail="""
        field text="x"
        subreport embedded="a" seq=5 data="[]"
        subreport embedded="b" seq=-1 data="[]"
        """,
    )
    assert [one.seq for one in band(loaded).subreports] == [-1, 5]


def test_a_tie_in_seq_breaks_on_document_order() -> None:
    loaded = built(
        layout="""
        embedded "first" { detail height=5 { field text="a" } }
        embedded "second" { detail height=5 { field text="b" } }
        """,
        detail="""
        field text="x"
        subreport embedded="first" seq=0 data="[]"
        subreport embedded="second" seq=0 data="[]"
        """,
    )
    assert [one.embedded for one in band(loaded).subreports] == ["first", "second"]
    assert [one.order for one in band(loaded).subreports] == [0, 1]


def test_an_xref_holds_its_own_elements_and_the_band_sees_them() -> None:
    loaded = built(
        detail="""
        xref type="url" target="'https://example.invalid/'" {
          field text="inside"
          rectangle
        }
        """
    )
    detail = band(loaded)
    assert [type(one) for one in detail.elements] == [Xref]
    assert [type(one) for one in elements_of(detail)] == [Field, Rectangle]


# -- geometry ---------------------------------------------------------


def test_width_on_a_line_is_the_pen_and_not_the_box() -> None:
    loaded = built(detail="line width=2 left=0 right=0 height=0")
    line = band(loaded).elements[0]
    assert isinstance(line, Line)
    assert line.stroke == 2
    assert line.box.across.size is None
    assert (line.box.across.start, line.box.across.end) == (0.0, 0.0)


def test_width_on_a_rectangle_is_the_pen_too() -> None:
    loaded = built(detail="rectangle width=1")
    shape = band(loaded).elements[0]
    assert isinstance(shape, Rectangle)
    assert shape.stroke == 1
    assert shape.box.across.size is None


def test_width_on_a_field_is_the_box() -> None:
    loaded = built(detail='field text="x" width=30')
    assert band(loaded).elements[0].box.across.size == 30


def test_x_and_y_are_the_other_spelling_of_left_and_top() -> None:
    loaded = built(detail='field text="x" x=5 y=7 width=10 height=8')
    box = band(loaded).elements[0].box
    assert (box.across.start, box.down.start) == (5, 7)


def test_a_dimension_reaches_the_model_in_points() -> None:
    loaded = built(detail='field text="x" left="12mm" width="1in"')
    box = band(loaded).elements[0].box
    assert (box.across.start, box.across.size) == (34.016, 72)


# -- band height ------------------------------------------------------


def test_a_band_that_says_nothing_has_no_declared_height() -> None:
    loaded = load_text(
        SKELETON.format(
            font=FONT.as_posix(), declarations="", layout="", detail='field text="x"'
        ).replace("detail height=20", "detail"),
        file="probe.kdl",
    )
    assert band(loaded).height is None


def test_auto_is_the_same_as_saying_nothing() -> None:
    loaded = load_text(
        SKELETON.format(
            font=FONT.as_posix(), declarations="", layout="", detail='field text="x"'
        ).replace("detail height=20", 'detail height="auto"'),
        file="probe.kdl",
    )
    assert band(loaded).height is None


def test_a_declared_zero_is_not_the_same_as_auto() -> None:
    loaded = load_text(
        SKELETON.format(
            font=FONT.as_posix(), declarations="", layout="", detail='field text="x"'
        ).replace("detail height=20", "detail height=0"),
        file="probe.kdl",
    )
    assert band(loaded).height == 0.0


# -- blobs ------------------------------------------------------------


def test_a_base64_blob_is_decoded_across_the_lines_it_was_written_on() -> None:
    packed = base64.b64encode(b"the larch").decode("ascii")
    # A long blob is written as a multi-line string, so the decoder
    # meets the line breaks the template laid the base64 out on.
    loaded = built(
        declarations=f'''
        data "blob" encoding="base64" {{
          content """
            {packed[:4]}
            {packed[4:]}
            """
        }}
        '''
    )
    assert loaded.report is not None
    assert loaded.report.data[0].content == b"the larch"


def test_a_compressed_blob_is_decompressed() -> None:
    packed = base64.b64encode(zlib.compress(b"the larch")).decode("ascii")
    loaded = built(
        declarations=f"""
        data "blob" encoding="base64" compress="zlib" {{
          content "{packed}"
        }}
        """
    )
    assert loaded.report is not None
    assert loaded.report.data[0].content == b"the larch"


def test_a_blob_with_no_encoding_is_the_text_as_utf_8() -> None:
    loaded = built(declarations='data "blob" { content "Šķūnis" }')
    assert loaded.report is not None
    assert loaded.report.data[0].content == "Šķūnis".encode()


def test_a_corrupt_blob_is_reported_rather_than_salvaged() -> None:
    loaded = built(
        declarations='data "blob" encoding="base64" { content "not base64!!" }'
    )
    assert any("not base64" in str(one) for one in loaded.errors)


# -- embedded layouts -------------------------------------------------


def test_an_embedded_name_resolves_to_a_qualified_name() -> None:
    loaded = built(
        layout="""
        embedded "lines" {
          detail height=5 { field text="a" }
        }
        """,
        detail="""
        field text="x"
        subreport embedded="lines" seq=1 data="[]"
        """,
    )
    assert band(loaded).subreports[0].scope == ("lines",)
    assert loaded.report is not None
    assert ("lines",) in loaded.report.layouts


def test_a_layout_may_name_itself_so_that_it_can_walk_a_tree() -> None:
    loaded = built(
        layout="""
        embedded "node" {
          detail height=5 {
            field text="a"
            subreport embedded="node" seq=1 data="[]"
          }
        }
        """,
        detail='field text="x"',
    )
    assert loaded.report is not None
    inner = loaded.report.layouts[("node",)]
    assert inner.detail is not None
    assert inner.detail.subreports[0].scope == ("node",)


def test_a_nested_layout_is_private_to_its_parent() -> None:
    loaded = built(
        layout="""
        embedded "outer" {
          embedded "inner" {
            detail height=5 { field text="a" }
          }
          detail height=5 {
            field text="b"
            subreport embedded="inner" seq=1 data="[]"
          }
        }
        """,
        detail="""
        field text="x"
        subreport embedded="inner" seq=1 data="[]"
        """,
    )
    assert loaded.report is not None
    assert ("outer", "inner") in loaded.report.layouts
    inner = loaded.report.layouts[("outer",)]
    assert inner.detail is not None
    assert inner.detail.subreports[0].scope == ("outer", "inner")
    assert any("no embedded layout named" in str(one) for one in loaded.errors)


# -- referenced templates ---------------------------------------------


def test_a_template_named_twice_is_read_once(tmp_path: Path) -> None:
    inner = tmp_path / "inner.kdl"
    inner.write_text(
        f"""
report name="inner" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{ field text="y" }}
  }}
}}
""",
        encoding="utf-8",
    )
    host = tmp_path / "host.kdl"
    host.write_text(
        f"""
report name="host" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    title height=10 {{
      field text="t"
      subreport template="inner.kdl" seq=1 data="[]"
    }}
    detail height=10 {{
      field text="x"
      subreport template="inner.kdl" seq=1 data="[]"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    loaded = load(host)
    assert loaded.ok, "\n".join(str(one) for one in loaded.errors)
    assert loaded.report is not None
    found = [
        one.report for band in sections_of(loaded.report) for one in band.subreports
    ]
    assert len(found) == 2
    assert found[0] is found[1]


def test_basedir_moves_where_a_relative_path_resolves(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "inner.kdl").write_text(
        f"""
report name="inner" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{ field text="y" }}
  }}
}}
""",
        encoding="utf-8",
    )
    host = tmp_path / "host.kdl"
    host.write_text(
        f"""
report name="host" basedir="shared" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{
      field text="x"
      subreport template="inner.kdl" seq=1 data="[]"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    loaded = load(host)
    assert loaded.ok, "\n".join(str(one) for one in loaded.errors)
    assert loaded.report is not None
    assert loaded.report.basedir == (tmp_path / "shared").resolve()


# -- what a load returns ----------------------------------------------


def test_a_file_that_is_not_there_is_a_diagnostic_and_not_a_traceback() -> None:
    loaded = load(Path("no", "such", "template.kdl"))
    assert loaded.report is None
    assert any("cannot read" in str(one) for one in loaded.errors)


def test_a_document_that_is_not_kdl_is_a_diagnostic() -> None:
    loaded = load_text("report {", file="probe.kdl")
    assert loaded.report is None
    assert any("parse error" in str(one) for one in loaded.errors)


def test_requiring_a_broken_template_raises_with_every_diagnostic() -> None:
    loaded = built(detail='field text="x" left=1 right=1 width=1')
    with pytest.raises(TemplateError) as refused:
        loaded.require()
    assert refused.value.diagnostics == loaded.errors


def test_a_good_template_is_returned_by_require() -> None:
    loaded = built()
    assert loaded.require() is loaded.report


def test_an_image_keeps_what_it_was_told_about_its_bytes() -> None:
    loaded = built(detail='image file="logo.png" scale="fill" width=10 height=10')
    picture = band(loaded).elements[0]
    assert isinstance(picture, Image)
    assert (picture.file, picture.scale, picture.embed) == ("logo.png", "fill", True)


# -- names the format does not define ---------------------------------
#
# doc/template.md#unknown-names.  The KDL layer decides what one costs;
# these are about the loader's part: reading `accept`, and carrying
# what the caller asked into every file the load touches.


def test_an_unknown_name_does_not_refuse_the_template() -> None:
    loaded = built(detail='field text="x" wobble=#true')
    assert loaded.ok
    assert any("unknown property `wobble`" in str(one) for one in loaded.warnings)


def test_an_accept_node_registers_a_name() -> None:
    loaded = built(declarations='accept "wobble"', detail='field text="x" wobble=#true')
    assert loaded.ok
    assert not loaded.warnings


def test_accept_nodes_add_up_rather_than_the_last_one_winning() -> None:
    loaded = built(
        declarations="""
        accept "wobble"
        accept "sprocket" "teeth"
        """,
        detail="""
        field text="x" wobble=#true
        sprocket teeth=1
        """,
    )
    assert loaded.ok
    assert not loaded.warnings


def test_a_name_the_caller_accepts_is_registered_too() -> None:
    loaded = built(
        detail='field text="x" wobble=#true',
        options=Options(accepted=frozenset({"wobble"})),
    )
    assert loaded.ok
    assert not loaded.warnings


def test_strict_names_makes_an_unknown_name_an_error() -> None:
    loaded = built(detail='field text="x" wobble=#true', options=Options(strict=True))
    assert not loaded.ok
    assert any("unknown property `wobble`" in str(one) for one in loaded.errors)


def test_a_near_miss_is_an_error_without_being_asked() -> None:
    loaded = built(detail='field text="x" printwhn="True"')
    assert not loaded.ok
    assert any("did you mean `printwhen`?" in str(one) for one in loaded.errors)


def test_nothing_inside_an_unknown_node_is_read() -> None:
    # One unknown node is one diagnostic, however much it contains: its
    # children and properties are part of what the format does not define.
    loaded = built(
        detail="""
        field text="x"
        sprocket teeth=1 {
          cog ratio=2 printwhn="True"
        }
        """
    )
    assert loaded.ok
    assert len(loaded.warnings) == 1


def test_an_accept_reaches_only_the_document_that_declares_it(tmp_path: Path) -> None:
    inner = tmp_path / "inner.kdl"
    inner.write_text(
        f"""
report name="inner" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{ field text="y" wobble=#true }}
  }}
}}
""",
        encoding="utf-8",
    )
    host = tmp_path / "host.kdl"
    host.write_text(
        f"""
report name="host" {{
  accept "wobble"
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{
      field text="x" wobble=#true
      subreport template="inner.kdl" seq=1 data="[]"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    loaded = load(host)
    assert loaded.ok, joined(loaded)
    said = [str(one) for one in loaded.warnings]
    assert len(said) == 1
    assert "inner.kdl" in said[0]


def test_what_the_caller_accepts_reaches_a_referenced_template(
    tmp_path: Path,
) -> None:
    inner = tmp_path / "inner.kdl"
    inner.write_text(
        f"""
report name="inner" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{ field text="y" wobble=#true }}
  }}
}}
""",
        encoding="utf-8",
    )
    host = tmp_path / "host.kdl"
    host.write_text(
        f"""
report name="host" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{
      field text="x"
      subreport template="inner.kdl" seq=1 data="[]"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    loaded = load(host, Options(accepted=frozenset({"wobble"})))
    assert loaded.ok, joined(loaded)
    assert not loaded.warnings
