"""What is in scope, and how a name on a value is reached.

Three things live here, and they are one subject: the environment
a compiled expression runs in.

* **The globals.**  doc/expressions.md#modules-and-builtins lists
  the Starlark builtins the dialect keeps, the `math` and `time` modules,
  and the four names the engine predeclares -- `format`, `strftime`,
  `decimal` and `quantize`.  Nothing else is reachable: a compiled
  expression is given this table and an empty ``__builtins__``, so
  ``open`` and ``__import__`` are not names that fail, they are names
  that were never there.

* **The methods.**  A whitelist per type rather than Python's ``getattr``,
  which is what keeps ``"".join.__globals__`` out of the language and
  what lets the string type have ``codepoints`` and not ``encode``.
  The mutating methods of a frozen value are in the table and raise,
  per doc/expressions.md#methods.

* **The resolvers.**  ``getattr_``, ``getitem_`` and ``getslice_`` are
  what the compiler rewrites ``a.b``, ``a[i]`` and ``a[i:j]`` into,
  and ``mod_`` is what it rewrites ``%`` into, since on a string
  that operator is Starlark's formatter rather than Python's.

The four resolvers are the only functions in the engine on the critical
path of every evaluation, which is why they dispatch on ``type()``
through a dict rather than through a chain of ``isinstance``.

"""

from __future__ import annotations

import math as pymath
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final

from sr.errors import ExpressionError
from sr.expr import golayout
from sr.expr.fmt import format_value, interpolate
from sr.expr.values import (
    NANOSECONDS,
    Decimal,
    Duration,
    FrozenDict,
    FrozenList,
    Namespace,
    Record,
    Set,
    Time,
    find_location,
    hashable,
    quantize,
    starlark_repr,
    starlark_str,
    truthy,
    type_name,
    valid_timezone,
)

__all__ = [
    "GLOBALS",
    "getattr_",
    "getitem_",
    "getslice_",
    "mod_",
]


def frozen(kind: str, method: str) -> Callable[..., Any]:
    """Return a method that refuses because its receiver is frozen.

    doc/expressions.md keeps the mutating methods on frozen values
    and has them fail, so that a template author who tries one
    is told the value cannot be changed rather than that the method
    does not exist.

    Args:
        kind: What the receiver is, for the diagnostic.
        method: The method's name.

    """

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise ExpressionError(f"cannot {method} a frozen {kind}")

    return refuse


# ---------------------------------------------------------------- strings


def string_codepoints(text: str) -> tuple[str, ...]:
    """Return the characters of a string, one per codepoint.

    Args:
        text: The string to iterate.

    """
    return tuple(text)


def string_codepoint_ords(text: str) -> tuple[int, ...]:
    """Return the codepoints of a string as numbers.

    Args:
        text: The string to iterate.

    """
    return tuple(ord(character) for character in text)


def string_splitlines(text: str, keepends: bool = False) -> list[str]:
    """Return a string split after each newline.

    Newline and nothing else.  Python splits on a dozen other characters --
    vertical tab, form feed, the Unicode separators -- and a report whose
    lines depended on that would not be a report the reference lays out
    the same way.

    Args:
        text: The string to split.
        keepends: Whether each line keeps its newline.

    """
    lines: list[str] = []
    start = 0
    for index, character in enumerate(text):
        if character == "\n":
            lines.append(text[start : index + 1] if keepends else text[start:index])
            start = index + 1
    if start < len(text):
        lines.append(text[start:])
    return lines


def string_join(separator: str, items: Iterable[Any]) -> str:
    """Return the items of a sequence joined by a separator.

    Args:
        separator: What to put between them.
        items: The strings to join.

    Raises:
        ExpressionError: One of the items is not a string.

    """
    parts = list(items)
    for part in parts:
        if not isinstance(part, str):
            raise ExpressionError(f"join() wants strings, got {type_name(part)}")
    return separator.join(parts)


def string_format(template: str, *args: Any, **kwargs: Any) -> str:
    """Return a string with its ``{}`` placeholders replaced.

    The braces form, which takes ``{}``, ``{1}``, ``{name}`` and
    the ``!r`` and ``!s`` conversions -- and rejects a format spec
    after a colon, because doc/expressions.md#formatting says
    the engine's own formatter is where width and precision live.

    Args:
        template: The string holding the placeholders.
        *args: Values for the positional placeholders.
        **kwargs: Values for the named ones.

    Raises:
        ExpressionError: A placeholder is malformed, names a format spec,
            mixes automatic numbering with manual indices, or has no value
            to fill it.

    """
    written: list[str] = []
    automatic = 0
    manual = False
    index = 0
    while index < len(template):
        character = template[index]
        if character in "{}" and template[index : index + 2] == character * 2:
            written.append(character)
            index += 2
            continue
        if character != "{":
            written.append(character)
            index += 1
            continue
        close = template.find("}", index)
        if close < 0:
            raise ExpressionError(f"unmatched '{{' in format {template!r}")
        field = template[index + 1 : close]
        index = close + 1
        if ":" in field:
            raise ExpressionError(
                f"format spec is not supported in .format(): {field!r}; "
                "use the format() builtin"
            )
        name, _, conversion = field.partition("!")
        if conversion not in ("", "r", "s"):
            raise ExpressionError(f"unknown conversion !{conversion}")
        if not name:
            if manual:
                raise ExpressionError(
                    f"format {template!r} cannot switch from a numbered "
                    "placeholder back to an automatic one"
                )
            value = positional(args, automatic, template)
            automatic += 1
        elif name.isascii() and name.isdigit():
            if automatic:
                raise ExpressionError(
                    f"format {template!r} cannot switch from an automatic "
                    "placeholder to a numbered one"
                )
            manual = True
            value = positional(args, int(name), template)
        elif name in kwargs:
            value = kwargs[name]
        else:
            raise ExpressionError(f"format {template!r} wants a keyword {name!r}")
        show = starlark_repr if conversion == "r" else starlark_str
        written.append(show(value))
    return "".join(written)


