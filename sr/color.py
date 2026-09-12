"""Colours: four spellings in, one out.

doc/template.md#color is the contract.  A template may write a hash
and six hexadecimal digits, one of 22 names, three comma-separated
components, or a single integer packed by bits; a printout carries
``"#RRGGBB"`` in upper case and nothing else.

The part worth reading twice is the triple.  Whether its components
are channel values 0-255 or fractions 0-1 is decided by the triple
as a whole rather than component by component: three integers are
channels, and anything else makes all three fractions.  So ``"1,1,1"``
is nearly black while ``"0,1,0."`` is green: one component carries
a decimal point.  Reading each component on its own would make the first
of those green too, which is a page of a very different colour.

"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sr.errors import BadValue
from sr.units import NUMBER, round_half_away

__all__ = ["NAMES", "channels", "parse_color"]

# The 16 HTML 4.01 names, then the six from the CSS extended set that
# doc/template.md adds.  `cyan` and `aqua` are one colour, as are `magenta`
# and `fuchsia`; there is no `grey`.
NAMES = {
    "black": "#000000",
    "silver": "#C0C0C0",
    "gray": "#808080",
    "white": "#FFFFFF",
    "maroon": "#800000",
    "red": "#FF0000",
    "purple": "#800080",
    "fuchsia": "#FF00FF",
    "green": "#008000",
    "lime": "#00FF00",
    "olive": "#808000",
    "yellow": "#FFFF00",
    "navy": "#000080",
    "blue": "#0000FF",
    "teal": "#008080",
    "aqua": "#00FFFF",
    "cyan": "#00FFFF",
    "darkgray": "#A9A9A9",
    "lightgray": "#D3D3D3",
    "magenta": "#FF00FF",
    "orange": "#FFA500",
    "pink": "#FFC0CB",
}

# Six digits, no more and no fewer: `#abc` is not a short form
# and `#aabbccdd` is not an alpha channel.
HEX = re.compile(r"#([0-9A-Fa-f]{6})\Z")

# An integer component: ASCII digits with an optional sign, leading zeros
# allowed, no underscores.  `1_0` is a number, but it is not an integer.
INTEGER = re.compile(r"[+-]?[0-9]+\Z")

# The single-integer spelling takes no sign:
# a colour below zero is not a colour that was meant.
UNSIGNED = re.compile(r"[0-9]+\Z")

# How large the single integer may be before it is a mistyped number
# rather than a colour with unused high bits.
INTEGER_LIMIT = 1 << 64

# Bits 16-23 red, 8-15 green, 0-7 blue; above 23, ignored.
CHANNEL_MASK = 0xFF
COLOR_MASK = 0xFFFFFF

# What a fraction is multiplied by to reach a channel.
FULL = 255

# `str.lower` folds letters outside ASCII too, which would let a dotted
# capital I or a Kelvin sign name a colour.  This lowers A-Z and nothing
# else, which is what "case-insensitive" means here.
ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def parse_color(text: str) -> str:
    """Return a colour as ``"#RRGGBB"``, upper case.

    Args:
        text: The property's value, in any of the four spellings.

    Raises:
        BadValue: The text is not a colour.

    """
    stripped = text.strip()
    if not stripped:
        raise BadValue("empty colour")
    if stripped.startswith("#"):
        return from_hex(stripped)
    named = NAMES.get(stripped.translate(ASCII_LOWER))
    if named is not None:
        return named
    if "," in stripped:
        return from_triple(stripped)
    return from_integer(stripped)


def channels(color: str) -> tuple[int, int, int]:
    """Return the red, green and blue of a canonical ``"#RRGGBB"``.

    The renderer works in components, and this is the one place
    that takes the canonical form apart, so nothing else has
    to know where the digits sit.

    Args:
        color: A colour as :func:`parse_color` returns it.

    """
    packed = int(color[1:], 16)
    return (
        (packed >> 16) & CHANNEL_MASK,
        (packed >> 8) & CHANNEL_MASK,
        packed & CHANNEL_MASK,
    )


def from_hex(text: str) -> str:
    """Return the canonical form of a ``#RRGGBB`` colour.

    Args:
        text: The value, stripped, beginning with a hash.

    Raises:
        BadValue: It is not a hash and six hexadecimal digits.

    """
    match = HEX.match(text)
    if match is None:
        raise BadValue(f'bad colour "{text}": want #RRGGBB')
    return "#" + match.group(1).upper()


def from_triple(text: str) -> str:
    """Return the colour three comma-separated components name.

    Args:
        text: The value, stripped, holding at least one comma.

    Raises:
        BadValue: There are not three components, or one is out of range.

    """
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3:
        raise BadValue(f'bad colour "{text}": want three components')
    if all(INTEGER.match(part) for part in parts):
        return pack(channel_from_integer(text, part) for part in parts)
    return pack(channel_from_fraction(text, part) for part in parts)


def channel_from_integer(text: str, part: str) -> int:
    """Return a channel from a component read as a value 0-255.

    Args:
        text: The whole colour, for the diagnostic.
        part: The one component, already stripped.

    Raises:
        BadValue: The component is outside 0-255.

    """
    value = int(part)
    if not 0 <= value <= FULL:
        raise BadValue(f'bad colour "{text}": component "{part}" out of 0-255')
    return value


def channel_from_fraction(text: str, part: str) -> int:
    """Return a channel from a component read as a fraction 0-1.

    The scaling rounds halves away from zero, as every other rounding
    in the engine does, so 0.5 is 128 rather than 127.

    Args:
        text: The whole colour, for the diagnostic.
        part: The one component, already stripped.

    Raises:
        BadValue: The component is not a number, or is outside 0-1.

    """
    value = float(part.replace("_", "")) if NUMBER.match(part) else None
    if value is None or not 0.0 <= value <= 1.0:
        raise BadValue(f'bad colour "{text}": component "{part}" out of 0-1')
    return int(round_half_away(value * FULL))


def from_integer(text: str) -> str:
    """Return the colour a single integer packs.

    Bits 16-23 are red, 8-15 green, 0-7 blue, and anything above
    is ignored -- so ``"16777216"`` is black.

    Args:
        text: The value, stripped.

    Raises:
        BadValue: It is not an integer, or it is longer than one can be.

    """
    if UNSIGNED.match(text):
        packed = int(text)
        if packed < INTEGER_LIMIT:
            return f"#{packed & COLOR_MASK:06X}"
    raise BadValue(f'bad colour "{text}"')


def pack(values: Iterable[int]) -> str:
    """Return ``"#RRGGBB"`` from three channels.

    Args:
        values: The three channels, red first.

    """
    red, green, blue = values
    return f"#{red:02X}{green:02X}{blue:02X}"
