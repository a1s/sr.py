"""Compilation: what parses, what is refused, and what the function takes."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from sr.errors import ExpressionError
from sr.expr.compile import RUNTIME, compile_expression, evaluate, parse
from sr.expr.literals import Lexed
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
        ("{**other}", r"no \*\* in a dict literal"),
        ("'a\\ud800'", r"invalid Unicode code point U\+D800"),
        ("'A\\xffB'", r"non-ASCII hex escape \\xff \(use \\u00FF for"),
        ("'A\\377B'", r"non-ASCII octal escape \\377 \(use \\u00FF for"),
    ],
)
def test_a_construct_the_dialect_lacks_is_refused(source: str, complaint: str) -> None:
    with pytest.raises(ExpressionError, match=complaint):
        compile_expression(source)


def test_the_namespace_an_expression_runs_against_is_not_reachable() -> None:
    """`__builtins__` is Python's name in the runtime, and undefined here.

    It has to be in the dict the compiled code runs against, because
    Python puts one there otherwise.  Were it also a name an expression
    could read, the dict resolvers would hand a template `setdefault`
    on the one namespace every expression in the process shares.
    """
    with pytest.raises(ExpressionError, match="undefined: __builtins__"):
        compile_expression("__builtins__", known=set())
    with pytest.raises(ExpressionError, match="undefined: __builtins__"):
        evaluate("__builtins__.setdefault('leaked', 1)")
    assert RUNTIME["__builtins__"] == {}


def test_a_refusal_says_where_in_the_expression_it_is() -> None:
    with pytest.raises(ExpressionError) as raised:
        compile_expression("amount + 2 ** 3")
    assert raised.value.offset == 9
    assert raised.value.where() == "at offset 9"


@pytest.mark.parametrize(
    "source",
    [
        "Šķ + 2 ** 3",
        "'Šķ' + 2 ** 3",
    ],
)
def test_a_refusal_after_non_ascii_text_counts_characters(source: str) -> None:
    # `ast` counts the column in UTF-8 bytes, two for each of Š and ķ.
    offset = source.index("2 **")
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.offset == offset
    assert raised.value.where() == f"at offset {offset}"


def test_a_refusal_on_a_later_line_counts_the_lines_before() -> None:
    source = "(Šķūnis +\nā + 2 ** 3)"
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.offset == source.index("2 **") == 14


@pytest.mark.parametrize(
    "source",
    [
        "('a\u2028b' +\n2 ** 3)",
        "(1 +  # \u2028\n2 ** 3)",
        "(1 +  # \x0c\n2 ** 3)",
    ],
)
def test_a_line_ends_only_where_the_parser_ends_one(source: str) -> None:
    # str.splitlines would also break at the U+2028 and the form feed.
    # A literal's contents are blanked before the parse, so the comments
    # are what keep each character in the text the parser counts in;
    # a form feed outside a literal or a comment is refused besides.
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.offset == source.index("2 **")


def test_a_syntax_error_says_where_too() -> None:
    with pytest.raises(ExpressionError) as raised:
        compile_expression("amount +")
    assert raised.value.offset is not None


def test_a_syntax_error_after_non_ascii_text_counts_characters() -> None:
    # Unlike a node's column, SyntaxError.offset is in characters.
    with pytest.raises(ExpressionError) as raised:
        compile_expression("Šķ + )")
    assert raised.value.offset == 5


@pytest.mark.parametrize(
    ("source", "at"),
    [
        ("1 +\f 2", 3),
        ("\f1 + 2", 0),
        ("1 + 2\f", 5),
        ("(1 +\n\f 2)", 5),
        ("(1 +\r\f 2)", 5),
        ("(1 +\r\n\f 2)", 6),
        ("1 +\\\n\f 2", 5),
        ("Šķ +\f 2", 4),
        ("(1 +\f 2", 4),
        ("1 +\f\v 2", 3),
    ],
)
def test_a_form_feed_between_tokens_is_refused(source: str, at: int) -> None:
    """Python's tokenizer skips one like a space, and Starlark's does not.

    The last two do not parse either, and the form feed comes first.
    The offset counts characters, so the name before the third from
    the end puts it at 4 rather than at 6, where UTF-8 bytes would.
    """
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.message == "unexpected input character '\\f'"
    assert raised.value.offset == at


@pytest.mark.parametrize(
    "source",
    ["'a\fb'", "b'a\fb'", "r'a\fb'", "'''a\fb'''", "1 # a\fb", "1 +\t2"],
)
def test_a_form_feed_in_a_literal_or_a_comment_compiles(source: str) -> None:
    """The reference takes each of these, and the tab between tokens too."""
    compile_expression(source)


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


# ------------------------------------------------- no surrogates in a string


def test_str_of_bytes_writes_u_fffd_for_each_invalid_byte() -> None:
    assert evaluate("str(b'\\xe2\\x82A')") == "\ufffd\ufffdA"
    assert evaluate("str(b'\\xc3\\xa9')") == "\u00e9"


def test_chr_and_percent_c_give_u_fffd_for_a_surrogate() -> None:
    assert evaluate("chr(0xD800)") == "\ufffd"
    assert evaluate("'%c' % 0xDCFF") == "\ufffd"


def test_the_repr_of_bytes_escapes_each_invalid_byte() -> None:
    assert evaluate("'%s' % b'A\\xc3\\xa9\\xc3'") == 'b"A\u00e9\\xc3"'


# ---------------------------------------------------------------- literals


@pytest.mark.parametrize(
    ("source", "answer"),
    [
        ("'\\x7f'", "\x7f"),
        ("'\\177'", "\x7f"),
        ("'\\u00ff'", "\xff"),
        ("'\\U000000ff'", "\xff"),
        ("'\\\\xff'", "\\xff"),
        ("r'\\xff'", "\\xff"),
        ("b'\\xff\\377'", b"\xff\xff"),
        ("('A'  # not '\\xff'\n + 'B')", "AB"),
    ],
)
def test_a_string_literal_escapes_a_byte_up_to_0x7f(source: str, answer: Any) -> None:
    """Above 0x7F, only a bytes literal names a byte, and a raw one none."""
    assert evaluate(source) == answer


@pytest.mark.parametrize(
    ("source", "answer"),
    [
        ("'\\a\\b\\f\\n\\r\\t\\v'", "\a\b\f\n\r\t\v"),
        ("'\\'\\\"\\\\'", "'\"\\"),
        ("'\\08'", "\x008"),
        ("'A\\\nB'", "AB"),
        ("'''A\nB'''", "A\nB"),
        ("r'A\\'B'", "A\\'B"),
        ("('A'\n      + r'\\xff'\n  + '\\x41')", "A\\xffA"),
        ("'\u0160\u0137' + '\\x41'", "\u0160\u0137A"),
    ],
)
def test_a_literal_holds_what_starlark_reads_in_it(source: str, answer: str) -> None:
    assert evaluate(source) == answer


@pytest.mark.parametrize(
    ("source", "answer"),
    [
        ("('a' +\r'\\x41')", "aA"),
        ("('''a\rb''' +\n'\\x41')", "a\nbA"),
        ("'''a\r\nb'''", "a\nb"),
        ("r'''a\rb'''", "a\nb"),
        ("b'''a\rb'''", b"a\nb"),
        ("'a\\\rb'", "ab"),
        ("'a\\\r\nb'", "ab"),
    ],
)
def test_a_carriage_return_ends_a_line_as_a_newline_does(
    source: str, answer: Any
) -> None:
    """In a literal it is a newline, and either way it is one line end."""
    assert evaluate(source) == answer


def test_a_literal_after_a_name_that_is_not_ascii_is_read() -> None:
    assert evaluate("\u0160\u0137 + '\\x41'", {"\u0160\u0137": "x"}) == "xA"


@pytest.mark.parametrize(
    "source", ["b'\\u00ff'", "b'\\U000000ff'", "b'\xff'", "rb'\xff'"]
)
def test_a_bytes_literal_holds_a_character_as_utf_8(source: str) -> None:
    r"""Python refuses the character, and keeps the ``\u`` as six bytes."""
    assert evaluate(source) == b"\xc3\xbf"


@pytest.mark.parametrize(
    ("source", "complaint"),
    [
        ("'C:\\data'", r"invalid escape sequence \\d"),
        ("'\\N{DIGIT ONE}'", r"invalid escape sequence \\N"),
        ("'\\8'", r"invalid escape sequence \\8"),
        ("b'\\q'", r"invalid escape sequence \\q"),
        ("b'\\400'", r"invalid escape sequence \\400"),
        ("'\\x4'", r"truncated escape sequence \\x4$"),
        ("'\\x4g'", r"invalid escape sequence \\x4g"),
        ("b'\\ud800'", r"invalid Unicode code point U\+D800"),
        ("'\\U00110000'", r"code point out of range: \\U00110000"),
        ("u'A'", "there is no u prefix; leave it out"),
        ("R'A'", "there is no R prefix; write r"),
        ("B'A'", "there is no B prefix; write b"),
        ("br'A'", "there is no br prefix; write rb"),
        ("'A' 'B'", "two literals in a row are not joined"),
        ("b'A' b'B'", "two literals in a row are not joined"),
        ("('A'\n 'B')", "two literals in a row are not joined"),
    ],
)
def test_a_literal_starlark_has_not_got_is_refused(source: str, complaint: str) -> None:
    with pytest.raises(ExpressionError, match=complaint):
        compile_expression(source)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="t-strings are 3.14's")
def test_a_t_string_is_refused() -> None:
    with pytest.raises(ExpressionError, match="no t-strings"):
        compile_expression("t'{1}'")


@pytest.mark.parametrize(
    ("source", "offset"),
    [
        ("'\\x7f' + \"\\x80\"", 10),
        ("'\u0160\u0137' + '\\d'", 8),
        ("('A' +\n '\\d')", 9),
        ("'A' 'B'", 4),
        ("1 + u'A'", 4),
        ("(1 +\r'\\d')", 6),
    ],
)
def test_a_literal_refusal_says_where_it_is(source: str, offset: int) -> None:
    """An escape is located at its backslash, and a literal at its start."""
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.offset == offset


@pytest.mark.filterwarnings("error")
def test_the_parser_never_sees_an_escape() -> None:
    """Python warns about an escape it has not got, and nothing should.

    An error filter turns such a warning into Python's own diagnostic,
    so this runs under one.  An f-string is blanked whole, unread.
    """
    assert evaluate("b'\\u00ff'") == b"\xc3\xbf"
    with pytest.raises(ExpressionError, match="invalid escape sequence \\\\d"):
        compile_expression("'\\d'")
    for source in ("f'\\d{1}'", "f'{\"\\d\"}'", "rf'''a\n\\d'''"):
        with pytest.raises(ExpressionError, match="no f-strings"):
            compile_expression(source)


@pytest.mark.parametrize(
    ("source", "offset"),
    [("'a" + chr(0xD800) + "'", 2), ("a" + chr(0xDFFF), 1)],
)
def test_a_surrogate_in_the_text_is_refused(source: str, offset: int) -> None:
    """From Python 3.12, the tokenizer cannot so much as encode one."""
    with pytest.raises(ExpressionError, match=r"invalid Unicode code point U\+D"):
        compile_expression(source)
    with pytest.raises(ExpressionError) as raised:
        compile_expression(source)
    assert raised.value.offset == offset


def test_text_the_tokenizer_refused_is_not_read_by_pythons_rules() -> None:
    """Were the parser to take it, its literals would be Python's."""
    with pytest.raises(ExpressionError, match=r"^why$"):
        parse(Lexed("b'_'", {}, unread="why"))


def test_a_literal_the_lexer_did_not_read_is_refused() -> None:
    """Its blanked value would otherwise reach the printout."""
    with pytest.raises(ExpressionError, match="internal error"):
        parse(Lexed("b'_'", {}))


def evaluate_with(source: str, environment: dict[str, Any]) -> Any:
    """Compile against the names given, and evaluate against their values."""
    return compile_expression(source, known=set(environment)).evaluate(environment)
