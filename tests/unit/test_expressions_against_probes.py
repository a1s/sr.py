"""The expression engine, against the probes M1 built with the reference.

Eight probes in `tests/differential/probes/expressions/` isolate the
dialect decisions -- the four M1 wrote into doc/expressions.md, and the
four M3 settled while building against the oracle.  Each is a template
whose fields are expressions and whose comments say what each field
should print here and what it prints in the reference; four of them
have the reference's printout recorded beside them.

So there are two things to check, and this checks both.

* Where the register says the engines **agree**, the answer is read
  out of the recorded printout and compared to ours.  Those assertions
  are the oracle's, not a table someone wrote by hand.
* Where it says they **diverge**, the answer is the decision -- and
  the test also asserts that it differs from what the reference recorded,
  so that a probe which stopped diverging is a failure here as well as in
  the differential suite.

No engine runs: the expressions are pulled out of the template
with the KDL adapter and evaluated directly.  What is being checked
is the dialect, and the dialect is all this milestone has.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr import kdl
from sr.errors import ExpressionError
from sr.expr.calc import make
from sr.expr.compile import compile_expression, evaluate

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "tests" / "differential" / "probes" / "expressions"


def expressions(name: str) -> tuple[str, ...]:
    """Return the `expr` of every field in a probe's detail band, in order."""
    document = kdl.read(PROBES / f"{name}.kdl")
    report = document.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    detail = layout.child("detail")
    assert detail is not None
    written = tuple(str(field.raw("expr")) for field in detail.each("field"))
    assert not document.diagnostics
    return written


def reference(name: str) -> list[str]:
    """Return the one line each mark of a probe's recorded answer holds."""
    recorded = (PROBES / f"{name}.answer.jsonl").read_text(encoding="utf-8")
    page: dict[str, Any] = json.loads(recorded.splitlines()[1])
    return [mark["lines"][0] for mark in page["marks"]]


# ---------------------------------------------------- strings-are-codepoints

# What each field of `strings.kdl` prints here.
# Every one of the five differs from the reference, which is
# the point of the probe: it counts bytes and this counts codepoints.
CODEPOINT_ANSWERS = ["6", '"Š"', '"Šķū"', '"ū"', '"Šķ"']


def test_the_strings_probe_has_the_fields_this_test_expects() -> None:
    assert len(expressions("strings")) == len(CODEPOINT_ANSWERS)


@pytest.mark.parametrize("index", range(len(CODEPOINT_ANSWERS)))
def test_a_string_is_a_sequence_of_codepoints(index: int) -> None:
    assert evaluate(expressions("strings")[index]) == CODEPOINT_ANSWERS[index]


@pytest.mark.parametrize("index", range(len(CODEPOINT_ANSWERS)))
def test_every_field_of_the_strings_probe_still_diverges(index: int) -> None:
    """A probe that stopped differing has done its work and comes out."""
    assert CODEPOINT_ANSWERS[index] != reference("strings")[index]


# ----------------------------------------------------- string-elems-dropped


def test_codepoints_agrees_with_the_reference_where_the_register_agrees() -> None:
    """The first two fields of `string-methods.kdl` are the same in both."""
    written = expressions("string-methods")
    recorded = reference("string-methods")
    assert evaluate(written[0]) == recorded[0]
    assert evaluate(written[1]) == recorded[1]


@pytest.mark.parametrize("index", (2, 3))
def test_the_byte_pair_is_gone_from_the_string_type(index: int) -> None:
    """`elems` and `elem_ords` answer in the reference and are absent here."""
    with pytest.raises(ExpressionError, match="codepoint"):
        evaluate(expressions("string-methods")[index])


# ------------------------------------------------------------ round-builtin

# The reference refuses the template rather than printing something,
# so these come from the probe's comments rather than from a printout.
ROUND_ANSWERS = ["2", "3", "-3", "3.0"]


@pytest.mark.parametrize("index", range(len(ROUND_ANSWERS)))
def test_round_is_predeclared_and_goes_away_from_zero(index: int) -> None:
    assert evaluate(expressions("round")[index]) == ROUND_ANSWERS[index]


def test_the_round_probe_has_no_recorded_answer() -> None:
    """There is nothing to record: the reference has no such builtin."""
    assert not (PROBES / "round.answer.jsonl").exists()


