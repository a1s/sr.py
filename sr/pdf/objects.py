"""The file itself: objects, streams, and the cross-reference table.

doc/render.md#file-structure fixes the shape.
PDF 1.7, a classic cross-reference table, and no object streams,
so every object is written as itself and found by its byte offset.
Nothing is encrypted.
Streams are Flate-compressed, except one that arrives compressed
in a format of its own, which is a JPEG.

Three rules make the bytes a function of what is written,
which doc/render.md#reproducibility asks of the whole renderer.

* **Objects are numbered in the order they are reserved**,
  and written in number order.
  Nothing is keyed by identity or iterated out of a hash.
* **A dictionary writes its keys in the order they were given.**
  Python's own order, which is insertion order, is the order here.
* **Numbers are written by :func:`number`**:
  three decimals at most, no trailing zeros, and no negative zero,
  which is how doc/render.md#coordinates spells a length.
  The same spelling serves every other number in the file.

The trailer's `ID` is a digest of the body,
so two renders of one printout carry one identifier,
and two different documents carry two.

"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sr.printout.write import number as spelled
from sr.units import round_points

__all__ = [
    "HEADER",
    "LEVEL",
    "Hex",
    "Name",
    "Ref",
    "Stream",
    "Text",
    "Writer",
    "literal",
    "name",
    "number",
    "serialize",
    "text_string",
]

# The version line, then a comment of four bytes above 0x7F,
# which is how a file says it holds binary data to a tool
# that would otherwise guess it is text.
HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"

# The Flate level, fixed rather than left to the library's default,
# so that a change of default does not change the file.
LEVEL = 6

# The characters a name writes as themselves: the printable ASCII
# range, less the delimiters and the `#` that introduces an escape.
NAME_DELIMITERS = frozenset(b"()<>[]{}/%#")

# The characters a literal string escapes with a backslash.
STRING_ESCAPES = frozenset(b"()\\")

# The characters a text string may hold and still be written as a
# literal in PDFDocEncoding, which agrees with ASCII over this range.
PLAIN_TEXT = range(0x20, 0x7F)


class Name(str):
    """A PDF name, written with its slash: ``Name("Type")`` is ``/Type``."""

    __slots__ = ()


class Hex(bytes):
    """A string written in hexadecimal, as the trailer's `ID` is."""

    __slots__ = ()


class Text(str):
    """A text string: what a reader shows a person.

    Written as a literal where every character is printable ASCII,
    and as UTF-16BE with a byte order mark otherwise,
    which are the two encodings PDF gives a text string.

    """

    __slots__ = ()


@dataclass(frozen=True)
class Ref:
    """An indirect reference, to an object of the same writer.

    Attributes:
        number: The object's number.

    """

    number: int


@dataclass
class Stream:
    """A stream object: a dictionary, and the bytes after it.

    Attributes:
        entries: The dictionary, without `Length` and `Filter`,
            which the writer adds.
        data: The bytes, as they are to be read.
        compress: Whether the writer may Flate-compress them.
            Bytes in a compressed format of their own say so
            in ``entries`` and are written as they are.

    """

    entries: dict[str, Any] = field(default_factory=dict)
    data: bytes = b""
    compress: bool = True


def number(value: float) -> str:
    """Return a number as the file spells it.

    A float is rounded to three decimals first, half away from zero,
    which is the rounding the printout's own lengths went through.

    Args:
        value: The number.

    """
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number")
    if isinstance(value, int):
        return str(value)
    return spelled(round_points(value))


def name(text: str) -> bytes:
    """Return a name token, escaping what a name cannot hold as itself.

    Args:
        text: The name, without its slash.

    """
    out = bytearray(b"/")
    for byte in text.encode("utf-8"):
        if 0x21 <= byte <= 0x7E and byte not in NAME_DELIMITERS:
            out.append(byte)
        else:
            out.extend(b"#%02X" % byte)
    return bytes(out)


def literal(data: bytes) -> bytes:
    """Return a literal string holding these bytes.

    Parentheses and backslashes are escaped with a backslash,
    and every byte outside printable ASCII as three octal digits.
    A carriage return is among them, since a reader turns a bare one
    into a line feed.

    Args:
        data: The string's bytes.

    """
    out = bytearray(b"(")
    for byte in data:
        if byte in STRING_ESCAPES:
            out.extend(b"\\" + bytes([byte]))
        elif 0x20 <= byte <= 0x7E:
            out.append(byte)
        else:
            out.extend(b"\\%03o" % byte)
    out.extend(b")")
    return bytes(out)