def positional(args: tuple[Any, ...], index: int, template: str) -> Any:
    """Return one positional value for ``.format``, or say it is missing.

    Args:
        args: The values given.
        index: Which one the placeholder asked for.
        template: The format string, for the diagnostic.

    """
    if index >= len(args):
        raise ExpressionError(
            f"format {template!r} wants at least {index + 1} values, {len(args)} given"
        )
    return args[index]


# The string methods doc/expressions.md#methods lists, and no others.
# `elems` and `elem_ords` are deliberately absent: they are the byte pair,
# and this dialect's string is a sequence of codepoints.
STRING_METHODS: Final[dict[str, Callable[..., Any]]] = {
    "capitalize": str.capitalize,
    "codepoint_ords": string_codepoint_ords,
    "codepoints": string_codepoints,
    "count": str.count,
    "endswith": str.endswith,
    "find": str.find,
    "format": string_format,
    "index": str.index,
    "isalnum": str.isalnum,
    "isalpha": str.isalpha,
    "isdigit": str.isdigit,
    "islower": str.islower,
    "isspace": str.isspace,
    "istitle": str.istitle,
    "isupper": str.isupper,
    "join": string_join,
    "lower": str.lower,
    "lstrip": str.lstrip,
    "partition": lambda text, sep: tuple(text.partition(sep)),
    "removeprefix": str.removeprefix,
    "removesuffix": str.removesuffix,
    "replace": lambda text, old, new, count=-1: text.replace(old, new, count),
    "rfind": str.rfind,
    "rindex": str.rindex,
    "rpartition": lambda text, sep: tuple(text.rpartition(sep)),
    "rsplit": lambda text, sep=None, maxsplit=-1: text.rsplit(sep, maxsplit),
    "rstrip": str.rstrip,
    "split": lambda text, sep=None, maxsplit=-1: text.split(sep, maxsplit),
    "splitlines": string_splitlines,
    "startswith": str.startswith,
    "strip": str.strip,
    "title": str.title,
    "upper": str.upper,
}

# The two names that came off the string type when it stopped counting
# bytes, kept here so the diagnostic can say where they went.
DROPPED_STRING_METHODS: Final = {
    "elems": "codepoints",
    "elem_ords": "codepoint_ords",
}


# ------------------------------------------------------- lists and dicts

LIST_QUERIES: Final = ("index",)
LIST_MUTATORS: Final = ("append", "clear", "extend", "insert", "pop", "remove")
DICT_MUTATORS: Final = ("clear", "pop", "popitem", "setdefault", "update")


def dict_get(entries: Mapping[Any, Any], key: Any, default: Any = None) -> Any:
    """Return one value by key, or a default.

    Args:
        entries: The receiver.
        key: What to look up.
        default: What to answer with when it is not there.

    """
    try:
        return entries.get(key, default)
    except TypeError:
        return default


def dict_items(entries: Mapping[Any, Any]) -> list[tuple[Any, Any]]:
    """Return a mapping's pairs as a list, which is what the language has."""
    return [(key, value) for key, value in entries.items()]


def dict_keys(entries: Mapping[Any, Any]) -> list[Any]:
    """Return a mapping's keys as a list rather than as a view."""
    return list(entries.keys())


def dict_values(entries: Mapping[Any, Any]) -> list[Any]:
    """Return a mapping's values as a list rather than as a view."""
    return list(entries.values())


# The query half of the dict method table.  Each is a plain function of
# the receiver, bound with `partial` when it is asked for, so that reading
# one attribute does not build the other three.
DICT_QUERIES: Final[dict[str, Callable[..., Any]]] = {
    "get": dict_get,
    "items": dict_items,
    "keys": dict_keys,
    "values": dict_values,
}


def list_index(items: tuple[Any, ...], value: Any, *bounds: int) -> int:
    """Return where a value first appears in a frozen list.

    Args:
        items: The elements to search.
        value: What to look for.
        *bounds: An optional start and end.

    """
    return list(items).index(value, *bounds)


# ------------------------------------------------------------------ sets


def set_difference(members: Set, other: Any) -> Set:
    """Return the members not in another set."""
    return members - as_set(other, "difference")


def set_intersection(members: Set, other: Any) -> Set:
    """Return the members also in another set."""
    return members & as_set(other, "intersection")


def set_issubset(members: Set, other: Any) -> bool:
    """Report whether every member is also in another set."""
    return all(item in as_set(other, "issubset") for item in members)


def set_issuperset(members: Set, other: Any) -> bool:
    """Report whether another set's every member is also in this one."""
    return all(item in members for item in as_set(other, "issuperset"))


def set_union(members: Set, other: Any) -> Set:
    """Return the members of both, in this set's order first."""
    return members | as_set(other, "union")


def set_add(members: Set, item: Any) -> None:
    """Add one member."""
    members.members[hashable(item)] = None