# ------------------------------------------------------- time-before-year-one


def test_the_fields_a_time_is_given_agree_with_the_reference() -> None:
    """The first three rows of `time-fields.kdl`, read out of its printout."""
    written = expressions("time-fields")
    recorded = reference("time-fields")
    for index in range(3):
        assert evaluate(written[index]) == recorded[index]


def test_a_time_before_year_one_is_refused_where_the_reference_builds_one() -> None:
    written = expressions("time-fields")
    assert reference("time-fields")[3].startswith("-0001-")
    with pytest.raises(ExpressionError):
        evaluate(written[3])


# ------------------------------------------------------- abs-takes-a-decimal

# The reference refuses the whole template over the first row,
# so there is no printout to read the other two out of.
DECIMAL_ABS_ANSWERS = ["1.5", "1", "1.5"]


@pytest.mark.parametrize("index", range(len(DECIMAL_ABS_ANSWERS)))
def test_abs_takes_a_decimal_here(index: int) -> None:
    assert evaluate(expressions("decimal-abs")[index]) == DECIMAL_ABS_ANSWERS[index]


def test_the_decimal_abs_probe_has_no_recorded_answer() -> None:
    """The reference will not build it, so there is nothing to record."""
    assert not (PROBES / "decimal-abs.answer.jsonl").exists()


# -------------------------------------------------- decimal-exponent-is-exact


def test_a_decimal_keeps_its_digits_where_the_reference_keeps_them() -> None:
    """The first four rows of `decimal-precision.kdl`, from its printout."""
    written = expressions("decimal-precision")
    recorded = reference("decimal-precision")
    for index in range(4):
        assert evaluate(written[index]) == recorded[index]


def test_a_precision_deeper_than_a_float_reaches_still_diverges() -> None:
    written = expressions("decimal-precision")
    recorded = reference("decimal-precision")[4]
    assert evaluate(written[4]) == "1.234567890123456789012345678901e+29"
    assert recorded == "1.234567890123456778777195970560e+29"


# ------------------------------------------------------ null-folds-are-skipped

# The reference refuses the template over the first variable, so these are
# the rule doc/expressions.md#calc now states rather than a printout.
NULL_FOLD_ANSWERS = [
    "total 10",
    "mean 10.0",
    "smallest 10",
    "counted 2",
    "every [None, 10]",
]


def test_a_null_folds_into_nothing_where_the_reference_refuses_the_build() -> None:
    """The probe's data is one null row and one row of ten."""
    document = kdl.read(PROBES / "null-folds.kdl")
    report = document.only_root("report")
    assert report is not None
    layout = report.child("layout")
    assert layout is not None
    summary = layout.child("summary")
    assert summary is not None
    fields = summary.each("field")
    assert not document.diagnostics

    accumulators = {
        name: make(calc)
        for name, calc in (
            ("total", "sum"),
            ("mean", "avg"),
            ("smallest", "min"),
            ("counted", "count"),
            ("every", "list"),
        )
    }
    for amount in (None, 10):
        for accumulator in accumulators.values():
            accumulator.fold(amount)
    environment = {name: one.value for name, one in accumulators.items()}
    for field, answer in zip(fields, NULL_FOLD_ANSWERS, strict=True):
        source = str(field.raw("expr"))
        assert evaluate_with(source, environment) == answer


def test_the_null_folds_probe_has_no_recorded_answer() -> None:
    """The reference will not build it: "cannot sum NoneType and int"."""
    assert not (PROBES / "null-folds.answer.jsonl").exists()


# --------------------------------------------------- decimal-int-comparison

# The first four differ from the reference, which refuses the comparison;
# the last two are the float half of the rule, where the two agree.
DECIMAL_ANSWERS = ["True", "True", "False", "d", "False", "True"]


@pytest.mark.parametrize("index", range(len(DECIMAL_ANSWERS)))
def test_a_decimal_compares_and_hashes_with_an_int(index: int) -> None:
    assert evaluate(expressions("decimal-int")[index]) == DECIMAL_ANSWERS[index]


def evaluate_with(source: str, environment: dict[str, Any]) -> Any:
    """Compile against the names given, and evaluate against their values."""
    return compile_expression(source, known=set(environment)).evaluate(environment)
