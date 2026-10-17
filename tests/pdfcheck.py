"""Holding a PDF to the shape doc/render.md#file-structure gives it.

Three looks at a rendered file, each cheap,
and each from somewhere the writer is not:

* :func:`check` reads the file as a reader finds its way into one:
  `startxref` to the table, the table to each object, each stream's
  `Length` to its end.  A wrong offset or a wrong length is the fault
  a hand-written writer is likeliest to have, and nothing that only
  renders the file is obliged to notice it, since readers repair both.
* ``qpdf --check``, where qpdf is on the path, as a second opinion
  from a library that has spent decades on malformed files.
* :func:`pages` rasterises with pdfium, a reader of another lineage,
  for the tests that compare what is drawn.

The rest are small readers of what :func:`check` returns, enough to ask
a test's question of an object without a PDF library in between.

"""

from __future__ import annotations

import re
import shutil
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Parsed",
    "check",
    "entry",
    "pages",
    "references",
    "stream_of",
]

# One entry of a classic cross-reference table: twenty bytes.
XREF_ENTRY = re.compile(rb"(\d{10}) (\d{5}) ([nf]) \n")

# How a stream's dictionary states its length, which is direct here.
LENGTH = re.compile(rb"/Length (\d+)")


@dataclass(frozen=True)
class Parsed:
    """A file that passed the check, by its parts.

    Attributes:
        objects: Each object's body, between `obj` and `endobj`, by number.
        trailer: The trailer's dictionary.

    """

    objects: dict[int, bytes]
    trailer: bytes

    def kind(self, name: str) -> list[int]:
        """Return the numbers of the objects whose `Type` is ``name``.

        Args:
            name: The type, without its slash.

        """
        marker = b"/Type /" + name.encode("ascii")
        return [
            number
            for number, body in self.objects.items()
            if re.search(re.escape(marker) + rb"[\s/>\]]", dictionary(body))
        ]


def check(data: bytes, path: Path | None = None) -> Parsed:
    """Assert a PDF's structure, and return its objects.

    Args:
        data: The file.
        path: Where it is on disk, for qpdf; written there when given.

    Raises:
        AssertionError: Something about the structure is wrong.

    """
    assert data.startswith(b"%PDF-1.7\n"), "not a PDF 1.7 header"
    assert data.endswith(b"%%EOF\n"), "the file does not end with %%EOF"
    tail = data.rsplit(b"startxref\n", 1)
    assert len(tail) == 2, "no startxref"
    start = int(tail[1].split(b"\n", 1)[0])
    reached = data[start : start + 5]
    assert reached == b"xref\n", "startxref does not reach xref"
    head, rest = data[start + 5 :].split(b"\n", 1)
    first, count = (int(one) for one in head.split())
    assert first == 0, "the table does not start at object 0"
    rows = (rest[at * 20 : at * 20 + 20] for at in range(count))
    entries = [XREF_ENTRY.fullmatch(row) for row in rows]
    assert all(entries), "a cross-reference entry is not twenty bytes"
    assert entries[0] is not None and entries[0].group(3) == b"f"
    objects: dict[int, bytes] = {}
    for number in range(1, count):
        found = entries[number]
        assert found is not None and found.group(3) == b"n", f"object {number}"
        offset = int(found.group(1))
        opening = b"%d 0 obj\n" % number
        assert data[offset : offset + len(opening)] == opening, (
            f"object {number}'s offset does not reach it"
        )
        body_start = offset + len(opening)
        line_end = data.index(b"\n", body_start)
        if data[line_end : line_end + 8] == b"\nstream\n":
            # A stream's end is where its Length says, and nowhere else:
            # compressed bytes may hold `endobj` by chance.
            length = LENGTH.search(data[body_start:line_end])
            assert length is not None, f"object {number}'s Length"
            payload_end = line_end + 8 + int(length.group(1))
            closing = b"\nendstream\nendobj\n"
            assert data[payload_end : payload_end + len(closing)] == closing, (
                f"object {number}'s Length is not the stream's"
            )
            body_end = payload_end + len(b"\nendstream")
        else:
            body_end = data.index(b"\nendobj\n", body_start)
        objects[number] = data[body_start:body_end]
    trailer_text = rest[count * 20 :]
    assert trailer_text.startswith(b"trailer\n"), "no trailer after the table"
    trailer = trailer_text[len(b"trailer\n") :].split(b"\nstartxref", 1)[0]
    assert re.search(rb"/Size %d\b" % count, trailer), "the trailer's Size"
    assert b"/ObjStm" not in data, "an object stream"
    assert b"/Encrypt" not in trailer, "encryption"
    if path is not None:
        path.write_bytes(data)
        qpdf(path)
    return Parsed(objects, trailer)


def qpdf(path: Path) -> None:
    """Run ``qpdf --check`` on a file, where qpdf is installed.

    Args:
        path: The file.

    Raises:
        AssertionError: qpdf found an error or a warning.

    """
    command = shutil.which("qpdf")
    if command is None:
        return
    done = subprocess.run(  # the argv is ours, not a shell string
        [command, "--check", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def dictionary(body: bytes) -> bytes:
    """Return an object's dictionary, without the stream after it.

    Args:
        body: The object's body.

    """
    return body.split(b"\nstream\n", 1)[0]


def stream_of(body: bytes) -> bytes:
    """Return a stream object's bytes, inflated where they were deflated.

    Args:
        body: The object's body.

    """
    entries, payload = body.split(b"\nstream\n", 1)
    data = payload[: -len(b"\nendstream")]
    if b"/FlateDecode" in entries:
        return zlib.decompress(data)
    return data


def entry(body: bytes, key: str) -> bytes:
    """Return the value one key of an object's dictionary holds, as written.

    Good for the values a test asks about: a reference, a name,
    a number, a string, or an array that holds no array.

    Args:
        body: The object's body.
        key: The key, without its slash.

    Raises:
        AssertionError: The key is not there.

    """
    pattern = (
        rb"/" + re.escape(key.encode("ascii")) + rb" "
        rb"(\d+ 0 R|\[[^\[\]]*\]|/[^\s/<>\[\]()]+|\((?:\\.|[^\\)])*\)"
        rb"|<[0-9A-Fa-f]*>|-?[\d.]+|true|false|null|<<)"
    )
    found = re.search(pattern, dictionary(body))
    assert found is not None, f"no /{key} in {dictionary(body)[:200]!r}"
    return found.group(1)


def references(value: bytes) -> list[int]:
    """Return the object numbers a value refers to, in order.

    Args:
        value: A value, as :func:`entry` returns it.

    """
    return [int(one) for one in re.findall(rb"(\d+) 0 R", value)]


def pages(data: bytes, scale: float = 2) -> list[Any]:
    """Return each page rasterised by pdfium, as a Pillow image.

    Args:
        data: The file.
        scale: Pixels per point.

    """
    import pypdfium2

    document = pypdfium2.PdfDocument(data)
    return [
        document[index].render(scale=scale).to_pil().convert("RGB")
        for index in range(len(document))
    ]