def set_clear(members: Set) -> None:
    """Remove every member."""
    members.members.clear()


def set_discard(members: Set, item: Any) -> None:
    """Remove one member, or do nothing where it is not one."""
    members.members.pop(item, None)


def set_pop(members: Set) -> Any:
    """Remove and return the first member.

    Raises:
        ExpressionError: The set is empty.

    """
    if not members.members:
        raise ExpressionError("pop from an empty set")
    first = next(iter(members.members))
    del members.members[first]
    return first


def set_remove(members: Set, item: Any) -> None:
    """Remove one member, which must be one.

    Raises:
        ExpressionError: The value is not a member.

    """
    if item not in members.members:
        raise ExpressionError(f"not a member: {starlark_repr(item)}")
    del members.members[item]


def set_symmetric_difference(members: Set, other: Any) -> None:
    """Keep the members of exactly one of two sets."""
    theirs = as_set(other, "symmetric_difference")
    for item in list(theirs):
        if item in members.members:
            del members.members[item]
        else:
            members.members[item] = None


# The set method table, split the way doc/expressions.md splits it: the
# queries answer on any set, and the mutators refuse on a frozen one.
SET_QUERIES: Final[dict[str, Callable[..., Any]]] = {
    "difference": set_difference,
    "intersection": set_intersection,
    "issubset": set_issubset,
    "issuperset": set_issuperset,
    "union": set_union,
}
SET_MUTATORS: Final[dict[str, Callable[..., Any]]] = {
    "add": set_add,
    "clear": set_clear,
    "discard": set_discard,
    "pop": set_pop,
    "remove": set_remove,
    "symmetric_difference": set_symmetric_difference,
}


def as_set(value: Any, method: str) -> Set:
    """Return an operand of a set method as a set.

    Args:
        value: The operand, which may be any sequence.
        method: The method being called, for the diagnostic.

    """
    if isinstance(value, Set):
        return value
    try:
        return Set(value)
    except TypeError:
        raise ExpressionError(
            f"{method}() wants a set or a sequence, got {type_name(value)}"
        ) from None


# ------------------------------------------------------- the value types


# What a time and a duration answer to.  Each entry is a function of the
# receiver rather than a value, so that reading `.year` computes the year
# and not the other eight members beside it -- which cost a datetime each.
TIME_MEMBERS: Final[dict[str, Callable[[Time], Any]]] = {
    "year": lambda moment: moment.year,
    "month": lambda moment: moment.month,
    "day": lambda moment: moment.day,
    "hour": lambda moment: moment.hour,
    "minute": lambda moment: moment.minute,
    "second": lambda moment: moment.second,
    "nanosecond": lambda moment: moment.nanosecond,
    "unix": lambda moment: moment.unix,
    "unix_nano": lambda moment: moment.unix_nano,
    "in_location": lambda moment: moment.in_location,
    "format": lambda moment: moment.format,
}

DURATION_MEMBERS: Final[dict[str, Callable[[Duration], Any]]] = {
    "hours": lambda length: length.hours,
    "minutes": lambda length: length.minutes,
    "seconds": lambda length: length.seconds,
    "milliseconds": lambda length: length.milliseconds,
    "microseconds": lambda length: length.microseconds,
    "nanoseconds": lambda length: length.nanoseconds,
}


# ------------------------------------------------------------- resolvers


def attribute_of_string(text: str, name: str) -> Any:
    """Return a method of a string.

    Args:
        text: The receiver.
        name: The method's name.

    """
    method = STRING_METHODS.get(name)
    if method is not None:
        return partial(method, text)
    replacement = DROPPED_STRING_METHODS.get(name)
    if replacement is not None:
        raise ExpressionError(
            f"a string has no {name}(): it counts codepoints rather than "
            f"bytes, so use {replacement}()"
        )
    raise ExpressionError(f"string has no attribute {name!r}")


def attribute_of_list(items: list[Any], name: str) -> Any:
    """Return a method of a list that may be changed.

    Args:
        items: The receiver.
        name: The method's name.

    """
    if name in LIST_QUERIES or name in LIST_MUTATORS:
        return getattr(items, name)
    raise ExpressionError(f"list has no attribute {name!r}")


def attribute_of_frozen_list(items: FrozenList, name: str) -> Any:
    """Return a method of a frozen list.

    Args:
        items: The receiver.
        name: The method's name.

    """
    if name == "index":
        return partial(list_index, items.items)
    if name in LIST_MUTATORS:
        return frozen("list", name)
    raise ExpressionError(f"list has no attribute {name!r}")


def attribute_of_dict(entries: dict[Any, Any], name: str) -> Any:
    """Return a method of a dict that may be changed.

    Args:
        entries: The receiver.
        name: The method's name.

    """
    query = DICT_QUERIES.get(name)
    if query is not None:
        return partial(query, entries)
    if name in DICT_MUTATORS:
        return getattr(entries, name)
    raise ExpressionError(f"dict has no attribute {name!r}")


def attribute_of_frozen_dict(entries: FrozenDict, name: str) -> Any:
    """Return a method of a frozen dict.

    Args:
        entries: The receiver.
        name: The method's name.

    """
    query = DICT_QUERIES.get(name)
    if query is not None:
        return partial(query, entries)
    if name in DICT_MUTATORS:
        return frozen("dict", name)
    raise ExpressionError(f"dict has no attribute {name!r}")


