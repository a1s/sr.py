"""Compilation: what parses, what is refused, and what the function takes."""

from __future__ import annotations

from typing import Any

import pytest

from sr.errors import ExpressionError
from sr.expr.compile import compile_expression, evaluate
from sr.expr.values import Decimal, Namespace, Record, Time

# Every predefined name of doc/expressions.md#predefined-variables,
# with a value of the type the table gives it.  Two group-scoped names
# stand for the family: a report with a `customer` group has both of these.
PREDEFINED: dict[str, Any] = {
    "THIS": Record({"amount": Decimal("19.99"), "region": "North"}),
    "ITEM_NUMBER": 3,
    "DATA_COUNT": 16,
    "REPORT_COUNT": 2,
    "PAGE_COUNT": 2,
    "COLUMN_COUNT": 1,
    "PAGE_NUMBER": 1,
    "COLUMN_NUMBER": 1,
    "customer_COUNT": 4,
    "customer_PAGE_NUMBER": 1,
    "VERTICAL_POSITION": 120.5,
    "VERTICAL_SPACE": 600.25,
    "BUILD_TIME": Time(0),
    "FINAL": Namespace("FINAL", {"PAGE_NUMBER": 7, "total_amount": Decimal("40")}),
}


def test_the_parameters_are_the_free_names_in_the_order_they_appear() -> None:
    """The example doc/expressions.md#compilation gives, checked."""
    compiled = compile_expression("amount * qty + math.floor(rate)")
    assert compiled.names == ("amount", "qty", "rate")


def test_a_global_is_not_a_parameter() -> None:
    assert compile_expression("len(sorted(set(tags)))").names == ("tags",)


def test_a_name_appears_once_however_often_it_is_used() -> None:
    assert compile_expression("amount + amount").names == ("amount",)


def test_a_comprehension_variable_is_local_to_its_comprehension() -> None:
    assert compile_expression("[x * factor for x in items]").names == (
        "items",
        "factor",
    )
    assert compile_expression("{k: v for k, v in pairs}").names == ("pairs",)


def test_a_compiled_expression_is_called_with_what_it_needs() -> None:
    compiled = compile_expression("unit * qty")
    assert compiled(Decimal("2.50"), 4) == Decimal("10.00")
    assert compiled.evaluate({"unit": Decimal("2.50"), "qty": 4, "unused": 1}) == (
        Decimal("10.00")
    )


def test_a_name_with_no_value_is_named_in_the_error() -> None:
    with pytest.raises(ExpressionError, match="undefined: qty"):
        compile_expression("unit * qty").evaluate({"unit": 1})


def test_a_name_that_is_not_in_scope_is_refused_at_compile_time() -> None:
    """Starlark resolves names before evaluation, and so does this."""
    with pytest.raises(ExpressionError, match="undefined: nope"):
        compile_expression("amount + nope", known={"amount"})


def test_the_globals_need_no_declaring() -> None:
    assert compile_expression("len(THIS)", known={"THIS"}).names == ("THIS",)


# ----------------------------------------------------------- the refusals


@pytest.mark.parametrize(
    ("source", "complaint"),
    [
        ("2 ** 3", "no \\*\\* operator"),
        ("lambda x: x", "no lambdas"),
        ("f'{x}'", "no f-strings"),
        ("(x := 1)", "no := operator"),
        ("1 < 2 < 3", "no chained comparisons"),
        ("{1, 2}", "no set literal"),
        ("fn(*args)", "may not be starred"),
        ("fn(**kwargs)", "may not be starred"),
        ("x is None", "no `is`"),
        ("x is not None", "no `is not`"),
        ("a @ b", "no @ operator"),
        ("{x for x in y}", "no set comprehensions"),
        ("(x for x in y)", "no generator expressions"),
        ("1j", "no complex numbers"),
        ("_sr_getattr(1, 'a')", "may not begin"),
    ],
)
def test_a_construct_the_dialect_lacks_is_refused(source: str, complaint: str) -> None:
    with pytest.raises(ExpressionError, match=complaint):
        compile_expression(source)


