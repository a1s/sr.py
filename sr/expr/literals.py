r"""String and bytes literals, read by Starlark's rules rather than Python's.

The tree comes from Python's parser, and Python reads a literal by its
own rules, which are not Starlark's.  It takes escapes Starlark refuses,
such as ``\d`` and ``\N{...}``, prefixes Starlark has not got, such as
``u``, ``R`` and ``br``, and two literals in a row as one.  It reads
``\xff`` in a string as a character, where Starlark reads a byte.
And it refuses a bytes literal holding a character that is not ASCII,
or keeps a ``\u`` escape in one as six characters, where Starlark writes
the character's UTF-8 encoding. Each of those is a different answer
to the same template.

So the literals are read here first, from the tokens, and the parser
is handed the expression with each literal's contents blanked: the same
characters in the same places, none of them an escape.  What each literal
holds is put into the tree afterwards, found by where the literal begins.
Since the parser never sees an escape, it never warns about one either,
and its warnings stay off standard error without any warning filter.

doc/expressions.md#literals is the specification.

"""

from __future__ import annotations

import io
import re
import string
import tokenize
from dataclasses import dataclass
from typing import Final

from sr.errors import ExpressionError
from sr.expr.values import SURROGATE

__all__ = ["Lexed", "lex", "read"]

# The prefixes Starlark has, in the one case it has them in.
PREFIXES: Final = frozenset({"", "r", "b", "rb"})

# The escapes that are one character after the backslash.
SIMPLE: Final = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}

# The escapes that take hexadecimal digits, and how many each takes.
DIGITS: Final = {"x": 2, "u": 4, "U": 8}

# The tokens that open and close an f-string or a t-string, on the Pythons
# whose tokenizer takes one apart.  What is inside is the tree's to refuse,
# which it does whole, so the literals in it are passed over here.
OPENING: Final = frozenset(
    getattr(tokenize, name)
    for name in ("FSTRING_START", "TSTRING_START")
    if hasattr(tokenize, name)
)
CLOSING: Final = frozenset(
    getattr(tokenize, name)
    for name in ("FSTRING_END", "TSTRING_END")
    if hasattr(tokenize, name)
)

# The tokens that may stand between two literals without separating them.
BETWEEN: Final = frozenset(
    {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.COMMENT,
        tokenize.INDENT,
        tokenize.DEDENT,
    }
)

# The two characters that end a line, which a blanked literal keeps.
BREAKS: Final = ("\n", "\r")

# A carriage return on its own.  Python's parser ends a line at one, and
# its tokenizer, which is handed a line at a time, does not: it takes one
# into the token that follows, and numbers every later line one short.
LONE_RETURN: Final = re.compile(r"\r(?!\n)")

# What two literals in a row are told.  Python joins them into one,
# and Starlark has no such rule.
ADJACENT: Final = "two literals in a row are not joined; write + between them"


@dataclass(frozen=True)
class Lexed:
    """An expression with its literals read, and blanked for the parser.

    Attributes:
        text: The expression with each literal's contents replaced,
            character for character, by characters that are not escapes,
            and each carriage return that ends a line on its own
            replaced by a newline.
        values: What each literal holds, by where it begins
            as the parser reports it: its line, and its column
            in UTF-8 bytes.

    """

    text: str
    values: dict[tuple[int, int], str | bytes]


def prefix_of(written: str) -> str:
    """Return a literal's prefix, which is what comes before its quote.

    A literal ends in its quote, and the first quote of that kind
    ends the prefix.

    Args:
        written: The literal as written.

    """
    return written[: written.index(written[-1])]


def parts(written: str) -> tuple[str, str, str]:
    """Return a literal's prefix, its opening quote, and what is between.

    Args:
        written: The literal as written.

    """
    prefix = prefix_of(written)
    quote = written[-1]
    if written.startswith(quote * 3, len(prefix)):
        quote *= 3
    return prefix, quote, written[len(prefix) + len(quote) : -len(quote)]


def formatted(written: str) -> bool:
    """Report whether a literal is an f-string, which the tree refuses.

    Args:
        written: The literal as written.

    """
    return "f" in prefix_of(written).lower()


