"""A barcode in a band: its size, its box, `grow`, and deferred values.

The differential suite holds all of this to the reference
over the `barcode/` probes.  What is here is each rule of
doc/layout.md#a-barcode-in-its-box and doc/layout.md#re-measurement
on its own, and the two places this engine and the reference part company --
the rounding of a barcode's lengths, and Code 93's second checkcharacter --
are tested for this engine's answer.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.barcode import encode
from sr.errors import BuildError
from sr.printout.inspect import mark_line
from sr.printout.model import Barcode, Line, Printout
from sr.template.load import load_text

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

HEAD = """
report name="Barcodes" {
  font "body" file="FACE" size=10
  data "blob" { content "BLOB"; }
  layout width=500 height=HEIGHT leftmargin=0 rightmargin=0 topmargin=0 \\
         bottommargin=0 {
    style font="body" color="red" bgcolor="navy"
BANDS
  }
}
"""


def built(
    tmp_path: Path,
    bands: str,
    rows: list[dict[str, Any]] | None = None,
    height: int = 800,
) -> Printout:
    """Build a report and return its printout.

    Args:
        tmp_path: Where to write the template and the data.
        bands: What goes under `layout`, after its style.
        rows: The records, or ``None`` for one record nothing reads.
        height: The page's height.

    """
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("HEIGHT", str(height))
    template.write_text(text.replace("BANDS", bands), encoding="utf-8")
    data = tmp_path / "rows.jsonl"
    lines = [json.dumps(one) for one in (rows if rows is not None else [{"n": 1}])]
    data.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    options = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(template, data, options).printout


def symbols(printout: Printout, page: int = 0) -> list[Barcode]:
    """Return the barcode marks of a page, in paint order."""
    return [one for one in printout.pages[page].marks if isinstance(one, Barcode)]


def box(mark: Barcode) -> tuple[float, float, float, float]:
    """Return a mark's box as a tuple."""
    return (mark.box.x, mark.box.y, mark.box.width, mark.box.height)


def detail(*elements: str) -> str:
    """Return a detail band holding these elements."""
    inside = "\n".join(f"      {one}" for one in elements)
    return f"    detail {{\n{inside}\n    }}"


A = 'barcode type="Code128" text="A" module=1'


# -- the symbol's size ------------------------------------------------


def test_a_1d_symbols_bars_are_a_quarter_inch_at_least(tmp_path: Path) -> None:
    (mark,) = symbols(built(tmp_path, detail(A)))
    assert box(mark) == (0, 0, 66, 18)
    assert mark.stripes[0] == mark.stripes[-1] == 10
    assert sum(mark.stripes) == 66


def test_a_1d_symbols_bars_are_fifteen_per_cent_of_a_long_one(
    tmp_path: Path,
) -> None:
    long = 'barcode type="Code128" text="ABCDEFGHIJ" module=1'
    (mark,) = symbols(built(tmp_path, detail(long)))
    assert box(mark) == (0, 0, 165, 24.75)


def test_a_barcodes_lengths_are_rounded_as_they_are_computed(
    tmp_path: Path,
) -> None:
    # 66 modules of 10mil is 47.519999999999996 in binary64.
    (mark,) = symbols(built(tmp_path, detail('barcode type="Code128" text="A"')))
    assert mark.module == 0.72
    assert box(mark) == (0, 0, 47.52, 18)


def test_a_2d_symbol_is_a_module_per_row_and_per_column(tmp_path: Path) -> None:
    printout = built(tmp_path, detail('barcode type="QR-L" text="A" module=2'))
    (mark,) = symbols(printout)
    assert box(mark) == (0, 0, 58, 58)
    assert len(mark.rows) == 29
    assert all(sum(row) == 29 for row in mark.rows)
    assert mark.stripes == ()


def test_vertical_turns_the_box_and_keeps_the_runs(tmp_path: Path) -> None:
    printout = built(tmp_path, detail(A, f"{A} top=100 vertical=#true"))
    across, down = symbols(printout)
    assert box(down) == (0, 100, 18, 66)
    assert down.vertical
    assert down.stripes == across.stripes


# -- the box ----------------------------------------------------------


def test_a_symbol_wider_than_its_box_overhangs_it_by_halign(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            f'{A} left=200 width=10 halign="left"',
            f'{A} left=200 width=10 top=30 halign="right"',
            f'{A} left=200 width=10 top=60 halign="center"',
        ),
    )
    assert [one.box.x for one in symbols(printout)] == [200, 144, 172]


