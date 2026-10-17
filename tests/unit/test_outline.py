"""Outline entries, and the xrefs that point at them.

doc/printout.md#outline is the shape: an entry is a mark of the band
that selected it, at the band's top-left corner, written after the
band's own marks, and moving with the band from then on.  The rest
is settled once the document is: every xref to an entry finds one,
an entry keeps its name only where an xref targets it, and no level
is deeper than the one before it allows.

The differential suite holds the agreeing part of this to the reference
over the `outline/` probes.  What is here is each rule on its own.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.errors import BuildError, TemplateError
from sr.printout.model import Outline, Printout, Text

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()

# A page 100 by 60 with 5pt margins, which leaves a frame 50 tall.
# Size 5 sets a line 6pt tall.
HEAD = """
report name="Outline" {
  font "body" file="FACE" size=5
  layout width=100 height=60 leftmargin=5 rightmargin=5 topmargin=5 \\
         bottommargin=5 {
    style font="body" color="black"
BANDS
  }
}
"""


def built(tmp_path: Path, bands: str, count: int = 1, **options: Any) -> Printout:
    """Build a report over ``count`` records and return its printout.

    Args:
        tmp_path: Where to write the template and the data.
        bands: What goes under `layout`, after its style.
        count: How many records, whose contents nothing reads.
        **options: What to pass to the build.

    """
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("BANDS", bands)
    template.write_bytes(text.encode("utf-8"))
    data = tmp_path / "rows.jsonl"
    rows = "".join(json.dumps({"n": index}) + "\n" for index in range(count))
    data.write_bytes(rows.encode("utf-8"))
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True, **options)
    return build(template, data, asked).printout


def entries(printout: Printout) -> list[Outline]:
    """Return every outline entry, in document order."""
    return [
        mark
        for page in printout.pages
        for mark in page.marks
        if isinstance(mark, Outline)
    ]


def point(entry: Outline) -> tuple[float, float]:
    """Return where an entry points."""
    return (entry.box.x, entry.box.y)


# -- where an entry is ------------------------------------------------


def test_an_entry_sits_at_its_bands_top_left_after_its_marks(
    tmp_path: Path,
) -> None:
    printout = built(
        tmp_path,
        "    title height=8 {\n"
        "      outline title=\"'T'\"\n"
        '      field text="T" left=2 top=1 width=10 height=6\n'
        "    }\n"
        "    detail height=0",
    )
    text, entry = printout.pages[0].marks
    assert isinstance(text, Text)
    assert isinstance(entry, Outline)
    assert (entry.title, entry.level, entry.closed) == ("T", 1, False)
    assert point(entry) == (5, 5)


def test_every_page_header_brings_an_entry(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    header height=6 { outline title=\"'page %d' % PAGE_NUMBER\" }\n"
        '    detail height=20 { field text="d" left=0 top=0 width=10 height=6 }',
        count=4,
    )
    assert [one.title for one in entries(printout)] == ["page 1", "page 2"]
    assert all(point(one) == (5, 5) for one in entries(printout))


def test_an_entry_in_a_column_points_at_the_column(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    columns count=2 gap=10 {\n"
        "      header height=4 { outline title=\"'c%d' % COLUMN_NUMBER\" }\n"
        "    }\n"
        '    detail height=20 { field text="d" left=0 top=0 width=10 height=6 }',
        count=4,
    )
    assert [(one.title, point(one)) for one in entries(printout)] == [
        ("c1", (5, 5)),
        ("c2", (55, 5)),
    ]


def test_a_split_band_carries_its_entry_in_its_head(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    title height=20 { field text="T" left=0 top=0 width=10 height=6 }\n'
        "    detail split=#true {\n"
        "      outline title=\"'d'\"\n"
        '      field text="a\\nb\\nc\\nd\\ne\\nf\\ng"'
        " left=0 top=0 width=40 stretch=#true\n"
        "    }",
    )
    first, second = printout.pages
    assert [mark.kind for mark in first.marks] == ["text", "text", "outline"]
    assert point(entries(printout)[0]) == (5, 25)
    assert [mark.kind for mark in second.marks] == ["text"]


def test_an_entry_is_not_content_a_cut_could_divide(tmp_path: Path) -> None:
    """Above the cut would be the entry alone, so the band moves whole."""
    printout = built(
        tmp_path,
        '    title height=30 { field text="T" left=0 top=0 width=10 height=6 }\n'
        "    detail split=#true {\n"
        "      outline title=\"'d'\"\n"
        '      field text="a\\nb\\nc" left=0 top=22 width=40 stretch=#true\n'
        "    }",
    )
    first, second = printout.pages
    assert [mark.kind for mark in first.marks] == ["text"]
    assert [mark.kind for mark in second.marks] == ["text", "outline"]
    assert point(entries(printout)[0]) == (5, 5)


def test_balancing_moves_an_entry_with_its_band(tmp_path: Path) -> None:
    """The reference leaves it in the column the fill put it in."""
    printout = built(
        tmp_path,
        "    columns count=2 gap=10 balance=#true\n"
        "    detail height=10 {\n"
        "      outline title=\"'r%d' % ITEM_NUMBER\"\n"
        "      field expr=\"'r%d' % ITEM_NUMBER\" left=0 top=0 width=40 height=6\n"
        "    }",
        count=3,
    )
    found = {one.title: point(one) for one in entries(printout)}
    texts = {
        mark.lines[0]: (mark.box.x, mark.box.y)
        for mark in printout.pages[0].marks
        if isinstance(mark, Text)
    }
    assert found == {"r1": (5, 5), "r2": (5, 15), "r3": (55, 5)}
    assert texts["r3"] == (55, 5)


# -- what an entry says -----------------------------------------------


def test_the_first_entry_whose_when_holds_is_the_one(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=10 {\n"
        '      outline title="\'odd\'" when="ITEM_NUMBER % 2"\n'
        '      outline title="\'even\'" when="not ITEM_NUMBER % 2"\n'
        "      outline title=\"'never'\"\n"
        "    }",
        count=3,
    )
    assert [one.title for one in entries(printout)] == ["odd", "even", "odd"]


def test_a_band_with_no_entry_selected_has_none(tmp_path: Path) -> None:
    printout = built(
        tmp_path, '    detail height=10 { outline title="\'x\'" when="False" }'
    )
    assert entries(printout) == []


@pytest.mark.parametrize(
    ("title", "said"),
    [
        ("None", "None"),
        ("decimal('1.50')", "1.50"),
        ("[1, 'a']", '[1, "a"]'),
        ("True", "True"),
        ("ITEM_NUMBER", "1"),
        ("BUILD_TIME", "2026-08-04 09:12:44 +0000 UTC"),
    ],
)
def test_a_title_is_text_as_a_fields_default_format_makes_it(
    tmp_path: Path, title: str, said: str
) -> None:
    printout = built(tmp_path, f'    detail height=10 {{ outline title="{title}" }}')
    assert entries(printout)[0].title == said


def test_level_and_closed_are_as_declared(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    title height=5 { outline title=\"'t'\" }\n"
        "    detail height=10 { outline title=\"'d'\" level=2 closed=#true }",
    )
    assert [(one.level, one.closed) for one in entries(printout)] == [
        (1, False),
        (2, True),
    ]


# -- names, and what targets them -------------------------------------


TARGETED = (
    "    title height=5 { outline title=\"'t'\" name=\"'top'\" }\n"
    "    detail height=10 { outline title=\"'d'\" name=\"'row'\" }\n"
    "    summary height=6 {\n"
    '      xref type="outline" target="\'top\'" left=0 top=0 width=20 height=5 {\n'
    '        field text="up" left=0 top=0 width=20 height=5\n'
    "      }\n"
    "    }"
)


def test_a_name_is_kept_only_where_an_xref_targets_it(tmp_path: Path) -> None:
    """The reference writes every name it is given."""
    printout = built(tmp_path, TARGETED)
    assert [(one.title, one.name) for one in entries(printout)] == [
        ("t", "top"),
        ("d", None),
    ]


def test_an_empty_name_is_no_name(tmp_path: Path) -> None:
    printout = built(
        tmp_path, "    detail height=10 { outline title=\"'d'\" name=\"''\" }"
    )
    assert entries(printout)[0].name is None


def test_an_xref_may_name_an_entry_a_later_page_holds(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        '    title height=45 { xref type="outline" target="\'end\'" height=5 }\n'
        "    detail height=0\n"
        "    summary height=10 { outline title=\"'end'\" name=\"'end'\" }",
    )
    assert len(printout.pages) == 2
    assert entries(printout)[0].name == "end"


def test_an_xref_whose_entry_never_prints_is_refused(tmp_path: Path) -> None:
    """The target is there, on a band that never prints, so the load passes."""
    with pytest.raises(BuildError) as refused:
        built(
            tmp_path,
            '    title printwhen="False" { outline title="\'Top\'" name="\'top\'" }\n'
            "    detail height=10 {\n"
            '      xref type="outline" target="\'top\'" height=5\n'
            "    }",
        )
    said = str(refused.value)
    assert "report > layout > detail > xref target=" in said
    assert "record 0" in said
    assert "no outline is named 'top'" in said


def test_a_name_two_entries_carry_is_still_one_target(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    detail height=10 { outline title=\"'d'\" name=\"'same'\" }\n"
        '    summary height=5 { xref type="outline" target="\'same\'" height=5 }',
        count=2,
    )
    assert [one.name for one in entries(printout)] == ["same", "same"]


# -- levels -----------------------------------------------------------


def test_a_level_that_jumps_is_written_as_its_depth(tmp_path: Path) -> None:
    """The reference writes the level as declared, breaking an invariant."""
    printout = built(
        tmp_path,
        "    title height=5 { outline title=\"'t'\" }\n"
        "    detail height=10 { outline title=\"'d'\" level=3 }",
        count=2,
    )
    assert [one.level for one in entries(printout)] == [1, 2, 2]


def test_entries_with_nothing_above_them_are_siblings_at_1(tmp_path: Path) -> None:
    """The title that would have been their parent does not print."""
    printout = built(
        tmp_path,
        '    title height=5 printwhen="False" { outline title="\'t\'" }\n'
        "    detail height=10 { outline title=\"'d'\" level=2 }",
        count=2,
    )
    assert [one.level for one in entries(printout)] == [1, 1]


def test_a_shallower_entry_after_a_jump_is_a_sibling_not_a_child(
    tmp_path: Path,
) -> None:
    """Declared 1, 4, 3: the 3 is the 1's child, as the 4 is, and not the 4's."""
    printout = built(
        tmp_path,
        "    title height=5 { outline title=\"'t'\" }\n"
        "    detail height=10 {\n"
        '      outline title="\'deep\'" level=4 when="ITEM_NUMBER == 1"\n'
        "      outline title=\"'less'\" level=3\n"
        "    }",
        count=2,
    )
    assert [one.level for one in entries(printout)] == [1, 2, 2]


def test_levels_without_a_gap_are_written_as_declared(tmp_path: Path) -> None:
    printout = built(
        tmp_path,
        "    title height=5 { outline title=\"'t'\" }\n"
        "    detail height=5 {\n"
        '      outline title="\'a\'" level=2 when="ITEM_NUMBER == 1"\n'
        '      outline title="\'b\'" level=3 when="ITEM_NUMBER == 2"\n'
        '      outline title="\'c\'" level=2 when="ITEM_NUMBER == 3"\n'
        "      outline title=\"'d'\" level=1\n"
        "    }",
        count=4,
    )
    assert [one.level for one in entries(printout)] == [1, 2, 3, 2, 1]


def test_a_level_below_1_is_refused_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateError) as refused:
        built(tmp_path, "    detail height=10 { outline title=\"'d'\" level=0 }")
    assert "an outline level must be at least 1, not 0" in str(refused.value)
