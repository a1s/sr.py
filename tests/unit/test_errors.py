"""Diagnostics: what one says, and how a run collects them.

The wording is held to the reference engine's, which is what
a person comparing two runs sees first.  The samples quoted here
were read off the reference binary rather than guessed.

"""

from __future__ import annotations

import pytest

from sr.errors import (
    WARNING_KINDS,
    BadValue,
    BuildWarning,
    Diagnostic,
    Diagnostics,
    Location,
    NodePath,
    SrError,
    Step,
    TemplateError,
)


def path(*steps: str | tuple[str, str]) -> NodePath:
    built = NodePath()
    for step in steps:
        built = built.child(*((step,) if isinstance(step, str) else step))
    return built


def test_a_step_names_a_node_by_itself() -> None:
    assert str(Step("layout")) == "layout"


def test_a_step_shows_the_identity_argument_where_there_is_one() -> None:
    assert str(Step("font", "body")) == 'font "body"'


def test_a_path_joins_its_steps_the_way_a_diagnostic_spells_them() -> None:
    assert str(path("report", ("font", "body"))) == 'report > font "body"'


def test_an_empty_path_is_false() -> None:
    assert not NodePath()
    assert path("report")


def test_a_path_does_not_change_the_one_it_grew_from() -> None:
    root = path("report")
    root.child("layout")
    assert str(root) == "report"


def test_a_location_reads_as_the_reference_writes_one() -> None:
    where = Location(
        file="path.kdl", path=path("report", ("font", "body")), prop="size", line=2
    )
    assert str(where) == 'path.kdl:2: report > font "body" size='


def test_a_location_without_a_line_leaves_no_trace_of_one() -> None:
    where = Location(file="path.kdl", path=path("report", "layout"), prop="pagesize")
    assert str(where) == "path.kdl: report > layout pagesize="


def test_a_location_names_the_record_where_there_is_one() -> None:
    where = Location(file="r.kdl", path=path("report", "layout", "detail"), record=7)
    assert str(where) == "r.kdl: report > layout > detail, record 7"


def test_a_location_with_nothing_in_it_says_nothing() -> None:
    assert str(Location()) == ""


def test_a_diagnostic_puts_its_location_first() -> None:
    where = Location(file="t.kdl", path=path("report"), prop="name")
    assert str(Diagnostic("unknown property", where)) == (
        "t.kdl: report name=: unknown property"
    )


def test_a_diagnostic_with_no_location_is_just_the_message() -> None:
    assert str(Diagnostic('bad colour "grey"')) == 'bad colour "grey"'


def test_a_location_can_be_filled_in_after_the_fact() -> None:
    located = Diagnostic('bad dimension "1MM"').at(
        file="t.kdl", path=path("report", "layout"), prop="width"
    )
    assert str(located) == 't.kdl: report > layout width=: bad dimension "1MM"'


def test_filling_in_touches_only_the_parts_given() -> None:
    first = Diagnostic("required", Location(file="one.kdl", prop="size"))
    assert first.at(line=4).location.file == "one.kdl"
    assert first.at(line=4).location.prop == "size"
    assert first.at(file="two.kdl").location.file == "two.kdl"


def test_a_bad_value_carries_the_message_and_nothing_else() -> None:
    refused = BadValue('bad dimension "12px"')
    assert isinstance(refused, SrError)
    assert str(refused) == 'bad dimension "12px"'


def test_a_collector_names_the_file_for_every_diagnostic() -> None:
    found = Diagnostics(file="t.kdl")
    found.error("unknown property", path=path("report"), prop="bogus")
    assert str(found) == "t.kdl: report bogus=: unknown property"


def test_a_collector_names_the_file_on_a_diagnostic_built_elsewhere() -> None:
    found = Diagnostics(file="t.kdl")
    found.add(Diagnostic("empty colour", Location(path=path("report"), prop="color")))
    assert str(found) == "t.kdl: report color=: empty colour"


def test_a_diagnostic_that_already_names_a_file_keeps_it() -> None:
    found = Diagnostics(file="t.kdl")
    found.add(Diagnostic("not UTF-8", Location(file="other.kdl")))
    assert str(found) == "other.kdl: not UTF-8"


def test_a_collector_keeps_the_order_the_checks_ran_in() -> None:
    found = Diagnostics(file="t.kdl")
    found.error("third", line=8)
    found.error("second", line=6)
    found.error("first", line=3)
    assert [one.message for one in found] == ["third", "second", "first"]
    assert len(found) == 3


def test_an_empty_collector_is_false_and_raises_nothing() -> None:
    found = Diagnostics(file="t.kdl")
    assert not found
    found.raise_if_any()


def test_a_collector_with_anything_in_it_raises_all_of_it() -> None:
    found = Diagnostics(file="t.kdl")
    found.error("required", path=path("report", ("font", "body")), prop="size")
    found.error("must be positive", path=path("report", ("font", "body")), prop="size")
    with pytest.raises(TemplateError) as refused:
        found.raise_if_any()
    assert len(refused.value.diagnostics) == 2
    assert str(refused.value).splitlines() == [
        't.kdl: report > font "body" size=: required',
        't.kdl: report > font "body" size=: must be positive',
    ]


def test_a_warning_takes_only_the_kinds_the_printout_defines() -> None:
    for kind in WARNING_KINDS:
        assert BuildWarning(kind, "something").kind == kind
    with pytest.raises(ValueError, match="unknown warning kind"):
        BuildWarning("typo", "something")


def test_a_warning_says_where_it_happened_where_it_knows() -> None:
    warning = BuildWarning(
        "glyph",
        "no glyph for U+0161",
        node="report > layout > detail > field",
        record=3,
    )
    assert str(warning) == (
        "glyph: no glyph for U+0161 (report > layout > detail > field, record 3)"
    )
    assert str(BuildWarning("font", "typeface substituted")) == (
        "font: typeface substituted"
    )
