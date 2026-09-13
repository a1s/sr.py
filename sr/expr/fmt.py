"""The two percent formatters, which are not the same formatter.

doc/expressions.md#formatting keeps numeric presentation out
of the expression language and puts it in two places instead:

* **The engine's**, reached as the ``format=`` property and as the
  ``format`` builtin.  Flags, width and precision, the conversions
  ``%s %q %d %i %o %x %X %b %e %E %f %g %G %c %%``, and decimals
  formatted exactly rather than through a float.  A tuple result
  spreads across the conversions positionally and the count must match.

* **Starlark's own ``%``**, which is the smaller one: the same
  conversions minus ``%q`` and ``%b``, plus ``%r``, and no flags,
  width or precision at all.  It exists because templates use it,
  and the specification's advice about it -- "Prefer format" --
  is what this module's asymmetry is for.

Neither is Python's ``%``.  Python spells a float, a tuple and
a nested string differently, rounds a decimal to even, and would
take flags where Starlark's takes none, so both are written out here
over the language's own :func:`~sr.expr.values.starlark_str`.

"""

from __future__ import annotations

import decimal
import math
import re
from typing import Any, Final

from sr.errors import ExpressionError
from sr.expr.values import (
    GO_EXPONENT_LIMIT,
    Decimal,
    FrozenDict,
    Record,
    go_general,
    go_shortest,
    quantize,
    quote,
    starlark_repr,
    starlark_str,
    type_name,
)

__all__ = ["apply_format", "format_value", "interpolate"]

# The engine's formatter: flags, then width, then precision, then one
# letter.  The letter is any character rather than a known verb, so that
# `%y` is an unknown conversion rather than a literal; `%` itself is one
# of the verbs, which is what makes `%%` a percent sign.
CONVERSION: Final = re.compile(r"%([-+#0 ]*)([0-9]*)(?:\.([0-9]*))?(.)")

# Which conversions take an integer, which a real number, and which the
# whole value.  The three lists are the specification's, in its order.
INTEGER_VERBS: Final = "dioxXbc"
REAL_VERBS: Final = "eEfgG"
ENGINE_VERBS: Final = "sqdioxXbeEfgGc%"

# Starlark's own, which has `%r` where the engine has `%q`, and no `%b`.
STARLARK_VERBS: Final = "srdioxXeEfgGc%"

# The base and prefix each integer conversion writes in.
BASES: Final = {
    "d": (10, ""),
    "i": (10, ""),
    "o": (8, "0"),
    "x": (16, "0x"),
    "X": (16, "0X"),
    "b": (2, "0b"),
}


class Spec:
    """One conversion out of a format string.

    Attributes:
        flags: The flag characters, in the order they were written.
        width: The minimum field width, or ``None``.
        precision: The precision, or ``None`` where none was given.
        verb: The conversion letter.

    """

    __slots__ = ("flags", "precision", "verb", "width")

    def __init__(
        self, flags: str, width: int | None, precision: int | None, verb: str
    ) -> None:
        """Hold the parts of one conversion.

        Args:
            flags: The flag characters.
            width: The minimum field width.
            precision: The precision.
            verb: The conversion letter.

        """
        self.flags = flags
        self.width = width
        self.precision = precision
        self.verb = verb

    @property
    def left(self) -> bool:
        """Report whether the value is left-aligned in its field."""
        return "-" in self.flags

    @property
    def zero(self) -> bool:
        """Report whether the field is padded with zeros rather than spaces."""
        return "0" in self.flags and not self.left

    @property
    def alternate(self) -> bool:
        """Report whether a base prefix is written."""
        return "#" in self.flags

    def sign_for(self, negative: bool) -> str:
        """Return the sign a number is written with.

        Args:
            negative: Whether the number is below zero.

        """
        if negative:
            return "-"
        if "+" in self.flags:
            return "+"
        return " " if " " in self.flags else ""

    def pad(self, sign: str, body: str) -> str:
        """Return a formatted value laid into its field.

        Zero padding goes between the sign and the digits and space
        padding outside both, which is what makes ``%+06.2f`` read
        as one number rather than as a sign adrift from its value.

        Args:
            sign: The sign and any base prefix.
            body: The digits.

        """
        text = sign + body
        if self.width is None or len(text) >= self.width:
            return text
        room = self.width - len(text)
        if self.left:
            return text + " " * room
        if self.zero:
            return sign + "0" * room + body
        return " " * room + text