def lex(source: str) -> Lexed:
    """Read every literal in an expression, and blank each for the parser.

    Args:
        source: The expression, as the template wrote it.

    Raises:
        ExpressionError: A literal is not one Starlark has, or two stand
            in a row.

    """
    # Every line ends in a newline from here on, one character for one,
    # so that the tokenizer and the parser count the same lines and
    # every character stays where the template wrote it.
    lined = LONE_RETURN.sub("\n", source)
    lines = io.StringIO(lined).readlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(lined).readline))
    except (SyntaxError, tokenize.TokenError):
        # Not even Python's tokens, and the parser will say why.
        return Lexed(lined, {})
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    blanked = list(lined)
    found: list[tuple[int, int, str | bytes]] = []
    last: tokenize.TokenInfo | None = None
    depth = 0
    for token in tokens:
        if token.type in BETWEEN:
            continue
        if token.type in OPENING:
            depth += 1
        elif token.type in CLOSING:
            depth -= 1
        elif token.type == tokenize.STRING and not depth:
            written = token.string
            if not formatted(written):
                row, column = token.start
                at = starts[row - 1] + column
                if (
                    last is not None
                    and last.type == tokenize.STRING
                    and not formatted(last.string)
                ):
                    raise ExpressionError(ADJACENT, offset=at)
                found.append((row, column, read(written, at)))
                blanked[at : at + len(written)] = blank(written)
        last = token
    text = "".join(blanked)
    rows = io.StringIO(text).readlines()
    values = {
        (row, len(rows[row - 1][:column].encode())): value
        for row, column, value in found
    }
    return Lexed(text, values)


def blank(written: str) -> str:
    """Return a literal with its contents replaced, for the parser.

    The prefix and the quotes stay, and so does every line break,
    with the backslash that continues a line before it, so that
    the parser sees a literal of the same kind in the same place.
    Everything else becomes an underscore.

    Args:
        written: The literal as written.

    """
    prefix, quote, body = parts(written)
    kept = []
    for index, one in enumerate(body):
        following = body[index + 1 : index + 2]
        if one in BREAKS or (one == "\\" and following in BREAKS):
            kept.append(one)
        else:
            kept.append("_")
    return prefix + quote + "".join(kept) + quote


def read(written: str, at: int) -> str | bytes:
    """Return what one literal holds, read by Starlark's rules.

    Args:
        written: The literal as written, prefix and quotes included.
        at: Where it begins in the expression, for a diagnostic.

    Raises:
        ExpressionError: The literal is not one Starlark has.

    """
    prefix, quote, body = parts(written)
    if prefix not in PREFIXES:
        raise ExpressionError(unknown_prefix(prefix), offset=at)
    reader = Reader(body, at + len(prefix) + len(quote), binary="b" in prefix)
    if "r" in prefix:
        reader.text(0, len(body))
    else:
        reader.escapes()
    return reader.value()


def unknown_prefix(prefix: str) -> str:
    """Return the complaint about a prefix, naming Starlark's spelling.

    Args:
        prefix: The prefix as written.

    """
    letters = prefix.lower()
    spelling = "".join(letter for letter in "rb" if letter in letters)
    if spelling:
        return f"there is no {prefix} prefix; write {spelling}"
    return f"there is no {prefix} prefix; leave it out"