def test_a_symbol_taller_than_its_box_pushes_the_box_down(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'barcode type="QR-L" text="A" module=1 top=10 height=5 valign="bottom"',
            "line left=490 width=0",
        ),
    )
    (mark,) = symbols(printout)
    assert box(mark) == (0, 10, 29, 29)
    rule = printout.pages[0].marks[1]
    assert isinstance(rule, Line)
    assert rule.box.height == 39


def test_a_symbol_in_a_band_dependent_box_overhangs_it_by_valign(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        detail(
            'barcode type="QR-L" text="A" module=1 top=0 bottom=0 valign="bottom"',
            'field text="x" top=0 height=10 left=100 width=50',
        ),
    )
    (mark,) = symbols(printout)
    assert box(mark) == (0, -19, 29, 29)


def test_a_symbol_sits_in_a_larger_box_by_halign_and_valign(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(f'{A} left=300 width=100 height=40 halign="center" valign="bottom"'),
    )
    (mark,) = symbols(printout)
    assert box(mark) == (317, 22, 66, 18)


def test_a_clamp_limits_the_box_and_never_the_symbol(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            f'{A} width=100 maxwidth=80 halign="right"',
            f"{A} left=200 height=10 maxheight=5",
            f"{A} left=300 width=40 height=100 maxheight=70 vertical=#true"
            ' valign="bottom"',
        ),
    )
    right, short, tall = symbols(printout)
    assert box(right) == (14, 0, 66, 18)
    assert box(short) == (200, 0, 66, 18)
    assert box(tall) == (300, 4, 18, 66)


# -- grow -------------------------------------------------------------


def test_grow_reaches_a_1d_symbols_bars_across_the_box(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            f"{A} grow=#true height=40",
            f"{A} grow=#true left=100 width=50 vertical=#true",
            f"{A} grow=#true left=200 height=10",
        ),
    )
    tall, wide, short = symbols(printout)
    assert box(tall) == (0, 0, 66, 40)
    assert box(wide) == (100, 0, 50, 66)
    assert box(short) == (200, 0, 66, 18)


def test_grow_gives_a_2d_symbol_the_module_that_fills_the_shorter_side(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        detail(
            'barcode type="QR-L" text="A" module=1 grow=#true width=58 height=100',
            'barcode type="QR-L" text="A" module=1 grow=#true left=100 width=7'
            " height=7",
            'barcode type="QR-L" text="A" module=1 grow=#true left=200 width=80',
        ),
    )
    filled, small, unbounded = symbols(printout)
    assert (filled.module, box(filled)) == (2, (0, 0, 58, 58))
    assert (small.module, box(small)) == (1, (100, 0, 29, 29))
    assert (unbounded.module, box(unbounded)) == (1, (200, 0, 29, 29))


def test_a_grown_module_is_rounded_down_so_the_symbol_fits_its_box(
    tmp_path: Path,
) -> None:
    # 50 over 29 modules is 1.7241..., which half away from zero would
    # make 1.724 as well; 66 over 29 is 2.2758..., which it would make
    # 2.276, and 29 of those are 66.004 in a box of 66.
    printout = built(
        tmp_path,
        detail(
            'barcode type="QR-L" text="A" module=1 grow=#true width=50 height=50',
            'barcode type="QR-L" text="A" module=1 grow=#true left=100 width=66'
            " height=66",
        ),
    )
    fifty, sixty_six = symbols(printout)
    assert (fifty.module, box(fifty)) == (1.724, (0, 0, 49.996, 49.996))
    assert (sixty_six.module, box(sixty_six)) == (2.275, (100, 0, 65.975, 65.975))


def test_a_grown_module_that_fits_exactly_is_kept_whole(tmp_path: Path) -> None:
    # 22.185 over 29 is 0.765 exactly, which a binary64 quotient rounded
    # down makes 0.764.
    printout = built(
        tmp_path,
        detail(
            'barcode type="QR-L" text="A" module=0.5 grow=#true width=22.185'
            " height=22.185",
        ),
    )
    (mark,) = symbols(printout)
    assert (mark.module, box(mark)) == (0.765, (0, 0, 22.185, 22.185))


# -- content ----------------------------------------------------------