def attribute_of_record(record: Record, name: str) -> Any:
    """Return a member of a record.

    A record has members and no methods, so that no member is shadowed
    by one: a data row with a column called `keys` is a row, not a mistake.

    Args:
        record: The receiver.
        name: The member's name.

    """
    try:
        return record.fields[name]
    except KeyError:
        raise ExpressionError(
            f"the record has no member {name!r}; "
            f"it has {', '.join(sorted(record.fields)) or 'none'}"
        ) from None


def attribute_of_set(members: Set, name: str) -> Any:
    """Return a method of a set.

    Args:
        members: The receiver.
        name: The method's name.

    """
    query = SET_QUERIES.get(name)
    if query is not None:
        return partial(query, members)
    mutator = SET_MUTATORS.get(name)
    if mutator is not None:
        return frozen("set", name) if members.frozen else partial(mutator, members)
    raise ExpressionError(f"set has no attribute {name!r}")


def attribute_of_time(moment: Time, name: str) -> Any:
    """Return an attribute or method of a time.

    Args:
        moment: The receiver.
        name: What was asked for.

    """
    read = TIME_MEMBERS.get(name)
    if read is None:
        raise ExpressionError(f"time has no attribute {name!r}")
    return read(moment)


def attribute_of_duration(length: Duration, name: str) -> Any:
    """Return an attribute of a duration.

    Args:
        length: The receiver.
        name: What was asked for.

    """
    read = DURATION_MEMBERS.get(name)
    if read is None:
        raise ExpressionError(f"duration has no attribute {name!r}")
    return read(length)


def attribute_of_namespace(module: Namespace, name: str) -> Any:
    """Return a member of a module.

    Args:
        module: The receiver.
        name: The member's name.

    """
    if name not in module.members:
        raise ExpressionError(f"{module.name} has no member {name!r}")
    return module.members[name]


def attribute_of_bytes(raw: bytes, name: str) -> Any:
    """Return a method of a bytes value, which is only ``elems``.

    Args:
        raw: The receiver.
        name: The method's name.

    """
    if name == "elems":
        return lambda: tuple(raw)
    raise ExpressionError(f"bytes has no attribute {name!r}")


# One entry per type that has attributes at all.  Dispatch is
# on the exact type rather than on `isinstance`, because this runs
# on every attribute access in every expression in every band.
ATTRIBUTES: Final[dict[type, Callable[[Any, str], Any]]] = {
    str: attribute_of_string,
    list: attribute_of_list,
    FrozenList: attribute_of_frozen_list,
    dict: attribute_of_dict,
    FrozenDict: attribute_of_frozen_dict,
    Record: attribute_of_record,
    Set: attribute_of_set,
    Time: attribute_of_time,
    Duration: attribute_of_duration,
    Namespace: attribute_of_namespace,
    bytes: attribute_of_bytes,
}


def getattr_(value: Any, name: str) -> Any:
    """Return the attribute of a value that ``a.b`` asks for.

    What the compiler rewrites every attribute access into.
    Nothing here reaches Python's own ``getattr`` on an arbitrary object,
    which is what keeps ``__class__`` and everything behind it out
    of the language.

    Args:
        value: The receiver.
        name: The attribute's name.

    Raises:
        ExpressionError: The type has no such attribute.

    """
    resolve = ATTRIBUTES.get(type(value))
    if resolve is None:
        raise ExpressionError(f"{type_name(value)} has no attribute {name!r}")
    return resolve(value, name)


def index_of(length: int, index: Any, kind: str) -> int:
    """Return a sequence index as a position from the front.

    Args:
        length: How long the sequence is.
        index: What the expression asked for.
        kind: What is being indexed, for the diagnostic.

    Raises:
        ExpressionError: The index is not an int, or is out of range.

    """
    if isinstance(index, bool) or not isinstance(index, int):
        raise ExpressionError(f"{kind} index wants an int, got {type_name(index)}")
    position = index + length if index < 0 else index
    if not 0 <= position < length:
        raise ExpressionError(f"{kind} index {index} out of range ({length})")
    return position


def getitem_(value: Any, key: Any) -> Any:
    """Return the element of a value that ``a[i]`` asks for.

    Args:
        value: The receiver.
        key: The index or key.

    Raises:
        ExpressionError: The type cannot be indexed,
            the index is out of range, or the key is not there.

    """
    kind = type(value)
    if kind is str:
        return value[index_of(len(value), key, "string")]
    if kind is list or kind is tuple:
        return value[index_of(len(value), key, "list")]
    if kind is FrozenList:
        return value.items[index_of(len(value.items), key, "list")]
    if kind is dict or kind is FrozenDict:
        try:
            return value[key]
        except (KeyError, TypeError):
            raise ExpressionError(f"key not found: {starlark_repr(key)}") from None
    if kind is Record:
        if not isinstance(key, str):
            raise ExpressionError(f"a record is indexed by name, got {type_name(key)}")
        return attribute_of_record(value, key)
    if kind is bytes:
        return value[index_of(len(value), key, "bytes")]
    if kind is range:
        return value[index_of(len(value), key, "range")]
    raise ExpressionError(f"{type_name(value)} is not indexable")


