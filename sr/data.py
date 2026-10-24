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

The same coercion turns the elements of
a [subreport](doc/layout.md#names-and-values)'s data into its records,
and those can arrive typed rather than as JSON gave them, since the data
is an expression: a decimal the host computed, a time, a list built by
a comprehension.  A value is written as text and read by the declared type,
as JSON's scalars are: a decimal as its digits and a time as RFC 3339,
without fractions of a second.  Three exceptions keep a value as it is:
a time in a `date` or `datetime` member, which a `date` takes at the start
of its day; a record or a dict in an `object` member; and a list or a tuple
in a `list` member.

A host program can skip JSON and hand the records over as Python values:
mappings, or objects whose fields have names, such as the rows a database
query fetches.  Those values are typed too, so they are coerced the way
a subreport's elements are, once the host's own types have become this
engine's: a ``decimal.Decimal`` an exact decimal, a ``datetime`` a time,
and so on, as :func:`host_value` lists them.  An undeclared member keeps
its type, which JSON could not have given it: a decimal is still exact.

"""

from __future__ import annotations

import codecs
import dataclasses
import decimal
import json
import numbers
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO
from uuid import UUID

from sr.errors import BadValue, BuildError, Location
from sr.expr.golayout import RFC3339
from sr.expr.values import (
    Decimal,
    Duration,
    FrozenDict,
    FrozenList,
    Namespace,
    Record,
    Set,
    Time,
    go_shortest,
    json_without_surrogates,
    type_name,
    without_surrogates,
)
from sr.template.model import Member, Records, freeze, parse_text

__all__ = [
    "coerce",
    "host_value",
    "read_records",
    "records_from",
    "records_given",
    "records_in",
    "typed",
]

# A byte order mark, which doc/template.md#data-input skips at the start
# of a JSON text: the whole document, and each line of NDJSON.
MARK = "\N{BYTE ORDER MARK}"

# A UTF-16 byte order mark, little-endian or big, as reading the records
# as UTF-8 with ``surrogateescape`` leaves it: two bytes that are not UTF-8.
UTF16_MARKS = tuple(
    mark.decode("utf-8", "surrogateescape")
    for mark in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
)

# What a host's record holds as it was given, by exact type: JSON's numbers
# and booleans, and this engine's own values.  Text is not one, as it may
# hold a surrogate, and a subclass is not one, as ``type`` would name it.
SCALARS = (bool, int, float, Decimal, Time, Duration)

# How deep a host's value may nest, records and lists together:
# deeper than any report's data are nested, and shallow enough that
# whatever reads the value later stays well inside Python's limit
# on recursion.
DEEPEST = 100


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


def records_given(
    rows: Iterable[Any], declared: Records | None, where: str | None = None
) -> tuple[Record, ...]:
    """Return one record per row a host program gave, its members coerced.

    A row is a value :func:`fields_of` reads fields from.
    Its values become this engine's by :func:`host_value`,
    and a declared member's is then coerced by :func:`typed`,
    as a subreport's element is: the host computed it,
    and no JSON spelled it.

    Args:
        rows: The records, in order: any iterable, read once.
        declared: The template's `records` node, where it has one.
        where: What to call the rows in a diagnostic.

    Raises:
        BuildError: The rows are not a sequence, a row has no named
            fields or will not give them, or a value has no counterpart
            here or will not coerce.

    """
    unordered = ", which has no order" if isinstance(rows, AbstractSet) else ""
    refused = BuildError(
        f"want a sequence of records, got {host_type(rows)}{unordered}",
        Location(file=where),
    )
    # Text, bytes, and a mapping iterate, but over characters, numbers,
    # and names, and a set in no order that a report could keep.
    if isinstance(rows, str | bytes | bytearray | memoryview | Mapping):
        raise refused
    if isinstance(rows, AbstractSet):
        raise refused
    try:
        given = iter(rows)
    except TypeError:
        raise refused from None
    members = {} if declared is None else {one.name: one for one in declared.members}
    found = []
    for index, row in enumerate(given):
        try:
            fields = fields_of(row)
        except BadValue as refusal:
            raise BuildError(
                str(refusal), Location(file=where, record=index)
            ) from refusal.__cause__
        if fields is None:
            got = host_type(row) + unread(row)
            raise BuildError(
                f"want a mapping or an object, got {got}",
                Location(file=where, record=index),
            )
        found.append(record_given(fields, members, index, where))
    return tuple(found)


def record_given(
    fields: Mapping[Any, Any],
    members: Mapping[str, Member],
    index: int,
    where: str | None,
) -> Record:
    """Return one record of a host's, its declared members coerced.

    The members keep the order the row gave them, and a declared member
    the row does not carry is absent, as :func:`record_of` leaves it.

    Args:
        fields: The row's fields, by name.
        members: What the template declares, by name.
        index: Which record this is, from zero.
        where: What to call the rows in a diagnostic.

    Raises:
        BuildError: A name is not a string, or a value has no counterpart
            here or will not coerce.

    """
    found: dict[str, Any] = {}
    for key, value in fields.items():
        try:
            name = name_of(key)
        except BadValue as refusal:
            raise BuildError(
                str(refusal),
                Location(file=where, record=index),
            ) from None
        member = members.get(name)
        try:
            held = host_value(value)
            found[name] = held if member is None else typed(held, member)
        except BadValue as refusal:
            raise BuildError(
                f"member {name!r}: {refusal}",
                Location(
                    file=where,
                    path=None if member is None else member.path,
                    record=index,
                ),
            ) from refusal.__cause__
    return Record(found)


def fields_of(row: Any) -> dict[Any, Any] | None:
    """Return a host value's fields by name, or ``None`` if it has none.

    Five shapes have them, tried in this order:

    * a mapping, such as a ``dict`` or SQLAlchemy's ``RowMapping``;
    * a named tuple, by its ``_asdict``, which SQLAlchemy's ``Row`` has;
    * anything with ``keys`` and item access, which is what ``dict``
      itself takes, such as ``sqlite3.Row``;
    * a dataclass instance, by its fields; and
    * any other object, by its attributes.

    The last two read what :func:`attributes` gives: the public
    attributes the object holds, so an ORM entity loads nothing.
    An object that holds no public attribute has no fields,
    and neither has a function, a method, a class, a module,
    or an exception: each is more likely a value the program
    forgot to call or to load than a record with nothing in it.
    A plain tuple has no names and a list has none either,
    so neither is a row.

    Args:
        row: The value.

    Raises:
        BadValue: Reading the fields raised, as the host's own code can:
            a driver's or an ORM's runs here.

    """
    try:
        if isinstance(row, type):
            return None
        if isinstance(row, Mapping):
            return dict(row)
        if callable(getattr(row, "_asdict", None)):
            return dict(row._asdict())
        keys = getattr(row, "keys", None)
        if callable(keys) and hasattr(row, "__getitem__"):
            return {key: row[key] for key in keys()}
        held = attributes(row) if readable(row) else None
        if held is None:
            return None
        if dataclasses.is_dataclass(row):
            names = [one.name for one in dataclasses.fields(row)]
            held = {name: held[name] for name in names if name in held}
        return held or None
    except Exception as raised:
        raise BadValue(
            f"reading its fields raised {host_type(raised)}: {raised}"
        ) from raised


def readable(value: Any) -> bool:
    """Return whether a value's attributes, if it has any, are its fields.

    This engine's own containers keep their contents in slots,
    and are not records either: its list stays a list, and its set
    and its module are refused, as the host's own are.

    Args:
        value: The value.

    """
    if isinstance(value, list | tuple | FrozenList | Set | Namespace):
        return False
    if isinstance(value, type | BaseException | ModuleType):
        return False
    return not callable(value)


def attributes(value: Any) -> dict[str, Any] | None:
    """Return the public attributes an object holds, by name.

    They are read where the object keeps them, its slots and its
    ``__dict__``, rather than looked up by name, so no property runs
    and an ORM entity loads nothing: a relationship that is not loaded
    is left out, and so is every column of an entity a commit expired.
    A name that starts with an underscore is where an object keeps its
    bookkeeping, such as SQLAlchemy's ``_sa_instance_state``.

    Args:
        value: The object.

    Returns:
        The attributes, or ``None`` for a value with nowhere to keep
        a public one, such as ``bytes`` or a ``Fraction``.

    """
    found: dict[str, Any] = {}
    kept = False
    for kind in reversed(type(value).__mro__):
        slots = vars(kind).get("__slots__") or ()
        for name in (slots,) if isinstance(slots, str) else slots:
            if name.startswith("_"):
                continue
            kept = True
            try:
                found[name] = object.__getattribute__(value, name)
            except AttributeError:
                continue  # A slot that holds nothing.
    try:
        held = object.__getattribute__(value, "__dict__")
    except AttributeError:
        return found if kept else None
    found.update(
        (name, item) for name, item in held.items() if not name.startswith("_")
    )
    return found


def unread(value: Any) -> str:
    """Return why a value has no fields, as a clause to end a refusal with.

    The clause is empty where the type says enough.

    Args:
        value: The value.

    """
    if isinstance(value, tuple):
        return ", whose fields have no names"
    if readable(value) and attributes(value) == {}:
        return ", which holds no public attributes"
    return ""


def host_value(value: Any, within: frozenset[int] = frozenset()) -> Any:
    """Return a value a host program gave as this engine holds it.

    JSON's scalars are this engine's already, and so are its decimal,
    time, and duration.  Six of the host's own types become one of those:

    * a ``decimal.Decimal``, an exact decimal, where it is finite;
    * a ``datetime``, a time.  One with no zone is read in UTC, as text
      with no offset is, so that the machine's zone decides nothing.
      One whose instant in UTC is outside the years 1 to 9999 is refused,
      as it is in JSON;
    * a ``date``, the start of that day in UTC, which is the time
      a `date` member reads ``"2005-05-24"`` as;
    * a ``timedelta``, a duration;
    * a ``UUID``, its text, which is its one spelling; and
    * an ``Enum`` member, its value, converted in turn.

    A subclass of ``str``, ``int``, or ``float`` becomes the type itself,
    and so does an integer or a binary float of another type, such as
    numpy's ``int64`` and ``float32``: ``type`` then names what a JSON
    value of that kind would have been.  A ``Fraction`` is refused,
    since no float holds one exactly.

    A value with fields (:func:`fields_of`) becomes a record,
    and a list or a tuple becomes a list, their contents converted
    in turn: an object a row refers to is then a record, and a list
    of them is what a subreport runs over.  They nest :data:`DEEPEST`
    deep at most.  Anything else is refused rather than guessed at:
    bytes, a time of day, a set.

    Args:
        value: What the host gave.
        within: The containers the value sits in, by identity,
            so that one that contains itself is refused
            rather than followed for ever.

    Raises:
        BadValue: The value has no counterpart here, a name in it
            is not a string, it contains itself or nests too deep,
            or reading its fields raised.

    """
    if isinstance(value, Enum):
        return host_value(value.value, within)
    if value is None or type(value) in SCALARS:
        return value
    if isinstance(value, str):
        return without_surrogates(str.__str__(value))
    if isinstance(value, numbers.Integral):
        return int(value)
    # Every float is real and none rational; a `Fraction` is both.
    real = isinstance(value, numbers.Real)
    if real and not isinstance(value, numbers.Rational):
        return float(value)
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise BadValue(f"not a finite decimal: {value}")
        return Decimal(value)
    if isinstance(value, datetime):
        zoned = value if value.utcoffset() is not None else value.replace(tzinfo=UTC)
        try:
            return Time.from_datetime(zoned)
        except OverflowError:
            raise BadValue(
                f"out of range: {value.isoformat()}"
                " is outside the years 1 to 9999 in UTC"
            ) from None
    if isinstance(value, date):
        midnight = datetime(value.year, value.month, value.day, tzinfo=UTC)
        return Time.from_datetime(midnight)
    if isinstance(value, timedelta):
        return Duration(value // timedelta(microseconds=1) * 1000)
    if isinstance(value, UUID):
        return str(value)
    if id(value) in within:
        raise BadValue(f"{host_type(value)} contains itself")
    if len(within) >= DEEPEST:
        raise BadValue(f"nested more than {DEEPEST} deep")
    inside = within | {id(value)}
    fields = fields_of(value)
    if fields is not None:
        return Record(named(fields, inside))
    if isinstance(value, list | tuple | FrozenList):
        return FrozenList(host_value(item, inside) for item in value)
    got = host_type(value) + unread(value)
    raise BadValue(f"want a value a record can hold, got {got}")


def named(fields: Mapping[Any, Any], within: frozenset[int]) -> dict[str, Any]:
    """Return the fields of a value a row holds as a record's, converted.

    A refusal names no field here: :func:`record_given` names the row's
    member that holds the value, which is the name a diagnostic needs.

    Args:
        fields: The fields, by name.
        within: The containers they sit in, by identity.

    Raises:
        BadValue: A name is not a string, or a value is refused,
            as :func:`host_value` refuses it.

    """
    found: dict[str, Any] = {}
    for key, value in fields.items():
        found[name_of(key)] = host_value(value, within)
    return found


def name_of(key: Any) -> str:
    """Return the name a host gave a field, as a record's name.

    Args:
        key: The name, as the host gave it.

    Raises:
        BadValue: The name is not a string.

    """
    if not isinstance(key, str):
        kind = host_type(key)
        raise BadValue(f"a name is {kind}, and a record's names are strings")
    return without_surrogates(str.__str__(key))


def host_type(value: Any) -> str:
    """Return the name of a host value's type, as Python spells it.

    A builtin type is named by itself and any other with its module,
    since ``time`` alone could be either of two.

    Args:
        value: The value.

    """
    kind = type(value)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


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
    if member.kind == "object":
        if isinstance(value, Record):
            return value
        if not isinstance(value, dict | FrozenDict):
            raise BadValue(f"want an object, got {type_name(value)}")
        return freeze(dict(value))
    if member.kind == "list":
        if isinstance(value, FrozenList):
            return value
        if not isinstance(value, list | tuple):
            raise BadValue(f"want an array, got {type_name(value)}")
        return FrozenList(freeze(item) for item in value)
    if isinstance(value, Time) and member.kind in ("date", "datetime"):
        return value if member.kind == "datetime" else start_of_day(value)
    return parse_text(member.kind, as_text(value), member.format)


def typed(value: Any, member: Member) -> Any:
    """Return a value the host computed as the type its `member` declares.

    The input file's coercion, and two refusals more: a `string`
    member takes no float, since a float the host computed has
    no one spelling as text, and no member takes a duration,
    since no member type is one.  A subreport's elements are
    such values, and so are the rows a host program hands
    :func:`records_given`.

    Args:
        value: The field's value.
        member: Its declaration.

    Raises:
        BadValue: The value is not of that type.

    """
    if member.kind == "string" and isinstance(value, float):
        raise BadValue(f"want text, got {type_name(value)}")
    if isinstance(value, Duration):
        raise BadValue("no member type holds a duration; leave it undeclared")
    return coerce(value, member)


def start_of_day(moment: Time) -> Time:
    """Return the first instant of a time's day, where it is read.

    Args:
        moment: The time.

    """
    midnight = moment.moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return Time.from_datetime(midnight, 0)


def as_text(value: Any) -> str:
    """Return a scalar as the text that spells it.

    A string is itself.  Everything else is written the way JSON writes
    it, so that one reader per declared type covers both the data that
    quotes its numbers and the data that does not.  A decimal is written
    as its digits and a time as RFC 3339, without fractions of a second:
    those reach here only from a subreport's data.

    Args:
        value: A JSON scalar, or a decimal or a time.

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
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Time):
        return value.format(RFC3339)
    raise BadValue(f"want a value, got {type_name(value)}")