class Reader:
    """The contents of one literal, read into a string or into bytes.

    Attributes:
        body: The literal between its quotes.
        at: Where the body begins in the expression, for a diagnostic.
        binary: Whether this is a bytes literal.
        chars: What a string literal holds, so far.
        data: What a bytes literal holds, so far.

    """

    def __init__(self, body: str, at: int, *, binary: bool) -> None:
        """Prepare to read one literal's body.

        Args:
            body: The literal between its quotes.
            at: Where the body begins in the expression.
            binary: Whether this is a bytes literal.

        """
        self.body = body
        self.at = at
        self.binary = binary
        self.chars: list[str] = []
        self.data = bytearray()

    def value(self) -> str | bytes:
        """Return what the literal holds."""
        return bytes(self.data) if self.binary else "".join(self.chars)

    def refuse(self, index: int, message: str) -> ExpressionError:
        """Return the error for what is at one place in the body.

        Args:
            index: Where in the body the problem begins.
            message: What is wrong.

        """
        return ExpressionError(message, offset=self.at + index)

    def put(self, chars: str) -> None:
        """Add characters, which a bytes literal holds as UTF-8.

        Args:
            chars: The characters.

        """
        if self.binary:
            self.data += chars.encode()
        else:
            self.chars.append(chars)

    def text(self, start: int, end: int) -> None:
        """Add a stretch of the body as it is written.

        Args:
            start: Where the stretch begins.
            end: Where it ends.

        """
        found = SURROGATE.search(self.body, start, end)
        if found is not None:
            code = ord(found.group())
            message = f"invalid Unicode code point U+{code:04X}"
            raise self.refuse(found.start(), message)
        # A line break written into a literal is a newline, however
        # the text spells it; a lone carriage return is one already.
        self.put(self.body[start:end].replace("\r\n", "\n"))

    def escapes(self) -> None:
        """Read the body, escapes and all."""
        index = 0
        while index < len(self.body):
            backslash = self.body.find("\\", index)
            if backslash < 0:
                backslash = len(self.body)
            self.text(index, backslash)
            if backslash == len(self.body):
                break
            index = self.escape(backslash)

    def escape(self, index: int) -> int:
        """Read the escape at a backslash, and return where it ends.

        Args:
            index: Where the backslash is in the body.

        """
        letter = self.body[index + 1 : index + 2]
        after = index + 2
        if not letter:
            # A tokenizer ends no literal on a backslash; for completeness.
            raise self.refuse(index, "invalid escape sequence \\")
        if letter in BREAKS:
            if letter == "\r" and self.body.startswith("\n", after):
                after += 1
        elif letter in SIMPLE:
            self.put(SIMPLE[letter])
        elif letter in string.octdigits:
            while (
                after < min(index + 4, len(self.body))
                and self.body[after] in string.octdigits
            ):
                after += 1
            spelt = self.body[index:after]
            self.byte(index, spelt, int(spelt[1:], 8), "octal")
        elif letter in DIGITS:
            digits = self.body[after : after + DIGITS[letter]]
            spelt = "\\" + letter + digits
            if len(digits) < DIGITS[letter]:
                raise self.refuse(index, f"truncated escape sequence {spelt}")
            if any(digit not in string.hexdigits for digit in digits):
                raise self.refuse(index, f"invalid escape sequence {spelt}")
            after += len(digits)
            if letter == "x":
                self.byte(index, spelt, int(digits, 16), "hex")
            else:
                self.code_point(index, spelt, int(digits, 16))
        else:
            raise self.refuse(index, f"invalid escape sequence \\{letter}")
        return after

    def byte(self, index: int, spelt: str, code: int, kind: str) -> None:
        r"""Add the byte an ``\x`` or octal escape names.

        A string literal takes one only up to 0x7F,
        where a byte and a code point are the same thing.

        Args:
            index: Where the escape is in the body.
            spelt: The escape as written.
            code: The byte it names.
            kind: ``hex`` or ``octal``, for the diagnostic.

        """
        if self.binary:
            if code > 0xFF:
                raise self.refuse(index, f"invalid escape sequence {spelt}")
            self.data.append(code)
        elif code > 0x7F:
            raise self.refuse(
                index,
                f"non-ASCII {kind} escape {spelt} "
                f"(use \\u{code:04X} for the UTF-8 encoding of U+{code:04X})",
            )
        else:
            self.chars.append(chr(code))

    def code_point(self, index: int, spelt: str, code: int) -> None:
        r"""Add the character a ``\u`` or ``\U`` escape names.

        Args:
            index: Where the escape is in the body.
            spelt: The escape as written.
            code: The code point it names.

        """
        if code > 0x10FFFF:
            raise self.refuse(
                index, f"code point out of range: {spelt} (max \\U0010FFFF)"
            )
        if 0xD800 <= code <= 0xDFFF:
            raise self.refuse(index, f"invalid Unicode code point U+{code:04X}")
        self.put(chr(code))