def test_format_applies_to_expr_and_nothing_else(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            'barcode type="Code128" expr="7" format="[%03d]" module=1',
            'barcode type="Code128" text="5" format="[%s]" module=1 top=20',
            'barcode type="Code128" data="blob" format="[%s]" module=1 top=40',
        ),
    )
    assert [one.value for one in symbols(printout)] == ["[007]", "5", "BLOB"]


def test_ink_and_paper_are_the_barcodes_own(tmp_path: Path) -> None:
    printout = built(tmp_path, detail(A, f'{A} top=20 ink="navy" paper="#FFE9B0"'))
    plain, coloured = symbols(printout)
    assert (plain.ink, plain.paper) == ("#000000", None)
    assert (coloured.ink, coloured.paper) == ("#000080", "#FFE9B0")


def test_a_value_the_type_cannot_encode_is_refused_with_its_record(
    tmp_path: Path,
) -> None:
    with pytest.raises(BuildError) as refused:
        built(
            tmp_path,
            detail('barcode type="2of5i" expr="v" module=1'),
            rows=[{"v": "12"}, {"v": "123"}],
        )
    said = str(refused.value)
    assert 'barcode 2of5i: cannot encode "123"' in said
    assert "3 is an odd number of them" in said
    assert refused.value.diagnostic.location.record == 1


def test_bytes_that_are_not_utf8_encode_as_u_fffd(tmp_path: Path) -> None:
    printout = built(
        tmp_path, detail('barcode type="Aztec" expr="str(b\'A\\\\xffB\')" module=1')
    )
    (mark,) = symbols(printout)
    assert mark.value == "A\ufffdB"
    assert mark.rows == encode("Aztec", "A\ufffdB").rows()


def test_a_parameter_byte_that_is_not_utf8_encodes_as_u_fffd(
    tmp_path: Path,
) -> None:
    template = tmp_path / "report.kdl"
    template.write_text(
        HEAD.replace("FACE", REGULAR)
        .replace("HEIGHT", "800")
        .replace('Barcodes" {', 'Barcodes" {\n  parameter "code" type="string"')
        .replace("BANDS", detail('barcode type="QR-M" expr="code" module=1')),
        encoding="utf-8",
    )
    data = tmp_path / "rows.jsonl"
    data.write_text('{"n": 1}\n', encoding="utf-8")
    # A command line on Linux hands Python such a byte as a surrogate.
    options = Options(
        build_time="2026-08-04T09:12:44Z",
        strict_fonts=True,
        params={"code": "A\udcffB"},
    )
    (mark,) = symbols(build(template, data, options).printout)
    assert mark.value == "A\ufffdB"
    assert mark.rows == encode("QR-M", "A\ufffdB").rows()


def test_code93_carries_both_check_characters(tmp_path: Path) -> None:
    # The reference leaves K out: probes/barcode/code93.kdl.
    printout = built(tmp_path, detail('barcode type="Code93" text="TEST93" module=1'))
    (mark,) = symbols(printout)
    # Start, six characters, C, K, and stop, nine modules each, the
    # termination bar, and two quiet zones.
    assert mark.box.width == 10 * 9 + 1 + 20


# -- the charset ------------------------------------------------------

CAFE = "Caf" + chr(0xE9)


def test_charset_and_eci_reach_the_symbol(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        detail(
            f'barcode type="Aztec" text="{CAFE}" module=1',
            f'barcode type="Aztec" text="{CAFE}" module=1 top=40'
            ' charset="iso-8859-1" eci=#true',
        ),
    )
    plain, named = symbols(printout)
    assert plain.rows == encode("Aztec", CAFE).rows()
    assert named.rows == encode("Aztec", CAFE, "iso-8859-1", eci=True).rows()
    assert named.rows != plain.rows


def test_a_character_outside_the_charset_is_refused_when_built(
    tmp_path: Path,
) -> None:
    with pytest.raises(BuildError) as refused:
        built(
            tmp_path,
            detail('barcode type="QR-M" expr="v" charset="iso-8859-1" module=1'),
            rows=[{"v": "5 " + chr(0x20AC)}],
        )
    said = str(refused.value)
    assert f'barcode QR-M: cannot encode "5 {chr(0x20AC)}"' in said
    assert f"{chr(0x20AC)!r} is not in ISO 8859-1" in said


# -- deferred ---------------------------------------------------------


PAGE_COUNT = 'expr="\'%d\' % FINAL.PAGE_COUNT" evaltime="page"'

