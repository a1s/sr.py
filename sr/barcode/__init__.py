"""Barcodes: every symbology doc/template.md#barcode names.

:func:`encode` is the whole surface the layout uses.  It takes
a type and the string a `barcode` element finally holds, and returns
a :class:`Symbol` in modules, quiet zone included, or raises
:class:`Unencodable` with the reason.  How big that symbol is drawn,
and where, is the layout's business; the encoders know nothing of points.

Each symbology is its own module, and the rules each follows where
its standard leaves a choice are written down in doc/barcode.md,
because every one of them changes the runs a printout records.
The 2-D types share one step before that: :func:`payload` turns
the value into bytes in the element's charset, and :func:`designator`
says which ECI, if any, the symbol opens with.
:mod:`sr.barcode.readable` is apart from all of them: it judges the
colours a symbol is printed in, and is asked when the template loads.

"""

from __future__ import annotations

from functools import lru_cache

from sr.barcode import aztec, dmtx, linear, qr
from sr.barcode.symbol import Symbol, Unencodable

__all__ = [
    "CHARSETS",
    "ECI",
    "LINEAR",
    "QUIET",
    "Symbol",
    "Unencodable",
    "designator",
    "encode",
    "payload",
]

# The quiet zone each type carries, in modules: at each end of a
# 1-D symbol, and all round a 2-D one.  doc/template.md#barcode.
QUIET = {
    "Code128": 10,
    "Code39": 10,
    "Code93": 10,
    "2of5i": 10,
    "QR-L": 4,
    "QR-M": 4,
    "QR-Q": 4,
    "QR-H": 4,
    "DataMatrix": 1,
    "Aztec": 0,
}

# Each charset a 2-D type encodes in, and its ECI assignment number.
ECI = {"utf-8": 26, "iso-8859-1": 3}

# The charsets, and the types that encode characters rather than bytes
# and so take none: what the template model checks `charset` against.
CHARSETS = tuple(ECI)
LINEAR = tuple(linear.ENCODERS)

# How many symbols :func:`encode` keeps.  A band is measured
# more than once, a header or a footer at least twice a page,
# and each measure encodes its barcodes.
CACHED_SYMBOLS = 1024


@lru_cache(maxsize=CACHED_SYMBOLS)
def encode(
    kind: str,
    value: str,
    charset: str = "utf-8",
    eci: bool = False,
) -> Symbol:
    """Return the symbol a value encodes to.

    The answer is cached, which a :class:`Symbol` being immutable allows;
    a refusal is not.

    Args:
        kind: The type, one of doc/template.md's barcode enum.
        value: The string to encode.
        charset: What a 2-D type encodes the characters in;
            a 1-D type, which takes ASCII only, ignores it.
        eci: Whether a 2-D symbol names its charset;
            a 1-D type ignores it too.

    Raises:
        Unencodable: The type cannot carry the value.

    """
    if not value:
        raise Unencodable("the value is empty")
    quiet = QUIET[kind]
    if kind in linear.ENCODERS:
        return Symbol(bars=linear.ENCODERS[kind](value), quiet=quiet)
    data = payload(value, charset)
    number = designator(value, charset, eci)
    if kind.startswith("QR-"):
        return Symbol(modules=qr.encode(data, kind[3:], number), quiet=quiet)
    if kind == "DataMatrix":
        return Symbol(modules=dmtx.encode(data, number), quiet=quiet)
    return Symbol(modules=aztec.encode(data, number), quiet=quiet)


def payload(value: str, charset: str) -> bytes:
    """Return the bytes a 2-D type encodes for a value.

    Args:
        value: The string to encode.
        charset: ``utf-8`` or ``iso-8859-1``.

    Raises:
        Unencodable: A character is not in ISO 8859-1.

    """
    if charset == "utf-8":
        return value.encode("utf-8")
    for character in value:
        if ord(character) > 0xFF:
            raise Unencodable(f"{character!r} is not in ISO 8859-1")
    return value.encode("latin-1")


def designator(value: str, charset: str, eci: bool) -> int | None:
    """Return the ECI a 2-D symbol opens with, or ``None`` for none.

    An ASCII value reads the same in every charset,
    so it never needs one.

    Args:
        value: The string to encode.
        charset: ``utf-8`` or ``iso-8859-1``.
        eci: Whether the element asks for one.

    """
    if not eci or value.isascii():
        return None
    return ECI[charset]