def getslice_(value: Any, lower: Any, upper: Any, step: Any) -> Any:
    """Return the slice of a value that ``a[i:j:k]`` asks for.

    Slicing counts codepoints on a string, which is the whole point
    of doc/expressions.md#strings: ``title[:20]`` cannot cut a character
    in half, because there are no halves of characters to cut.

    Args:
        value: The receiver.
        lower: The start, or ``None``.
        upper: The stop, or ``None``.
        step: The step, or ``None``.

    Raises:
        ExpressionError: The type cannot be sliced, or a bound is not an int.

    """
    for bound in (lower, upper, step):
        if bound is None:
            continue
        if isinstance(bound, bool) or not isinstance(bound, int):
            raise ExpressionError(f"a slice bound wants an int, got {type_name(bound)}")
    if step == 0:
        raise ExpressionError("a slice step cannot be zero")
    cut = slice(lower, upper, step)
    if isinstance(value, FrozenList):
        return list(value.items[cut])
    if isinstance(value, str | list | tuple | bytes | range):
        return value[cut]
    raise ExpressionError(f"{type_name(value)} is not sliceable")


def mod_(left: Any, right: Any) -> Any:
    """Return ``left % right``, which on a string is interpolation.

    Args:
        left: The left operand.
        right: The right operand.

    """
    if isinstance(left, str):
        return interpolate(left, right)
    return left % right


# ------------------------------------------------------------- the names


def sr_len(value: Any) -> int:
    """Return the length of a value that has one.

    Args:
        value: The value to measure.

    Raises:
        ExpressionError: The value has no length.

    """
    if isinstance(
        value, str | list | tuple | dict | bytes | range | FrozenList | FrozenDict
    ) or isinstance(value, Set | Record):
        return len(value)
    raise ExpressionError(f"{type_name(value)} has no length")


def sr_int(value: Any = 0, base: int | None = None) -> int:
    """Return a value as an int, truncating toward zero.

    Args:
        value: The number or string to convert.
        base: The base to read a string in.

    Raises:
        ExpressionError: The value is not convertible.

    """
    try:
        if base is not None:
            if not isinstance(value, str):
                raise ExpressionError("int() with a base wants a string")
            return int(value, base)
        if isinstance(value, int | Decimal | str | bool):
            return int(value)
        # Float is on its own, because it is the one that is not int(value):
        # an infinity has no integer to truncate to.
        if isinstance(value, float):
            if not pymath.isfinite(value):
                raise ExpressionError(f"int() wants a finite float, got {value}")
            return pymath.trunc(value)
    except ValueError:
        raise ExpressionError(f"not an int: {starlark_repr(value)}") from None
    raise ExpressionError(f"int() wants a number or a string, got {type_name(value)}")


def sr_float(value: Any = 0.0) -> float:
    """Return a value as a float, which for a decimal is the lossy conversion.

    Args:
        value: The number or string to convert.

    Raises:
        ExpressionError: The value is not convertible.

    """
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            raise ExpressionError(f"not a float: {value!r}") from None
    raise ExpressionError(f"float() wants a number or a string, got {type_name(value)}")


def sr_abs(value: Any) -> Any:
    """Return a number without its sign.

    Args:
        value: The number.

    Raises:
        ExpressionError: The value is not a number.

    """
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ExpressionError(f"abs() wants a number, got {type_name(value)}")
    return abs(value)


def round_away(value: float) -> float:
    """Return a float rounded to a whole one, halves away from zero.

    The split is exact, which ``math.floor(value + 0.5)`` is not:
    the addition alone reaches 1.0 for 0.49999999999999994.  The
    same reasoning, and the same shape, as ``units.round_half_away``.

    Args:
        value: The number to round.

    Raises:
        ExpressionError: The number is not finite.

    """
    if not pymath.isfinite(value):
        raise ExpressionError(f"round() wants a finite number, got {value}")
    fraction, whole = pymath.modf(value)
    if abs(fraction) >= 0.5:
        whole += pymath.copysign(1.0, value)
    return whole


def sr_round(value: Any) -> int:
    """Return a number rounded to a whole one, halves away from zero.

    The builtin doc/expressions.md#starlark-builtins predeclares.
    One argument, and the same rounding as ``quantize`` and as
    the engine's coordinates -- one rule for the whole system
    rather than three. Python's own ``round`` is not this, it
    rounds halves to even.

    The result is an **int**, where ``math.round`` answers with a float.
    That is the one way the two spellings differ, and it is the reference
    that fixes it: ``math.round`` is the host Starlark's own function and
    changing what it returns would mean forking the module it comes from,
    while this one is predeclared by the engine and free to be the
    convenient shape.

    Args:
        value: The number to round.

    Raises:
        ExpressionError: The value is not a number.

    """
    if isinstance(value, Decimal):
        return int(quantize(value, 0))
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ExpressionError(f"round() wants a number, got {type_name(value)}")
    if isinstance(value, int):
        return value
    return int(round_away(value))


def sr_hash(value: Any) -> int:
    """Return a stable hash of a string or a bytes value.

    Strings and bytes only, as Starlark has it -- and two different
    functions, which is the reference's arrangement, measured: a string
    is hashed by the 31-multiplier recurrence over its **codepoints**,
    signed, and a bytes value by FNV-1a over its bytes, unsigned.

    Both are stable, which Python's ``hash`` is not: its string hash
    is seeded per process, so a report that printed one would differ
    between two runs of the same command.

    Args:
        value: The string or bytes to hash.

    Raises:
        ExpressionError: The value is neither.

    """
    if isinstance(value, str):
        digest = 0
        for character in value:
            digest = (digest * 31 + ord(character)) & 0xFFFFFFFF
        return digest - (1 << 32) if digest >= (1 << 31) else digest
    if isinstance(value, bytes):
        digest = 0x811C9DC5
        for byte in value:
            digest = ((digest ^ byte) * 0x01000193) & 0xFFFFFFFF
        return digest
    raise ExpressionError(f"hash() wants a string or bytes, got {type_name(value)}")