# Two records, which each page below holds both of.
TWO = [{"n": 1}, {"n": 2}]


def test_a_shorter_value_sits_in_the_placeholders_room(tmp_path: Path) -> None:
    bands = detail(
        f'barcode type="Code128" {PAGE_COUNT} text="ABCDEF" module=1 width=200'
        ' height=40 halign="center" valign="center"'
    )
    first = symbols(built(tmp_path, bands, rows=TWO, height=100))[0]
    # The placeholder's symbol is 121 modules, its bars 18.15, centred in
    # 200 by 40; the value's is 66, centred in that room, bars kept.
    assert box(first) == (67, 10.925, 66, 18.15)
    assert first.value == "2"


def test_a_2d_value_keeps_its_module_in_the_room(tmp_path: Path) -> None:
    bands = detail(
        f'barcode type="QR-L" {PAGE_COUNT} text="ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"'
        ' module=1 width=60 height=60 halign="right" valign="bottom"'
    )
    first = symbols(built(tmp_path, bands, height=130))[0]
    assert box(first) == (31, 31, 29, 29)


def test_a_grown_2d_value_fills_the_room_again(tmp_path: Path) -> None:
    bands = detail(
        'barcode type="QR-L" expr="FINAL.PAGE_COUNT" format="%02d"'
        ' evaltime="page" text="99" module=1 grow=#true width=58 height=58'
    )
    first = symbols(built(tmp_path, bands, rows=TWO, height=130))[0]
    assert (first.module, box(first), first.value) == (2, (0, 0, 58, 58), "02")


def test_a_value_longer_than_its_placeholder_is_refused(tmp_path: Path) -> None:
    bands = detail(
        'barcode type="Code128" expr="\'abcdefgh%d\' % FINAL.PAGE_COUNT"'
        ' evaltime="page" text="A" module=1'
    )
    with pytest.raises(BuildError) as refused:
        built(tmp_path, bands)
    said = str(refused.value)
    assert 'the deferred value "abcdefgh1" needs 154 pt' in said
    assert 'its placeholder "A" reserved 66 pt' in said


def test_a_placeholder_the_type_cannot_encode_is_refused(tmp_path: Path) -> None:
    bands = detail(
        'barcode type="2of5i" expr="FINAL.PAGE_COUNT" format="%02d"'
        ' evaltime="page" text="999" module=1'
    )
    with pytest.raises(BuildError, match='cannot encode "999"'):
        built(tmp_path, bands)


# -- around the mark --------------------------------------------------


def test_inspect_counts_a_2d_symbols_rows(tmp_path: Path) -> None:
    printout = built(tmp_path, detail('barcode type="QR-L" text="A" module=1'))
    (mark,) = symbols(printout)
    record = {
        "kind": "barcode",
        "box": {"x": 0, "y": 0, "width": 29, "height": 29},
        "type": mark.symbology,
        "value": mark.value,
        "module": mark.module,
        "vertical": False,
        "ink": mark.ink,
        "rows": [list(row) for row in mark.rows],
    }
    assert mark_line(record) == (
        'barcode  box 0,0 29x29  QR-L  "A"  module 1  ink #000000  29 rows'
    )


@pytest.mark.parametrize(
    ("colours", "prop"),
    [
        ('ink="yellow"', "ink"),
        ('ink="black" paper="navy"', "paper"),
        ('ink="#C0C0C0" paper="white"', "ink"),
    ],
)
def test_a_pair_a_scanner_cannot_read_is_refused_at_load(
    colours: str, prop: str
) -> None:
    text = f"""
report name="Colours" {{
  layout width=200 height=200 {{
    detail {{
      barcode type="QR-H" text="x" {colours}
    }}
  }}
}}
"""
    (found,) = load_text(text).errors
    assert found.location.prop == prop
    assert "will not scan" in found.message


@pytest.mark.parametrize("prop", ["charset", "eci"])
def test_a_1d_type_takes_no_charset_and_no_eci(prop: str) -> None:
    value = '"utf-8"' if prop == "charset" else "#false"
    text = f"""
report name="Charset" {{
  layout width=200 height=200 {{
    detail {{
      barcode type="Code39" text="X" {prop}={value}
    }}
  }}
}}
"""
    (found,) = load_text(text).errors
    assert found.location.prop == prop
    assert "encodes characters rather than bytes" in found.message
