"""The four 1-D symbologies: Code 128, Code 39, Code 93, Interleaved 2 of 5.

Each returns its runs, bar first, in modules.  The standards fix
the patterns and the check characters; what they leave open, and
what doc/barcode.md makes a rule, is:

* **Code 128's code sets**, chosen by the rule in :func:`code128_values`.
  The standard allows many symbols for one value, and a printout records
  which one was drawn.
* **The wide-to-narrow ratio** of the two symbologies that have one:
  2 for Code 39, 3 for Interleaved 2 of 5.  Both standards allow a range.
* **Check characters**: Code 128 and Code 93 carry the ones
  their standards require, and Code 39 and Interleaved 2 of 5,
  for which the standards make one optional, carry none.

"""

from __future__ import annotations

from collections.abc import Callable

from sr.barcode.symbol import Unencodable

__all__ = ["ENCODERS", "code39", "code93", "code128", "interleaved"]

# Code 128's 107 patterns, by symbol value: bar, space, bar,
# space, bar, space widths, and the stop pattern's seven.
CODE128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()

# Code 128's switching and start values.
CODE_C, CODE_B, CODE_A = 99, 100, 101
START = {"A": 103, "B": 104, "C": 105}
STOP = 106

# Code 39's characters, and which of each one's nine elements are wide.
CODE39 = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100", "A": "100001001", "B": "001001001",
    "C": "101001000", "D": "000011001", "E": "100011000", "F": "001011000",
    "G": "000001101", "H": "100001100", "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110", "U": "110000001", "V": "011000001",
    "W": "111000000", "X": "010010001", "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100", "$": "010101000",
    "/": "010100010", "+": "010001010", "%": "000101010", "*": "010010100",
}  # fmt: skip

# What a wide element of Code 39 and of Interleaved 2 of 5 is worth.
CODE39_WIDE = 2
INTERLEAVED_WIDE = 3

# Code 93's 47 characters in value order, the four shifts last but one
# group, and each one's six widths; the start and stop pattern follows.
CODE93_CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"
CODE93 = (
    "131112 111213 111312 111411 121113 121212 121311 111114 131211 141111 "
    "211113 211212 211311 221112 221211 231111 112113 112212 112311 122112 "
    "132111 111123 111222 111321 121122 131121 212112 212211 211122 211221 "
    "221121 222111 112122 112221 122121 123111 121131 311112 311211 321111 "
    "112131 113121 211131 121221 312111 311121 122211"
).split()
CODE93_EDGE = "111141"

# The Code 93 characters a value's character is written as directly.
CODE93_PLAIN = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. ")

# Code 93's four shift characters, by the value each takes.
DOLLAR, PERCENT, SLASH, PLUS = 43, 44, 45, 46

# Interleaved 2 of 5's digits, and which of each one's five elements are wide.
INTERLEAVED = (
    "00110", "10001", "01001", "11000", "00101",
    "10100", "01100", "00011", "10010", "01010",
)  # fmt: skip


def code128(value: str) -> tuple[int, ...]:
    """Return a Code 128 symbol's runs.

    Args:
        value: What to encode.

    Raises:
        Unencodable: It holds a character outside ASCII.

    """
    for character in value:
        if ord(character) > 127:
            raise Unencodable(f"{character!r} is outside ASCII 0-127")
    values = code128_values(value)
    weighted = sum(index * one for index, one in enumerate(values))
    check = (values[0] + weighted) % 103
    runs: list[int] = []
    for one in (*values, check, STOP):
        runs.extend(int(width) for width in CODE128[one])
    return tuple(runs)