def sr_str(value: Any) -> str:
    """Return a value as text, which for a bytes value is its characters.

    ``str`` and ``%s`` agree on everything but bytes, where ``str``
    decodes and ``%s`` writes the quoted ``b"..."`` form.  Measured
    against the reference rather than reasoned about, because there
    is no reason to it.

    Args:
        value: The value to write.

    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    return starlark_str(value)


def sr_dir(value: Any) -> list[str]:
    """Return the attribute names a value has, in order.

    Args:
        value: The value to inspect.

    """
    kind = type(value)
    if kind is str:
        return sorted(STRING_METHODS)
    if kind is list or kind is FrozenList:
        return sorted([*LIST_QUERIES, *LIST_MUTATORS])
    if kind is dict or kind is FrozenDict:
        return sorted([*DICT_QUERIES, *DICT_MUTATORS])
    if kind is Set:
        return sorted([*SET_QUERIES, *SET_MUTATORS])
    if kind is Time:
        return sorted(TIME_MEMBERS)
    if kind is Duration:
        return sorted(DURATION_MEMBERS)
    if kind is Record:
        return sorted(value.fields)
    if kind is Namespace:
        return sorted(value.members)
    if kind is bytes:
        return ["elems"]
    return []


def sr_getattr(value: Any, name: str, *default: Any) -> Any:
    """Return an attribute, or a default where the value has none.

    Args:
        value: The receiver.
        name: The attribute's name.
        *default: What to return instead of failing.

    """
    try:
        return getattr_(value, name)
    except ExpressionError:
        if default:
            return default[0]
        raise


def sr_hasattr(value: Any, name: str) -> bool:
    """Report whether a value has an attribute.

    Args:
        value: The receiver.
        name: The attribute's name.

    """
    try:
        getattr_(value, name)
    except ExpressionError:
        return False
    return True


def sr_fail(*args: Any) -> Any:
    """Abort the expression with a message.

    Args:
        *args: The parts of the message, joined by spaces.

    Raises:
        ExpressionError: Always; that is what the builtin is for.

    """
    raise ExpressionError(" ".join(starlark_str(arg) for arg in args) or "fail")


def sr_bytes(value: Any = b"") -> bytes:
    """Return a bytes value from a string or a sequence of numbers.

    Args:
        value: What to convert.

    Raises:
        ExpressionError: The value is not convertible.

    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return bytes(value)
    except (TypeError, ValueError):
        raise ExpressionError(
            f"bytes() wants a string or a sequence of numbers, got {type_name(value)}"
        ) from None


def sr_sorted(
    items: Iterable[Any],
    key: Callable[[Any], Any] | None = None,
    reverse: bool = False,
) -> list[Any]:
    """Return the elements of a sequence in order.

    Args:
        items: What to sort.
        key: What to sort each element by.
        reverse: Whether to sort downward.

    """
    return sorted(items, key=key, reverse=reverse)


def sr_enumerate(items: Iterable[Any], start: int = 0) -> list[tuple[int, Any]]:
    """Return the elements of a sequence paired with their positions.

    Args:
        items: What to number.
        start: What to number the first element.

    """
    return list(enumerate(items, start))


def sr_zip(*sequences: Iterable[Any]) -> list[tuple[Any, ...]]:
    """Return the elements of several sequences, grouped by position.

    Args:
        *sequences: What to draw from, stopping with the shortest.

    """
    return list(zip(*sequences, strict=False))


def sr_reversed(items: Iterable[Any]) -> list[Any]:
    """Return the elements of a sequence back to front.

    Args:
        items: What to reverse.

    """
    return list(reversed(list(items)))


def sr_list(items: Iterable[Any] = ()) -> list[Any]:
    """Return a sequence's elements as a list that may be changed.

    Args:
        items: What to copy.

    """
    return list(items)


def sr_tuple(items: Iterable[Any] = ()) -> tuple[Any, ...]:
    """Return a sequence's elements as a tuple.

    Args:
        items: What to copy.

    """
    return tuple(items)


def sr_set(items: Iterable[Any] = ()) -> Set:
    """Return the distinct elements of a sequence, in first-seen order.

    Args:
        items: What to draw from.

    """
    return Set(items)


def sr_dict(entries: Any = (), **named: Any) -> dict[Any, Any]:
    """Return a dict built from pairs, another mapping, or keywords.

    Args:
        entries: Pairs or a mapping to copy.
        **named: Entries given by keyword.

    """
    if isinstance(entries, FrozenDict | Record):
        entries = dict(entries)
    return dict(entries, **named)


