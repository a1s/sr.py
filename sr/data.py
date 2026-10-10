"""Records: JSON in, coerced values out.

doc/template.md#data-input is two sentences long, and both are load
bearing.  Records come from **either** a single JSON array document
**or** NDJSON with one record per line, and the engine buffers the
whole dataset because ``DATA_COUNT``, report-scoped aggregates and
keep-together lookahead all need the full sequence.

What turns JSON into values is the template's
[`records`](doc/template.md#records) declaration.  A declared
member is coerced once, here, and is what an expression reaches
as a bare name; an undeclared member is passed through as JSON
gave it and stays reachable as ``THIS["name"]``.

Coercion is **by the declared type, not by the JSON type**.  That is
the point of declaring: `"19.99"` in the data is an exact decimal
because the template says `type="decimal"`, and a JSON number in the
same member is one too.  So a value that arrives as a string is read
by the same reader a `parameter` default is read by, and one that
arrives as a number, a boolean or ``null`` is converted to the text
that spells it and read the same way.  One reader per type, one answer
per type.

A JSON ``null`` is ``None`` in a `nullable` member and an error
in any other, naming the member and the record index, which is
the one coercion rule doc/template.md#records writes out.

"""

from __future__ import annotations

import codecs
import json
from pathlib import Path
from typing import Any, TextIO

from sr.errors import BadValue, BuildError, Location
from sr.expr.values import (
    Record,
    go_shortest,
    json_without_surrogates,
    without_surrogates,
)
from sr.template.model import Member, Records, freeze, parse_text

__all__ = ["coerce", "read_records", "records_from", "records_in"]

# A byte order mark, which doc/template.md#data-input skips at the start
# of a JSON text: the whole document, and each line of NDJSON.
MARK = "\N{BYTE ORDER MARK}"

# A UTF-16 byte order mark, little-endian or big, as reading the records
# as UTF-8 with ``surrogateescape`` leaves it: two bytes that are not UTF-8.
UTF16_MARKS = tuple(
    mark.decode("utf-8", "surrogateescape")
    for mark in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
)


def read_records(
    source: Path | str | TextIO, declared: Records | None, name: str | None = None
) -> tuple[Record, ...]:
    """Return the records a JSON or NDJSON source holds.

    doc/template.md#data-input: the JSON is UTF-8, and a byte that is
    not part of a valid sequence reads as U+FFFD, one per byte, as the
    reference reads it.  ``surrogateescape`` keeps each such byte apart
    as a surrogate, and the surrogates are replaced before anything
    parses the text.  A stream is decoded by whoever opened it, which
    for standard input is the command line, with the same two settings.

    Records in UTF-16 are refused in words that say so.  Read as UTF-8
    they open with two bytes that are not UTF-8, and the JSON parser
    would say only that it expected a value.  Windows PowerShell 5.1
    writes UTF-16 for a ``>`` and for ``Out-File``, so this is the likely
    way to have such a file.  Byte order marks are :func:`json_rows`'s.

    Args:
        source: A path to read, or an open stream such as standard input.
        declared: The template's `records` node, where it has one.
        name: What to call the source in a diagnostic;
            the path otherwise.

    Raises:
        BuildError: The source is not JSON, or a member will not coerce.

    """
    if isinstance(source, Path | str):
        where = str(source) if name is None else name
        path = Path(source)
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        remedy = "save it as UTF-8"
    else:
        where = "standard input" if name is None else name
        text = source.read()
        remedy = "send it as UTF-8"
    # Past any UTF-8 marks: Windows PowerShell 5.1 puts one in front of
    # what it pipes when the console's input encoding is UTF-8 with one,
    # whatever ``$OutputEncoding`` encodes the records in.
    if text.lstrip(MARK).startswith(UTF16_MARKS):
        raise BuildError(
            f"not JSON: it is UTF-16; {remedy}", Location(file=where, line=1)
        )
    return records_from(without_surrogates(text), declared, where)


def records_from(
    text: str, declared: Records | None, where: str | None = None
) -> tuple[Record, ...]:
    """Return the records a JSON or NDJSON document holds.

    Args:
        text: The document.
        declared: The template's `records` node, where it has one.
        where: What to call the document in a diagnostic.

    Raises:
        BuildError: The text is not JSON, or a member will not coerce.

    """
    return records_in(rows_in(text, where), declared, where)


