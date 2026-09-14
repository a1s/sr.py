"""The rules of doc/template.md#validation, and the helpers behind them.

The corpus in ``tests/templates/broken`` is where each rule is exercised
end to end; what is here is the reasoning a rule is built on: reading
``FINAL.`` names out of an expression, recognising a literal, and
deciding which elements give a band a height.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from sr.expr import compile_expression
from sr.template import load_text
from sr.template.load import Loaded
from sr.template.model import Element, Xref
from sr.template.validate import contributes, final_names, literal

FONT = Path(__file__).resolve().parents[2] / "example" / "fonts" / "Go-Regular.ttf"


def built(detail: str, height: str = " height=20") -> Loaded:
    """Load a template built round one detail band.

    Args:
        detail: What goes in the detail band.
        height: The band's height property, written out or left empty.

    """
    return load_text(
        f"""
report name="probe" {{
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail{height} {{
      {detail}
    }}
  }}
}}
""",
        file="probe.kdl",
    )


def elements(loaded: Loaded) -> tuple[Element | Xref, ...]:
    """Return the detail band's top-level elements.

    Args:
        loaded: What the load produced.

    """
    assert loaded.report is not None
    assert loaded.report.layout is not None
    assert loaded.report.layout.detail is not None
    return loaded.report.layout.detail.elements


# -- FINAL names ------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("FINAL.PAGE_NUMBER", ("PAGE_NUMBER",)),
        ("FINAL['total']", ("total",)),
        ("FINAL.THIS.region", ("THIS",)),
        ("(FINAL.a, FINAL.b)", ("a", "b")),
        ("PAGE_NUMBER", ()),
        ("other.FINAL", ()),
        ("FINAL[name]", ()),
        ("this is not an expression", ()),
    ],
)
def test_the_names_read_out_of_final(source: str, expected: tuple[str, ...]) -> None:
    assert tuple(sorted(final_names(source))) == tuple(sorted(expected))


# -- literals ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [("'top'", "top"), ('"top"', "top"), ("'a' + 'b'", None), ("name", None)],
)
def test_a_literal_is_recognised_only_where_it_is_one(
    source: str, expected: str | None
) -> None:
    assert literal(compile_expression(source)) == expected


# -- what gives a band a height ---------------------------------------


def test_a_field_sized_from_the_bottom_edge_does_not_give_the_band_height() -> None:
    loaded = built('field text="x" top=2 bottom=2', height="")
    assert not contributes(elements(loaded)[0])


def test_a_field_with_its_own_height_does() -> None:
    loaded = built('field text="x" top=2 height=10', height="")
    assert contributes(elements(loaded)[0])


def test_a_stretch_field_does_even_anchored_to_the_bottom() -> None:
    loaded = built('field text="x" stretch=#true top=2 bottom=2', height="")
    assert contributes(elements(loaded)[0])


def test_a_barcode_always_does() -> None:
    loaded = built('barcode type="Code128" text="x" top=2 bottom=2', height="")
    assert contributes(elements(loaded)[0])


def test_an_image_does_only_when_it_grows() -> None:
    grown = built('image file="a.png" scale="grow" top=2 bottom=2', height="")
    cut = built('image file="a.png" scale="cut" top=2 bottom=2', height="")
    assert contributes(elements(grown)[0])
    assert not contributes(elements(cut)[0])


def test_an_xref_is_asked_about_what_is_inside_it() -> None:
    empty = built(
        """
        xref type="url" target="'https://example.invalid/'" top=0 bottom=0 {
          field text="x" top=0 bottom=0
        }
        """,
        height="",
    )
    filled = built(
        """
        xref type="url" target="'https://example.invalid/'" top=0 bottom=0 {
          field text="x" stretch=#true top=0 bottom=0
        }
        """,
        height="",
    )
    assert not contributes(elements(empty)[0])
    assert contributes(elements(filled)[0])


def test_a_band_of_nothing_but_bottom_anchored_elements_warns() -> None:
    loaded = built('rectangle\nfield text="x" top=2', height="")
    assert loaded.ok
    assert any("collapses to nothing" in str(one) for one in loaded.warnings)


def test_a_band_that_declares_a_height_does_not_warn() -> None:
    loaded = built("rectangle", height=" height=10")
    assert loaded.ok
    assert not loaded.warnings


def test_an_empty_band_is_not_worth_warning_about() -> None:
    loaded = built("", height="")
    assert not loaded.warnings


# -- the namespace boundary -------------------------------------------


def test_an_embedded_layout_resolves_its_own_names_and_not_the_reports() -> None:
    loaded = load_text(
        f"""
report name="probe" {{
  variable "outer_total" expr="1" calc="sum"
  font "body" file="{FONT.as_posix()}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    embedded "lines" {{
      variable "inner_total" expr="1" calc="sum"
      detail height=10 {{
        field expr="FINAL.inner_total" evaltime="report" width=50
        field expr="FINAL.outer_total" evaltime="report" width=50 top=12
      }}
    }}
    detail height=10 {{
      field text="x"
      subreport embedded="lines" seq=1 data="[]"
    }}
  }}
}}
""",
        file="probe.kdl",
    )
    said = "\n".join(str(one) for one in loaded.errors)
    assert "'outer_total' is neither" in said
    assert "inner_total" not in said