def sr_strftime(moment: Time, spec: str) -> str:
    """Return a time formatted by the familiar directives.

    ``%Y %y %m %d %H %M %S %j %B %b %A %a %p %I %Z %z %%``, and English
    names, as doc/expressions.md#strftime says: locale-independent,
    so that a report reads the same on every machine that builds it.

    Args:
        moment: The time to write.
        spec: The directives to write it with.

    Raises:
        ExpressionError: The value is not a time, or a directive is
            not one of the seventeen.

    """
    if not isinstance(moment, Time):
        raise ExpressionError(f"strftime() wants a time, got {type_name(moment)}")
    when = moment.moment
    offset = moment.offset_seconds()
    directives = {
        "Y": f"{when.year:04d}",
        "y": f"{when.year % 100:02d}",
        "m": f"{when.month:02d}",
        "d": f"{when.day:02d}",
        "H": f"{when.hour:02d}",
        "M": f"{when.minute:02d}",
        "S": f"{when.second:02d}",
        "j": f"{when.timetuple().tm_yday:03d}",
        "B": golayout.LONG_MONTHS[when.month - 1],
        "b": golayout.SHORT_MONTHS[when.month - 1],
        "A": golayout.LONG_WEEKDAYS[when.weekday()],
        "a": golayout.SHORT_WEEKDAYS[when.weekday()],
        "p": "PM" if when.hour >= 12 else "AM",
        "I": f"{when.hour % 12 or 12:02d}",
        "Z": golayout.zone_name(when, offset),
        "z": golayout.offset_text(offset, parts=2, colon=False, iso=False),
        "%": "%",
    }
    written: list[str] = []
    index = 0
    while index < len(spec):
        character = spec[index]
        if character != "%":
            written.append(character)
            index += 1
            continue
        if index + 1 >= len(spec):
            raise ExpressionError(f"strftime: a trailing % in {spec!r}")
        directive = spec[index + 1]
        if directive not in directives:
            raise ExpressionError(f"strftime: unknown directive %{directive}")
        written.append(directives[directive])
        index += 2
    return "".join(written)