def rows_in(text: str, where: str | None) -> list[Any]:
    """Return the JSON objects a document holds, in order.

    Which of the two shapes it is decided by the first character that
    is not a space: an array document opens with ``[`` and NDJSON with
    the first record's ``{``.  Nothing else distinguishes them, and
    guessing by extension would read a ``.json`` file of NDJSON wrongly.

    Args:
        text: The document.
        where: What to call it in a diagnostic.

    Raises:
        BuildError: The text is not JSON, or does not hold objects.

    """
    rows = json_rows(text, where)
    if "\\u" in text:
        # Only an escape spells a lone surrogate.
        return [json_without_surrogates(row) for row in rows]
    return rows


def json_rows(text: str, where: str | None) -> list[Any]:
    """Return the JSON values a document holds, in order, as parsed.

    Byte order marks at the start of a JSON text are skipped, as many as
    there are, as RFC 8259 allows and the reference does not.  Windows
    PowerShell 5.1 can pipe two: one when the console's input encoding
    is UTF-8, whatever ``$OutputEncoding`` says, and a second when
    ``$OutputEncoding`` is UTF-8 with one of its own.  Each NDJSON line
    is a JSON text, so marks at the start of a line are skipped too,
    and files that each open with one can be joined.  Elsewhere outside
    a string a mark is a stray character, and the parser refuses it;
    inside one it is a character like any other.

    Args:
        text: The document.
        where: What to call it in a diagnostic.

    Raises:
        BuildError: The text is not JSON, or is not an array document.

    """
    stripped = text.lstrip(MARK).lstrip()
    if not stripped:
        return []
    if stripped[0] == "[":
        try:
            document = json.loads(stripped)
        except ValueError as refused:
            raise BuildError(f"not JSON: {refused}", Location(file=where)) from None
        if not isinstance(document, list):
            raise BuildError(
                f"want an array of records, got {type(document).__name__}",
                Location(file=where),
            )
        return document
    rows: list[Any] = []
    for index, written in enumerate(text.split("\n")):
        line = written.lstrip(MARK)
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as refused:
            raise BuildError(
                f"not JSON: {refused}", Location(file=where, line=index + 1)
            ) from None
    return rows


def records_in(
    rows: list[Any], declared: Records | None, where: str | None = None
) -> tuple[Record, ...]:
    """Return one record per row, with declared members coerced.

    Args:
        rows: What the JSON held, one entry per record.
        declared: The template's `records` node, where it has one.
        where: What to call the source in a diagnostic.

    Raises:
        BuildError: A row is not an object, or a member will not coerce.

    """
    members = () if declared is None else declared.members
    found = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BuildError(
                f"want an object, got {type(row).__name__}",
                Location(file=where, record=index),
            )
        found.append(record_of(row, members, index, where))
    return tuple(found)


def record_of(
    row: dict[str, Any],
    members: tuple[Member, ...],
    index: int,
    where: str | None,
) -> Record:
    """Return one record, its declared members coerced.

    The members keep the order the data gave them, and a declared member
    the data does not carry is simply absent: an expression that reads
    it fails with the name, which is a better diagnostic than a column
    of empty cells.

    Args:
        row: The JSON object.
        members: What the template declares.
        index: Which record this is, from zero.
        where: What to call the source in a diagnostic.

    Raises:
        BuildError: A member will not coerce.

    """
    declared = {member.name: member for member in members}
    fields: dict[str, Any] = {}
    for name, value in row.items():
        member = declared.get(name)
        if member is None:
            fields[name] = freeze(value)
            continue
        try:
            fields[name] = coerce(value, member)
        except BadValue as refused:
            raise BuildError(
                f"member {member.name!r}: {refused}",
                Location(file=where, path=member.path, record=index),
            ) from None
    return Record(fields)


def coerce(value: Any, member: Member) -> Any:
    """Return one JSON value as the type its `member` declares.

    Args:
        value: What the JSON held.
        member: The declaration.

    Raises:
        BadValue: The value is not of that type, or is a ``null``
            in a member that is not `nullable`.

    """
    if value is None:
        if member.nullable:
            return None
        raise BadValue("null in a member that is not nullable")
    if member.kind in ("object", "list"):
        wanted = dict if member.kind == "object" else list
        if not isinstance(value, wanted):
            article = "an object" if member.kind == "object" else "an array"
            raise BadValue(f"want {article}, got {type(value).__name__}")
        return freeze(value)
    return parse_text(member.kind, as_text(value), member.format)


def as_text(value: Any) -> str:
    """Return a JSON scalar as the text that spells it.

    A string is itself.  Everything else is written the way JSON writes
    it, so that one reader per declared type covers both the data that
    quotes its numbers and the data that does not.

    Args:
        value: A JSON scalar.

    Raises:
        BadValue: The value is an object or an array, which no scalar
            type takes.

    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return go_shortest(value)
    raise BadValue(f"want a value, got {type(value).__name__}")