def parse_spec(match: re.Match[str]) -> Spec:
    """Return the conversion a match of :data:`CONVERSION` describes.

    Args:
        match: The match, whose groups are flags, width, precision, verb.

    """
    flags, width, precision, verb = match.groups()
    return Spec(
        flags,
        int(width) if width else None,
        # A point with no digits after it is a precision of zero, which is
        # what `%.f` means and what `int("")` would have raised over.
        None if precision is None else int(precision or 0),
        verb,
    )


def as_integer(value: Any, verb: str) -> int:
    """Return a value as the integer an integer conversion writes.

    An int is itself; a **decimal rounds**, halves away from zero, and
    a **float truncates** toward zero.  The asymmetry is the reference's,
    measured rather than reasoned about: ``%d`` on ``decimal("1.9")``
    is 2 and on ``1.9`` is 1.  It is not the same question as ``int(d)``,
    which truncates in both engines, because a this is a rendering and
    ``int`` is a conversion.

    Args:
        value: What the expression produced.
        verb: The conversion, for the diagnostic.

    Raises:
        ExpressionError: The value is not a number.

    """
    if isinstance(value, bool):
        raise ExpressionError(f"%{verb} wants a number, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(quantize(value, 0))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExpressionError(f"%{verb} wants a finite number, got {value}")
        return math.trunc(value)
    raise ExpressionError(f"%{verb} wants a number, got {type_name(value)}")


def integer_text(value: Any, spec: Spec) -> str:
    """Return an integer conversion's output.

    Args:
        value: What the expression produced.
        spec: The conversion.

    """
    if spec.verb == "c":
        text = value if isinstance(value, str) else chr(as_integer(value, "c"))
        return spec.pad("", text)
    number = as_integer(value, spec.verb)
    base, prefix = BASES[spec.verb]
    digits = format(abs(number), {10: "d", 8: "o", 16: "x", 2: "b"}[base])
    if spec.verb == "X":
        digits = digits.upper()
    if spec.precision is not None:
        digits = digits.rjust(spec.precision, "0")
    head = spec.sign_for(number < 0)
    if spec.alternate and prefix:
        head += prefix if spec.verb != "o" else "0"
    return spec.pad(head, digits)


def real_text(value: Any, spec: Spec) -> str:
    """Return a real conversion's output, exactly where the value is exact.

    A decimal never becomes a float on the way: it is rounded in decimal
    arithmetic, halves away from zero like every other rounding here,
    so ``%.2f`` on ``19.995`` is ``20.00`` rather than the ``19.99``
    that binary64 gives for a value it cannot hold.

    Args:
        value: What the expression produced.
        spec: The conversion.

    Raises:
        ExpressionError: The value is not a real number.

    """
    if isinstance(value, bool):
        raise ExpressionError(f"%{spec.verb} wants a number, got bool")
    if isinstance(value, Decimal):
        body, negative = decimal_body(value, spec)
    elif isinstance(value, int | float):
        body, negative = float_body(float(value), spec)
    else:
        raise ExpressionError(f"%{spec.verb} wants a number, got {type_name(value)}")
    return spec.pad(spec.sign_for(negative), body)


def decimal_parts(number: decimal.Decimal) -> tuple[str, int]:
    """Return a decimal's significant digits and where its point falls.

    The value is ``0.<digits>`` times ten to the point, with no leading or
    trailing zeros; zero has no digits at all.  This is the exact-decimal
    counterpart of what ``repr`` gives for a float, and it is what keeps
    ``%e`` and ``%g`` off binary64 on the way to their digits.

    Args:
        number: A finite decimal, without a sign.

    """
    _, digits, exponent = number.as_tuple()
    text = "".join(str(digit) for digit in digits)
    point = len(text) + int(exponent)
    stripped = text.lstrip("0")
    point -= len(text) - len(stripped)
    stripped = stripped.rstrip("0")
    return (stripped, point) if stripped else ("", 0)


def significant(number: decimal.Decimal, wanted: int) -> tuple[str, int]:
    """Return a decimal rounded to a number of significant digits.

    Rounded half away from zero, like every other rounding here, and in
    decimal arithmetic, so a value with more digits than binary64 holds
    keeps the ones it was asked for.

    Args:
        number: A finite decimal, without a sign.
        wanted: How many significant digits to keep.

    """
    digits, point = decimal_parts(number)
    if not digits or len(digits) <= wanted:
        return digits, point
    return decimal_parts(quantize(Decimal(number), wanted - point).value)


def exponent_form(digits: str, point: int, places: int) -> str:
    """Return significant digits written the way ``%e`` writes them.

    Args:
        digits: The significant digits, already rounded, possibly none.
        point: Where the point falls.
        places: How many digits after the point.

    """
    if not digits:
        mantissa = "0" + ("." + "0" * places if places else "")
        return f"{mantissa}e+00"
    body = digits.ljust(places + 1, "0")[: places + 1]
    mantissa = body[0] + ("." + body[1:] if places else "")
    exponent = point - 1
    return f"{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent):02d}"


def decimal_body(value: Decimal, spec: Spec) -> tuple[str, bool]:
    """Return an exact decimal's digits, without a sign.

    Every form is computed in decimal arithmetic rather than through a
    float, so the digits are the value's own.  The shape is Go's, which
    Python's ``Decimal.__format__`` does not give: it writes a one-digit
    exponent where Go writes two, and it knows nothing of the turnover
    at 1e6 that ``%g`` follows.

    Args:
        value: The number to write.
        spec: The conversion.

    """
    negative = value.value < 0
    magnitude = abs(value).value
    if spec.verb == "f":
        size = 6 if spec.precision is None else spec.precision
        return format(quantize(Decimal(magnitude), size).value, "f"), negative
    if spec.verb in "eE":
        places = 6 if spec.precision is None else spec.precision
        digits, point = significant(magnitude, places + 1)
        return exponent_case(exponent_form(digits, point, places), spec.verb), negative
    if spec.precision is None:
        digits, point = decimal_parts(magnitude)
        threshold = GO_EXPONENT_LIMIT
    else:
        threshold = max(spec.precision, 1)
        digits, point = significant(magnitude, threshold)
        if threshold > len(digits) and len(digits) >= point:
            threshold = len(digits)
    if not digits:
        return "0", negative
    return exponent_case(go_general(digits, point, threshold), spec.verb), negative


def float_body(value: float, spec: Spec) -> tuple[str, bool]:
    """Return a float's digits, without a sign.

    Args:
        value: The number to write.
        spec: The conversion.

    """
    negative = math.copysign(1.0, value) < 0
    magnitude = abs(value)
    if math.isnan(magnitude):
        return "nan", False
    if math.isinf(magnitude):
        return "inf", negative
    if spec.verb in "gG" and spec.precision is None:
        return exponent_case(go_shortest(magnitude), spec.verb), negative
    size = 6 if spec.precision is None else spec.precision
    text = format(magnitude, f".{size}{spec.verb.lower()}")
    return exponent_case(text, spec.verb), negative


def exponent_case(text: str, verb: str) -> str:
    """Return a formatted real with its exponent letter in the right case.

    Args:
        text: The formatted number.
        verb: The conversion, whose case the exponent follows.

    """
    return text.upper() if verb in "EG" else text.lower()


def convert(value: Any, spec: Spec) -> str:
    """Return one value formatted by one conversion.

    Args:
        value: What the expression produced.
        spec: The conversion.

    Raises:
        ExpressionError: The conversion does not take this value.

    """
    if spec.verb in "sqr":
        if spec.verb == "s":
            text = starlark_str(value)
        elif spec.verb == "r":
            text = starlark_repr(value)
        else:
            text = quote(starlark_str(value))
        if spec.precision is not None:
            text = text[: spec.precision]
        return spec.pad("", text)
    if spec.verb in INTEGER_VERBS:
        return integer_text(value, spec)
    if spec.verb in REAL_VERBS:
        return real_text(value, spec)
    raise ExpressionError(f"unknown conversion %{spec.verb}")


def check_tail(spec: str, tail: str) -> None:
    """Refuse a ``%`` that ends a format string with nothing to convert.

    Every other ``%`` is matched as a conversion, right or wrong,
    because the pattern takes whichever character follows it.  One at
    the very end has no character following it and matches nothing at all,
    so this is where a truncated conversion is caught -- in both formatters,
    and after both of them have written everything they could.

    Args:
        spec: The whole format string, for the diagnostic.
        tail: What is left of it after the last conversion.

    Raises:
        ExpressionError: The format ends in a ``%`` on its own.

    """
    if tail.endswith("%"):
        raise ExpressionError(f"truncated conversion at the end of format {spec!r}")


def format_value(spec: str, *args: Any) -> str:
    """Return a format string with its conversions filled in.

    The engine's formatter: the ``format`` builtin, and what ``format=``
    applies to an element's value.

    Args:
        spec: The format string.
        *args: The values to convert, one per conversion.

    Raises:
        ExpressionError: A conversion is unknown, or the number of values
            is not the number of conversions.

    """
    written: list[str] = []
    position = 0
    taken = 0
    for match in CONVERSION.finditer(spec):
        written.append(spec[position : match.start()])
        position = match.end()
        conversion = parse_spec(match)
        if conversion.verb == "%":
            written.append("%")
            continue
        if conversion.verb not in ENGINE_VERBS:
            raise ExpressionError(
                f"unknown conversion %{conversion.verb} in format {spec!r}"
            )
        if taken >= len(args):
            raise ExpressionError(
                f"format {spec!r} wants more values than the {len(args)} given"
            )
        written.append(convert(args[taken], conversion))
        taken += 1
    check_tail(spec, spec[position:])
    written.append(spec[position:])
    if taken != len(args):
        raise ExpressionError(
            f"format {spec!r} takes {taken} values, {len(args)} given"
        )
    return "".join(written)


def apply_format(spec: str, value: Any) -> str:
    """Return an element's value formatted by its ``format=`` property.

    A tuple spreads across the conversions positionally, which is
    what ``format="Total for %s, %s: %.2f"`` over a three-element
    tuple asks for; anything else is one value.

    Args:
        spec: The format string.
        value: What the element's expression produced.

    """
    if isinstance(value, tuple):
        return format_value(spec, *value)
    return format_value(spec, value)


# Starlark's `%` takes a conversion and nothing else -- optionally a
# parenthesised key, which is how it reads a dict.
SIMPLE: Final = re.compile(r"%(?:\(([^)]*)\))?(.)")


def interpolate(spec: str, operand: Any) -> str:
    """Return Starlark's own ``%`` applied to one operand.

    A tuple spreads and the count must match; a dict is read by the keys
    a format names; anything else is a single value.  There are no flags,
    no width and no precision, which is why the engine has a second
    formatter at all.

    Args:
        spec: The format string, on the left of the operator.
        operand: What is on the right.

    Raises:
        ExpressionError: A conversion is unknown, a key is missing,
            or the number of values is not the number of conversions.

    """
    mapping = operand if isinstance(operand, dict | FrozenDict | Record) else None
    values = operand if isinstance(operand, tuple) else (operand,)
    written: list[str] = []
    position = 0
    taken = 0
    for match in SIMPLE.finditer(spec):
        written.append(spec[position : match.start()])
        position = match.end()
        key, verb = match.groups()
        if verb == "%" and key is None:
            written.append("%")
            continue
        if verb not in STARLARK_VERBS:
            raise ExpressionError(f"unknown conversion %{verb} in format {spec!r}")
        if key is not None:
            if mapping is None:
                raise ExpressionError(
                    f"format {spec!r} names a key, so it wants a dict, "
                    f"got {type_name(operand)}"
                )
            if key not in mapping:
                raise ExpressionError(f"format {spec!r} wants a key {key!r}")
            written.append(convert(mapping[key], Spec("", None, None, verb)))
            continue
        if taken >= len(values):
            raise ExpressionError(
                f"format {spec!r} wants more values than the {len(values)} given"
            )
        written.append(convert(values[taken], Spec("", None, None, verb)))
        taken += 1
    check_tail(spec, spec[position:])
    written.append(spec[position:])
    if mapping is None and taken != len(values):
        raise ExpressionError(
            f"format {spec!r} takes {taken} values, {len(values)} given"
        )
    return "".join(written)