def code128_values(value: str) -> list[int]:
    """Return the symbol values of a Code 128 symbol, start code first.

    Set C carries any run of four digits or more, as many pairs of it
    as there are.  Elsewhere the symbol is in set A or set B, and stays
    in the one it is in for as long as each character is in it: a control
    character needs A and a lower-case letter, or any of `` ` { | } ~``
    and DEL, needs B.  A switch is always a latch, never a shift.

    Which of A and B a symbol starts in is decided by the first character
    in the value that only one of them has, and is B where none is.
    Leaving set C is decided by the next character alone.

    Args:
        value: What to encode, ASCII only.

    """
    found: list[int] = []
    codes = [ord(character) for character in value]
    current = ""
    index = 0
    while index < len(codes):
        run = digit_run(codes, index)
        if run >= 4:
            if current != "C":
                found.append(START["C"] if not current else CODE_C)
                current = "C"
            for _ in range(run // 2):
                tens, units = codes[index] - 0x30, codes[index + 1] - 0x30
                found.append(tens * 10 + units)
                index += 2
            continue
        code = codes[index]
        if not current:
            current = first_set(codes)
            found.append(START[current])
        elif current == "C":
            current = "A" if code < 32 else "B"
            found.append(CODE_A if current == "A" else CODE_B)
        elif current == "A" and code >= 96:
            current = "B"
            found.append(CODE_B)
        elif current == "B" and code < 32:
            current = "A"
            found.append(CODE_A)
        found.append(code + 64 if current == "A" and code < 32 else code - 32)
        index += 1
    return found


def digit_run(codes: list[int], index: int) -> int:
    """Return how many digits in a row start at a position.

    Args:
        codes: The value's character codes.
        index: Where to look.

    """
    end = index
    while end < len(codes) and 0x30 <= codes[end] <= 0x39:
        end += 1
    return end - index


def first_set(codes: list[int]) -> str:
    """Return the set a symbol that does not start in C starts in.

    Args:
        codes: The value's character codes.

    """
    for code in codes:
        if code < 32:
            return "A"
        if code >= 96:
            return "B"
    return "B"


def code39(value: str) -> tuple[int, ...]:
    """Return a Code 39 symbol's runs, with no check character.

    Args:
        value: What to encode.

    Raises:
        Unencodable: It holds a character Code 39 has no pattern for.

    """
    for character in value:
        if character not in CODE39 or character == "*":
            raise Unencodable(
                f"{character!r} is not one of the digits, A-Z upper case,"
                " space, or - . $ / + %"
            )
    runs: list[int] = []
    for character in f"*{value}*":
        if runs:
            runs.append(1)
        pattern = CODE39[character]
        runs.extend(CODE39_WIDE if wide == "1" else 1 for wide in pattern)
    return tuple(runs)


def code93(value: str) -> tuple[int, ...]:
    """Return a Code 93 symbol's runs, with both of its check characters.

    Characters outside its 43 are written as a shift and a letter,
    the full ASCII table of the standard's annex.

    Args:
        value: What to encode.

    Raises:
        Unencodable: It holds a character outside ASCII.

    """
    values: list[int] = []
    for character in value:
        code = ord(character)
        if code > 127:
            raise Unencodable(f"{character!r} is outside the ASCII range")
        values.extend(full_ascii(code))
    for weights in (20, 15):
        total = sum(
            one * ((len(values) - 1 - index) % weights + 1)
            for index, one in enumerate(values)
        )
        values.append(total % 47)
    runs: list[int] = [int(width) for width in CODE93_EDGE]
    for one in values:
        runs.extend(int(width) for width in CODE93[one])
    runs.extend(int(width) for width in CODE93_EDGE)
    runs.append(1)
    return tuple(runs)


def full_ascii(code: int) -> tuple[int, ...]:
    """Return the Code 93 values that spell one ASCII character.

    Digits, upper-case letters, space, ``-``, and ``.`` are their own
    characters.  Everything else is a shift and a letter, by the full
    ASCII table Code 39 and Code 93 share -- ``$ / + %`` included,
    although Code 93 has a character of its own for each of them.

    Args:
        code: The character's code, 0 to 127.

    """
    character = chr(code)
    if character in CODE93_PLAIN:
        return (CODE93_CHARACTERS.index(character),)
    letter = CODE93_CHARACTERS.index
    if code == 0:
        return (PERCENT, letter("U"))
    if code <= 26:
        return (DOLLAR, letter(chr(0x40 + code)))
    if code <= 31:
        return (PERCENT, letter(chr(0x41 + code - 27)))
    if code == 0x40:
        return (PERCENT, letter("V"))
    if code == 0x60:
        return (PERCENT, letter("W"))
    if 0x61 <= code <= 0x7A:
        return (PLUS, letter(chr(code - 0x20)))
    if code <= 0x3A:
        return (SLASH, letter(chr(0x41 + code - 0x21)))
    if code <= 0x3F:
        return (PERCENT, letter(chr(0x46 + code - 0x3B)))
    if code <= 0x5F:
        return (PERCENT, letter(chr(0x4B + code - 0x5B)))
    return (PERCENT, letter(chr(0x50 + code - 0x7B)))


def interleaved(value: str) -> tuple[int, ...]:
    """Return an Interleaved 2 of 5 symbol's runs, with no check digit.

    Args:
        value: What to encode.

    Raises:
        Unencodable: It holds a character that is not a digit,
            or an odd number of digits.

    """
    for character in value:
        if not ("0" <= character <= "9"):
            raise Unencodable(f"{character!r} is not a digit")
    if len(value) % 2:
        raise Unencodable(
            "interleaved 2 of 5 encodes digits in pairs,"
            f" and {len(value)} is an odd number of them;"
            " use format to fix the width"
        )
    runs: list[int] = [1, 1, 1, 1]
    for index in range(0, len(value), 2):
        bars = INTERLEAVED[int(value[index])]
        spaces = INTERLEAVED[int(value[index + 1])]
        for bar, space in zip(bars, spaces, strict=True):
            runs.append(INTERLEAVED_WIDE if bar == "1" else 1)
            runs.append(INTERLEAVED_WIDE if space == "1" else 1)
    runs.extend((INTERLEAVED_WIDE, 1, 1))
    return tuple(runs)


# The 1-D types, by the name doc/template.md gives each.
ENCODERS: dict[str, Callable[[str], tuple[int, ...]]] = {
    "Code128": code128,
    "Code39": code39,
    "Code93": code93,
    "2of5i": interleaved,
}