def test_a_refusal_says_where_in_the_expression_it_is() -> None:
    with pytest.raises(ExpressionError) as raised:
        compile_expression("amount + 2 ** 3")
    assert raised.value.offset == 9
    assert raised.value.where() == "at offset 9"


def test_a_syntax_error_says_where_too() -> None:
    with pytest.raises(ExpressionError) as raised:
        compile_expression("amount +")
    assert raised.value.offset is not None


# ------------------------------------------------------------ the sandbox


@pytest.mark.parametrize(
    "source",
    [
        "open('/etc/passwd')",
        "__import__('os')",
        "eval('1')",
        "globals()",
        "'a'.__class__",
        "().__class__.__bases__",
        "[].append.__globals__",
    ],
)
def test_nothing_reaches_outside_the_language(source: str) -> None:
    with pytest.raises(ExpressionError):
        evaluate(source)


# ------------------------------------------------------- predefined names


@pytest.mark.parametrize("name", sorted(PREDEFINED))
def test_every_predefined_name_is_reachable(name: str) -> None:
    compiled = compile_expression(name, known=set(PREDEFINED))
    assert compiled.evaluate(PREDEFINED) is PREDEFINED[name]


def test_a_record_field_is_reachable_bare_and_through_this() -> None:
    environment = dict(PREDEFINED, amount=Decimal("19.99"))
    assert evaluate_with("amount == THIS.amount", environment) is True
    assert evaluate_with('THIS["region"] == "North"', environment) is True


def test_final_reads_an_end_of_scope_value() -> None:
    text = "'Page %d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)"
    assert evaluate_with(text, PREDEFINED) == "Page 1 of 7"


def test_an_expression_says_whether_it_uses_final() -> None:
    """What validation needs, since `FINAL` and `evaltime` require each other."""
    assert compile_expression("FINAL.PAGE_NUMBER").uses_final
    assert not compile_expression("PAGE_NUMBER").uses_final


def test_the_position_names_are_floats_in_points() -> None:
    assert evaluate_with("VERTICAL_SPACE - VERTICAL_POSITION", PREDEFINED) == 479.75


# --------------------------------------------------------------- semantics


@pytest.mark.parametrize(
    ("source", "answer"),
    [
        ("1 / 3", 1 / 3),
        ("7 // 2", 3),
        ("-7 // 2", -4),
        ("5 % -3", -1),
        ("'a' * 3", "aaa"),
        ("[1] + [2]", [1, 2]),
        ("'b' in 'abc'", True),
        ("1 if False else 2", 2),
        ("not None", True),
        ("None or 0", 0),
        ("0 or 'fallback'", "fallback"),
        ("123456789012345678901234567890 + 1", 123456789012345678901234567891),
        ("[x for x in range(4) if x % 2 == 0]", [0, 2]),
        ("sorted(['b', 'a'])", ["a", "b"]),
        ("'%s-%s' % ('a', 'b')", "a-b"),
    ],
)
def test_the_dialect_computes_what_it_should(source: str, answer: object) -> None:
    assert evaluate(source) == answer


def test_or_is_the_defaulting_operator_the_specification_recommends() -> None:
    """`sum` of nothing is None, and this is the documented spelling."""
    assert evaluate_with("total_amount or 0", {"total_amount": None}) == 0


def test_a_failure_at_evaluation_carries_its_message() -> None:
    with pytest.raises(ExpressionError, match="no rows"):
        evaluate("fail('no rows')")


def test_a_python_error_becomes_an_expression_error() -> None:
    with pytest.raises(ExpressionError, match="ZeroDivisionError"):
        evaluate("1 // 0")


def evaluate_with(source: str, environment: dict[str, Any]) -> Any:
    """Compile against the names given, and evaluate against their values."""
    return compile_expression(source, known=set(environment)).evaluate(environment)
