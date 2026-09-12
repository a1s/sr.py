"""The KDL adapter: typed access, node paths, cardinality.

The diagnostics are checked as text, because their wording is the part
a person reads.  Every message asserted here was read off the reference
binary, so the two engines report the same mistake the same way, except
for the line number, which ``ckdl`` does not supply.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from sr import kdl
from sr.errors import TemplateError

TEMPLATE = """
report name="sales" {
  font "body" file="Go-Regular.ttf" size=10
  layout pagesize="A4" landscape=#true {
    style color="0,89,0"
    detail height="12mm" {
      field text="Total:" left=0 top=0 width="2cm" height=12
      rectangle left=0 top=0 width=1 height=5
      rectangle left=10 top=0 width=1 height=5
    }
  }
}
"""


def document(text: str = TEMPLATE, file: str = "t.kdl") -> kdl.Document:
    return kdl.parse(text, file=file)


def field_of(doc: kdl.Document) -> kdl.Node:
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    detail = layout.child("detail")
    assert detail is not None
    found = detail.child("field")
    assert found is not None
    return found


def test_the_root_is_the_node_the_format_asks_for() -> None:
    doc = document()
    report = doc.only_root("report")
    assert report is not None
    assert report.name == "report"
    assert not doc.diagnostics


def test_a_document_that_is_not_one_report_says_so() -> None:
    doc = document('node "x"\nreport name="y" { }\n')
    assert doc.only_root("report") is None
    assert str(doc.diagnostics) == (
        "t.kdl: <document>: a template's root node is a single `report`"
    )


def test_a_node_path_is_built_as_the_tree_is_walked() -> None:
    assert str(field_of(document()).path) == "report > layout > detail > field"


def test_a_path_shows_the_identity_argument() -> None:
    report = document().only_root("report")
    assert report is not None
    font = report.child("font")
    assert font is not None
    assert str(font.path) == 'report > font "body"'
    assert font.identity == "body"


def test_a_node_without_a_string_argument_has_no_identity() -> None:
    report = document().only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    assert layout.identity is None


def test_a_property_comes_back_typed() -> None:
    doc = document()
    field = field_of(doc)
    assert field.string("text") == "Total:"
    assert field.dimension("width") == 56.693
    assert field.dimension("height") == 12.0
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    assert layout.boolean("landscape") is True
    font = report.child("font")
    assert font is not None
    assert font.integer("size") == 10
    style = layout.child("style")
    assert style is not None
    assert style.color("color") == "#005900"
    assert not doc.diagnostics


def test_an_absent_property_is_the_default_and_no_diagnostic() -> None:
    doc = document()
    field = field_of(doc)
    assert field.string("format") is None
    assert field.string("format", default="%s") == "%s"
    assert field.boolean("stretch", default=False) is False
    assert not doc.diagnostics


def test_an_absent_property_that_was_required_is_a_diagnostic() -> None:
    doc = document()
    assert field_of(doc).string("expr", required=True) is None
    assert str(doc.diagnostics) == (
        "t.kdl: report > layout > detail > field expr=: required"
    )


@pytest.mark.parametrize(
    ("source", "asked", "message"),
    [
        ("report name=5 { }", "string", "want a string, got integer"),
        ("report name=#null { }", "string", "want a string, got null"),
        ("report name=#true { }", "string", "want a string, got boolean"),
        ("report name=1.5 { }", "integer", "want an integer, got number"),
        ("report name=#true { }", "integer", "want an integer, got boolean"),
        ("report name=1 { }", "boolean", "want #true or #false, got integer"),
        ("report name=#true { }", "dimension", "want a dimension, got boolean"),
        ("report name=1 { }", "color", "want a string, got integer"),
    ],
)
def test_a_property_of_the_wrong_type_is_named_as_the_reference_names_it(
    source: str, asked: str, message: str
) -> None:
    doc = document(source)
    report = doc.only_root("report")
    assert report is not None
    assert getattr(report, asked)("name") is None
    assert str(doc.diagnostics) == f"t.kdl: report name=: {message}"


def test_a_type_annotation_is_a_hint_and_not_a_type() -> None:
    doc = document('report { font "body" size=(u8)10 }')
    report = doc.only_root("report")
    assert report is not None
    font = report.child("font")
    assert font is not None
    assert font.integer("size") == 10
    assert not doc.diagnostics


def test_a_value_the_parsers_refuse_arrives_with_its_node_attached() -> None:
    doc = document('report { layout width="1MM" }')
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    assert layout.dimension("width") is None
    assert layout.dimension("width", default=0.0) == 0.0
    assert str(doc.diagnostics).splitlines()[0] == (
        't.kdl: report > layout width=: bad dimension "1MM"'
    )


@pytest.mark.parametrize(
    ("source", "spelled"),
    [
        ("report { layout width=#inf }", "#inf"),
        ("report { layout width=#-inf }", "#-inf"),
        ("report { layout width=#nan }", "#nan"),
        ("report { layout width=1e308 }", "1e+308"),
    ],
)
def test_a_number_too_large_to_be_a_coordinate_is_refused(
    source: str, spelled: str
) -> None:
    """The route a string grammar cannot guard.

    KDL v2 writes an infinity as a keyword, so it arrives as a float
    and never passes through the dimension grammar at all; `1e308`
    arrives as an ordinary number and only overflows once the rounding
    scales it.

    """
    doc = document(source)
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    assert layout.dimension("width") is None
    assert str(doc.diagnostics) == (
        f"t.kdl: report > layout width=: bad dimension {spelled}: not finite"
    )


def test_a_bad_colour_arrives_the_same_way() -> None:
    doc = document('report { layout { style color="grey" } }')
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    style = layout.child("style")
    assert style is not None
    assert style.color("color") is None
    assert str(doc.diagnostics) == (
        't.kdl: report > layout > style color=: bad colour "grey"'
    )


def test_an_enumeration_lists_what_it_would_have_taken() -> None:
    doc = document('report { layout { detail { field align="sideways" } } }')
    field = field_of(doc)
    assert field.enum("align", ("left", "center", "right", "justified")) is None
    assert str(doc.diagnostics) == (
        "t.kdl: report > layout > detail > field align=: "
        'unknown value "sideways"; want one of: center justified left right'
    )


def test_an_enumeration_takes_a_value_that_is_in_it() -> None:
    doc = document('report { layout { detail { field align="right" } } }')
    field = field_of(doc)
    assert field.enum("align", ("left", "center", "right"), default="left") == "right"
    assert not doc.diagnostics


def test_a_property_the_node_does_not_take_is_reported() -> None:
    doc = document('report name="x" bogus="y" { }')
    report = doc.only_root("report")
    assert report is not None
    report.known_properties("name")
    assert str(doc.diagnostics) == "t.kdl: report bogus=: unknown property"


def test_a_node_the_parent_does_not_take_is_reported() -> None:
    doc = document('report { nosuchnode "x" }')
    report = doc.only_root("report")
    assert report is not None
    report.known_children("font", "layout")
    assert str(doc.diagnostics) == (
        't.kdl: report > nosuchnode "x": '
        "unexpected node here; report accepts: font layout"
    )


def test_children_come_back_in_document_order() -> None:
    doc = document()
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    detail = layout.child("detail")
    assert detail is not None
    rectangles = detail.each("rectangle")
    assert [one.dimension("left") for one in rectangles] == [0.0, 10.0]
    assert detail.each("barcode") == ()
    assert not doc.diagnostics


def test_exactly_one_child_means_exactly_one() -> None:
    doc = document("report {\n  layout { }\n  layout { }\n}")
    report = doc.only_root("report")
    assert report is not None
    assert report.child("layout") is not None
    assert str(doc.diagnostics) == (
        "t.kdl: report > layout: a report has exactly one `layout`"
    )


def test_a_missing_child_is_reported_against_the_parent() -> None:
    doc = document("report { }")
    report = doc.only_root("report")
    assert report is not None
    assert report.child("layout") is None
    assert str(doc.diagnostics) == "t.kdl: report: a report has exactly one `layout`"


def test_at_most_one_child_allows_none() -> None:
    doc = document("report { }")
    report = doc.only_root("report")
    assert report is not None
    assert report.optional_child("style") is None
    assert not doc.diagnostics


def test_at_most_one_child_reports_the_second() -> None:
    doc = document("report {\n  style { }\n  style { }\n}")
    report = doc.only_root("report")
    assert report is not None
    assert report.optional_child("style") is not None
    assert str(doc.diagnostics) == (
        "t.kdl: report > style: at most one style is allowed here"
    )


def test_a_repeated_property_is_the_last_one_written() -> None:
    """KDL's own rule, and the reference takes it silently.

    ``ckdl`` hands over a mapping, so a duplicate is not visible to us at
    all; this records that the engine agrees rather than that it checks.

    """
    doc = document("report { layout width=1 width=2 }")
    report = doc.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    assert layout.dimension("width") == 2.0


def test_a_document_that_is_not_kdl_raises_rather_than_collecting() -> None:
    with pytest.raises(TemplateError, match="parse error"):
        document('report name="x" {\n')


def test_version_one_spellings_are_not_accepted() -> None:
    """doc/template.md#parser-requirements asks for v2 specifically.

    ``true`` without its ``#`` is v1, and a reader left on "any version"
    would take it.

    """
    with pytest.raises(TemplateError, match="parse error"):
        document("report prompt=true\n")


def test_a_file_is_read_and_named_in_its_diagnostics(tmp_path: Path) -> None:
    template = tmp_path / "report.kdl"
    template.write_text("report name=5 { }", encoding="utf-8")
    doc = kdl.read(template)
    report = doc.only_root("report")
    assert report is not None
    assert report.string("name") is None
    assert (
        str(doc.diagnostics) == f"{template}: report name=: want a string, got integer"
    )


def test_a_file_that_is_not_utf8_is_refused_with_its_name(tmp_path: Path) -> None:
    template = tmp_path / "report.kdl"
    template.write_bytes(b'report name="\xff\xfe"\n')
    with pytest.raises(TemplateError, match="not UTF-8"):
        kdl.read(template)


def test_every_example_template_reads() -> None:
    """The three templates in `example/`, through the adapter.

    Nothing here asks what they mean -- that is M4 -- only that the
    document comes back with the root the format names and no complaint
    from the reader.

    """
    root = Path(__file__).resolve().parents[2]
    for name in ("sakila/sakila", "invoices/invoices", "invoices/region_sheet"):
        doc = kdl.read(root / "example" / f"{name}.kdl")
        assert doc.only_root("report") is not None, name
        assert not doc.diagnostics, name