def real(value: Any, name: str) -> float:
    """Return a value as the float a math function takes.

    A decimal is refused rather than converted.  doc/expressions.md
    makes decimals and floats separate on purpose, and a module that
    quietly bridged them would put the lossy conversion back where
    the language took it out; ``float(d)`` says it deliberately.

    Args:
        value: The argument.
        name: The function's name, for the diagnostic.

    Raises:
        ExpressionError: The value is not an int or a float.

    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        extra = (
            "; write float(d) to convert one deliberately"
            if isinstance(value, Decimal)
            else ""
        )
        raise ExpressionError(
            f"math.{name}() wants a number, got {type_name(value)}{extra}"
        )
    return float(value)


def math_function(name: str, function: Callable[..., float]) -> Callable[..., Any]:
    """Return one member of the ``math`` module, checking its arguments.

    Args:
        name: The member's name.
        function: What to compute once the arguments are floats.

    """

    def call(*args: Any) -> Any:
        return function(*(real(argument, name) for argument in args))

    return call


def math_log(value: Any, base: Any = None) -> float:
    """Return a logarithm, natural unless a base is given.

    Args:
        value: The number.
        base: The base, if not e.

    """
    if base is None:
        return pymath.log(real(value, "log"))
    return pymath.log(real(value, "log"), real(base, "log"))


MATH: Final = Namespace(
    "math",
    {
        "ceil": math_function("ceil", lambda value: pymath.ceil(value)),
        "floor": math_function("floor", lambda value: pymath.floor(value)),
        "round": math_function("round", lambda value: float(round_away(value))),
        "mod": math_function("mod", pymath.fmod),
        "pow": math_function("pow", pymath.pow),
        "sqrt": math_function("sqrt", pymath.sqrt),
        "fabs": math_function("fabs", pymath.fabs),
        "exp": math_function("exp", pymath.exp),
        "log": math_log,
        "hypot": math_function("hypot", pymath.hypot),
        "copysign": math_function("copysign", pymath.copysign),
        "remainder": math_function("remainder", pymath.remainder),
        "sin": math_function("sin", pymath.sin),
        "cos": math_function("cos", pymath.cos),
        "tan": math_function("tan", pymath.tan),
        "asin": math_function("asin", pymath.asin),
        "acos": math_function("acos", pymath.acos),
        "atan": math_function("atan", pymath.atan),
        "atan2": math_function("atan2", pymath.atan2),
        "sinh": math_function("sinh", pymath.sinh),
        "cosh": math_function("cosh", pymath.cosh),
        "tanh": math_function("tanh", pymath.tanh),
        "asinh": math_function("asinh", pymath.asinh),
        "acosh": math_function("acosh", pymath.acosh),
        "atanh": math_function("atanh", pymath.atanh),
        "degrees": math_function("degrees", pymath.degrees),
        "radians": math_function("radians", pymath.radians),
        "gamma": math_function("gamma", pymath.gamma),
        "pi": pymath.pi,
        "e": pymath.e,
    },
)


def time_value(
    year: int = 0,
    month: int = 0,
    day: int = 0,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    nanosecond: int = 0,
    location: str = "UTC",
) -> Time:
    """Return the time some calendar fields name.

    Out-of-range fields carry, as Go's ``time.Date`` does: month 13
    is January of the next year and day 0 is the last day of the month
    before.  Every field defaults to **zero** rather than to its lowest
    legal value, which is the reference's behaviour and is why
    ``time.time(year=2005)`` is the last day of November 2004: month 0
    is December of 2004 and day 0 is the day before the first of it.

    A time before year 1 is refused rather than built.  The reference
    has one -- ``time.time()`` with nothing named is in the year -1 --
    and a value that no field access and no layout can render is not
    worth carrying to reach it.

    Args:
        year: The calendar year.
        month: The month, from 1.
        day: The day of the month, from 1.
        hour: The hour, from 0.
        minute: The minute, from 0.
        second: The second, from 0.
        nanosecond: The sub-second part.
        location: Where these fields are a wall clock.

    Raises:
        ExpressionError: The fields do not name a time that exists.

    """
    zone = find_location(location)
    carried, month_index = divmod(month - 1, 12)
    try:
        first = datetime(year + carried, month_index + 1, 1, tzinfo=zone)
        moment = first + timedelta(
            days=day - 1, hours=hour, minutes=minute, seconds=second
        )
    except (ValueError, OverflowError) as error:
        raise ExpressionError(f"time(): {error}") from None
    return Time.from_datetime(moment, nanosecond)


def parse_time(
    text: str, format: str = golayout.RFC3339, location: str = "UTC"
) -> Time:
    """Return the time some text spells.

    Args:
        text: The value to read.
        format: The layout to read it with, in Go spelling.
        location: Where a value that names no zone of its own is.

    """
    moment, nanosecond = golayout.parse_layout(format, text, find_location(location))
    return Time.from_datetime(moment, nanosecond)


def from_timestamp(seconds: int, nanoseconds: int = 0) -> Time:
    """Return the time a Unix timestamp names.

    Args:
        seconds: Whole seconds since the epoch.
        nanoseconds: The sub-second part.

    """
    return Time(int(seconds) * NANOSECONDS + int(nanoseconds))


def parse_duration(text: Any) -> Duration:
    """Return the duration a string spells, in Go's spelling.

    ``300ms``, ``1h30m``, ``-2.5h``: a run of numbers each with a unit,
    where the units are ``ns``, ``us`` (or ``µs``), ``ms``, ``s``, ``m``
    and ``h``.

    Args:
        text: The duration, as a string or as a duration already.

    Raises:
        ExpressionError: The text is not a duration.

    """
    if isinstance(text, Duration):
        return text
    if not isinstance(text, str):
        raise ExpressionError(f"parse_duration() wants a string, got {type_name(text)}")
    units = {
        "ns": 1,
        "us": 1_000,
        "µs": 1_000,
        "μs": 1_000,
        "ms": 1_000_000,
        "s": NANOSECONDS,
        "m": 60 * NANOSECONDS,
        "h": 3600 * NANOSECONDS,
    }
    rest = text.strip()
    if rest in ("0", "+0", "-0"):
        return Duration(0)
    sign = 1
    if rest[:1] in "+-":
        sign = -1 if rest[0] == "-" else 1
        rest = rest[1:]
    if not rest:
        raise ExpressionError(f"not a duration: {text!r}")
    total = 0.0
    while rest:
        digits = 0
        while digits < len(rest) and (rest[digits].isdigit() or rest[digits] == "."):
            digits += 1
        if digits == 0:
            raise ExpressionError(f"not a duration: {text!r}")
        try:
            number = float(rest[:digits])
        except ValueError:
            raise ExpressionError(f"not a duration: {text!r}") from None
        rest = rest[digits:]
        for unit in sorted(units, key=len, reverse=True):
            if rest.startswith(unit):
                total += number * units[unit]
                rest = rest[len(unit) :]
                break
        else:
            raise ExpressionError(f"not a duration: {text!r}: unknown unit")
    return Duration(sign * round(total))


TIME: Final = Namespace(
    "time",
    {
        "time": time_value,
        "parse_time": parse_time,
        "from_timestamp": from_timestamp,
        "parse_duration": parse_duration,
        "is_valid_timezone": valid_timezone,
        "hour": Duration(3600 * NANOSECONDS),
        "minute": Duration(60 * NANOSECONDS),
        "second": Duration(NANOSECONDS),
        "millisecond": Duration(1_000_000),
        "microsecond": Duration(1_000),
        "nanosecond": Duration(1),
    },
)


# Every name an expression may use without one being supplied to it:
# the builtins doc/expressions.md keeps, the two modules, and the four
# names the engine predeclares.  `print` is absent, as the specification
# says, and so is everything Python would otherwise have put in scope.
GLOBALS: Final[dict[str, Any]] = {
    "abs": sr_abs,
    "all": all,
    "any": any,
    "bool": truthy,
    "bytes": sr_bytes,
    "chr": chr,
    "decimal": Decimal,
    "dict": sr_dict,
    "dir": sr_dir,
    "enumerate": sr_enumerate,
    "fail": sr_fail,
    "float": sr_float,
    "format": format_value,
    "getattr": sr_getattr,
    "hasattr": sr_hasattr,
    "hash": sr_hash,
    "int": sr_int,
    "len": sr_len,
    "list": sr_list,
    "math": MATH,
    "max": max,
    "min": min,
    "ord": ord,
    "quantize": quantize,
    "range": range,
    "repr": starlark_repr,
    "reversed": sr_reversed,
    "round": sr_round,
    "set": sr_set,
    "sorted": sr_sorted,
    "str": sr_str,
    "strftime": sr_strftime,
    "time": TIME,
    "tuple": sr_tuple,
    "type": type_name,
    "zip": sr_zip,
}


def iterate(value: Any) -> Iterator[Any]:
    """Return an iterator over a value, for a caller outside this module.

    Args:
        value: The sequence to iterate.

    Raises:
        ExpressionError: The value is not iterable.

    """
    try:
        return iter(value)
    except TypeError:
        raise ExpressionError(f"{type_name(value)} is not iterable") from None