def text_string(text: str) -> bytes:
    """Return a text string, in whichever encoding can hold it.

    A lone surrogate, which only a file name brings, is written
    as the one code unit it is, rather than refused.

    Args:
        text: What a reader shows.

    """
    if all(ord(character) in PLAIN_TEXT for character in text):
        return literal(text.encode("ascii"))
    encoded = text.encode("utf-16-be", "surrogatepass")
    return b"<FEFF" + encoded.hex().upper().encode("ascii") + b">"


def serialize(value: Any) -> bytes:
    """Return a direct object as the file spells it.

    Args:
        value: A :class:`Name`, a :class:`Text`, a :class:`Ref`,
            bytes for a string, a boolean, ``None``, a number,
            a sequence for an array, or a mapping with string keys
            for a dictionary.

    Raises:
        TypeError: The value is none of those.

    """
    if isinstance(value, Name):
        return name(value)
    if isinstance(value, Text):
        return text_string(value)
    if isinstance(value, Ref):
        return b"%d 0 R" % value.number
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if value is None:
        return b"null"
    if isinstance(value, int | float):
        return number(value).encode("ascii")
    if isinstance(value, Hex):
        return b"<" + value.hex().upper().encode("ascii") + b">"
    if isinstance(value, bytes):
        return literal(value)
    if isinstance(value, Mapping):
        parts = [b"<<"]
        for key, item in value.items():
            parts.append(name(key) + b" " + serialize(item))
        parts.append(b">>")
        return b" ".join(parts)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return b"[" + b" ".join(serialize(item) for item in value) + b"]"
    raise TypeError(f"cannot write a {type(value).__name__} into a PDF")


class Writer:
    """The object table of one file, and the file it becomes.

    An object is reserved, which gives it its number, and then put,
    which gives it its value.  The two are separate so that
    two objects can refer to each other: a page names its parent,
    and the parent lists its pages.

    """

    def __init__(self, compress: bool = True) -> None:
        """Start an empty table.

        Args:
            compress: Whether streams are Flate-compressed.
                ``False`` is ``--uncompressed``, which leaves
                the file readable in a text editor.

        """
        self.compress = compress
        self.objects: list[Any] = []

    def reserve(self) -> Ref:
        """Return the reference of a new object, whose value comes later."""
        self.objects.append(None)
        return Ref(len(self.objects))

    def put(self, ref: Ref, value: Any) -> Ref:
        """Give a reserved object its value, and return its reference.

        Args:
            ref: What :meth:`reserve` returned.
            value: The object: anything :func:`serialize` takes,
                or a :class:`Stream`.

        """
        self.objects[ref.number - 1] = value
        return ref

    def add(self, value: Any) -> Ref:
        """Reserve an object and give it its value at once.

        Args:
            value: The object.

        """
        return self.put(self.reserve(), value)

    def finish(self, root: Ref, info: Ref) -> bytes:
        """Return the whole file.

        Args:
            root: The document catalog.
            info: The information dictionary.

        Raises:
            ValueError: An object was reserved and never put.

        """
        out = bytearray(HEADER)
        offsets: list[int] = []
        for index, value in enumerate(self.objects, start=1):
            if value is None:
                raise ValueError(f"object {index} was reserved and never put")
            offsets.append(len(out))
            out.extend(b"%d 0 obj\n" % index)
            out.extend(self.body(value))
            out.extend(b"\nendobj\n")
        digest = Hex(hashlib.md5(out, usedforsecurity=False).digest())
        started = len(out)
        count = len(self.objects) + 1
        out.extend(b"xref\n0 %d\n0000000000 65535 f \n" % count)
        for offset in offsets:
            out.extend(b"%010d 00000 n \n" % offset)
        trailer = {
            "Size": count,
            "Root": root,
            "Info": info,
            "ID": [digest, digest],
        }
        out.extend(b"trailer\n" + serialize(trailer) + b"\n")
        out.extend(b"startxref\n%d\n%%%%EOF\n" % started)
        return bytes(out)

    def body(self, value: Any) -> bytes:
        """Return one object's bytes, between its `obj` and `endobj`.

        Args:
            value: The object.

        """
        if not isinstance(value, Stream):
            return serialize(value)
        data = value.data
        entries = dict(value.entries)
        if value.compress and self.compress:
            data = zlib.compress(data, LEVEL)
            entries["Filter"] = Name("FlateDecode")
        entries["Length"] = len(data)
        return serialize(entries) + b"\nstream\n" + data + b"\nendstream"
