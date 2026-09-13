"""Go reference-time layouts: a time to text, and text back to a time.

doc/expressions.md gives ``.format`` a **Go reference-time layout**,
where the pattern is an example date rather than a set of directives:
``"02.01.2006"`` means day, dot, month, dot, four-digit year, because
2 January 2006 is the reference.  ``time.parse_time`` reads the same
patterns, and RFC 3339 -- the default for parsing a `datetime` parameter
and for reading a `datetime` member -- is itself one of them.

The reference moment is 15:04:05 on Monday 2 January 2006, -0700,
and the digits it is spelled with are what the tokens are::

    Mon Jan 2 15:04:05 MST 2006   ==   01/02 03:04:05PM '06 -0700

Two consequences are worth stating, because both look like bugs otherwise.
A layout is scanned **longest token first**, so ``2006`` is a year and not
a ``2`` followed by ``006``; and any text that happens to look like a token
*is* one, which is why ``May`` in a layout is a month name rather than a
word.  Both are Go's rules, and a layout that behaved differently here
would be a layout the two engines disagree about.

The module works in primitives -- an aware :class:`~datetime.datetime`
and a nanosecond count -- rather than in the engine's own time value,
so that ``values`` may import it and it need import nothing.

"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo

from sr.errors import ExpressionError

__all__ = [
    "LONG_MONTHS",
    "LONG_WEEKDAYS",
    "RFC3339",
    "SHORT_MONTHS",
    "SHORT_WEEKDAYS",
    "format_layout",
    "parse_layout",
    "split_layout",
]

# The one layout that is a specification rather than an example:
# what doc/template.md#parameter calls "RFC 3339 timestamp",
# and the default wherever a `format` property is left off.
RFC3339 = "2006-01-02T15:04:05Z07:00"

# English names, and only English: doc/expressions.md says `strftime` is
# locale-independent, and a layout is a pattern rather than a translation.
LONG_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
SHORT_MONTHS = tuple(name[:3] for name in LONG_MONTHS)
LONG_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
SHORT_WEEKDAYS = tuple(name[:3] for name in LONG_WEEKDAYS)


def fraction_tokens() -> tuple[str, ...]:
    """Return every fractional-second token, both spellings and both leads.

    ``.000`` pads to a fixed width and ``.999`` trims trailing zeros,
    dropping the point as well when nothing is left.  Go takes a comma
    as the decimal separator too, and refusing it here would make
    a layout this engine rejects and the reference accepts.

    """
    tokens: list[str] = []
    for lead in ".,":
        for digit in "09":
            tokens.extend(f"{lead}{digit * width}" for width in range(1, 10))
    return tuple(tokens)


# Every token, and what it means.  The names are Go's, minus the `std`
# prefix, so that a reader with Go's `format.go` open finds the same words.
TOKENS: dict[str, str] = {
    "January": "long-month",
    "Jan": "month",
    "Monday": "long-weekday",
    "Mon": "weekday",
    "2006": "long-year",
    "06": "year",
    "01": "zero-month",
    "1": "num-month",
    "002": "zero-yearday",
    "__2": "under-yearday",
    "_2": "under-day",
    "02": "zero-day",
    "2": "day",
    "15": "hour",
    "03": "zero-hour12",
    "3": "hour12",
    "04": "zero-minute",
    "4": "minute",
    "05": "zero-second",
    "5": "second",
    "PM": "pm-upper",
    "pm": "pm-lower",
    "-070000": "numeric-second-zone",
    "-07:00:00": "numeric-colon-second-zone",
    "-0700": "numeric-zone",
    "-07:00": "numeric-colon-zone",
    "-07": "numeric-short-zone",
    "Z070000": "iso-second-zone",
    "Z07:00:00": "iso-colon-second-zone",
    "Z0700": "iso-zone",
    "Z07:00": "iso-colon-zone",
    "Z07": "iso-short-zone",
    "MST": "abbrev-zone",
    **{token: "fraction" for token in fraction_tokens()},
}

# Longest first, which is the whole of Go's disambiguation: `2006` is
# a year because it is tried before `2`, and `-07:00:00` a zone because
# it is tried before `-07:00`.
ORDERED_TOKENS = tuple(sorted(TOKENS, key=len, reverse=True))

# How many of hours, minutes and seconds a zone token spells, and whether
# colons separate them.  `-07` is hours alone, `-0700` hours and minutes.
ZONE_SHAPE = {
    "numeric-second-zone": (3, False),
    "numeric-colon-second-zone": (3, True),
    "numeric-zone": (2, False),
    "numeric-colon-zone": (2, True),
    "numeric-short-zone": (1, False),
    "iso-second-zone": (3, False),
    "iso-colon-second-zone": (3, True),
    "iso-zone": (2, False),
    "iso-colon-zone": (2, True),
    "iso-short-zone": (1, False),
}

# The zone tokens that spell a zero offset `Z` rather than `+00:00`.
ISO_KINDS = frozenset(kind for kind in ZONE_SHAPE if kind.startswith("iso-"))

# What the names in a zone abbreviation mean when the parser meets one.
# Go looks the abbreviation up in the location it was given and fabricates
# a zone when it is not there; this engine reads the four spellings of
# "no offset" and leaves everything else to the caller's location, which
# is the same answer for every abbreviation a report is likely to carry.
UTC_NAMES = frozenset({"UTC", "GMT", "UT", "Z"})

LETTERS = re.compile(r"[A-Za-z]+")


def split_layout(layout: str) -> tuple[tuple[str, str], ...]:
    """Return a layout as a run of chunks, each a token or literal text.

    A chunk is ``(kind, text)``: a literal's kind is ``"literal"``
    and its text is what to write or expect, and a token's text is
    the token as the layout spelled it, which is what says how wide
    a fraction is.

    Args:
        layout: The layout, in Go reference-time spelling.

    """
    chunks: list[tuple[str, str]] = []
    literal: list[str] = []
    position = 0
    while position < len(layout):
        for token in ORDERED_TOKENS:
            if layout.startswith(token, position):
                if literal:
                    chunks.append(("literal", "".join(literal)))
                    literal = []
                chunks.append((TOKENS[token], token))
                position += len(token)
                break
        else:
            literal.append(layout[position])
            position += 1
    if literal:
        chunks.append(("literal", "".join(literal)))
    return tuple(chunks)


def offset_text(seconds: int, *, parts: int, colon: bool, iso: bool) -> str:
    """Return a zone offset written the way a zone token spells one.

    Args:
        seconds: The offset east of UTC, in seconds.
        parts: How many of hours, minutes and seconds to write.
        colon: Whether to separate them with colons.
        iso: Whether a zero offset is written ``Z``.

    """
    if iso and seconds == 0:
        return "Z"
    sign = "-" if seconds < 0 else "+"
    total = abs(seconds)
    fields = (total // 3600, total // 60 % 60, total % 60)[:parts]
    separator = ":" if colon else ""
    return sign + separator.join(f"{field:02d}" for field in fields)


def fraction_text(token: str, nanosecond: int) -> str:
    """Return the fractional second a fraction token asks for.

    Args:
        token: The token, whose length gives the number of digits
            and whose digit says whether trailing zeros are kept.
        nanosecond: The sub-second part, in nanoseconds.

    """
    width = len(token) - 1
    digits = f"{nanosecond:09d}"[:width]
    if token[1] == "0":
        return token[0] + digits
    digits = digits.rstrip("0")
    return token[0] + digits if digits else ""


def zone_name(moment: datetime, seconds: int) -> str:
    """Return the abbreviation ``MST`` writes for a moment's location.

    A zone with no abbreviation of its own -- a fixed offset, which is
    what a parsed ``-0700`` leaves behind -- falls back to the numeric
    form, as Go does.

    Args:
        moment: The time, as an aware datetime.
        seconds: Its offset east of UTC, in seconds.

    """
    name = moment.tzname()
    if name and LETTERS.fullmatch(name):
        return name
    return offset_text(seconds, parts=2, colon=False, iso=False)


def format_layout(layout: str, moment: datetime, nanosecond: int) -> str:
    """Return a moment written the way a layout spells it.

    Args:
        layout: The layout, in Go reference-time spelling.
        moment: The time to write, as an aware datetime already in the
            location it is to be written in.
        nanosecond: The sub-second part, in nanoseconds, which a datetime
            cannot hold at full precision.

    """
    offset = moment.utcoffset() or timedelta(0)
    seconds = int(offset.total_seconds())
    hour12 = moment.hour % 12 or 12
    yearday = moment.timetuple().tm_yday
    written: list[str] = []
    for kind, token in split_layout(layout):
        if kind == "literal":
            written.append(token)
        elif kind == "long-month":
            written.append(LONG_MONTHS[moment.month - 1])
        elif kind == "month":
            written.append(SHORT_MONTHS[moment.month - 1])
        elif kind == "long-weekday":
            written.append(LONG_WEEKDAYS[moment.weekday()])
        elif kind == "weekday":
            written.append(SHORT_WEEKDAYS[moment.weekday()])
        elif kind == "long-year":
            written.append(f"{moment.year:04d}")
        elif kind == "year":
            written.append(f"{moment.year % 100:02d}")
        elif kind == "zero-month":
            written.append(f"{moment.month:02d}")
        elif kind == "num-month":
            written.append(str(moment.month))
        elif kind == "zero-day":
            written.append(f"{moment.day:02d}")
        elif kind == "under-day":
            written.append(f"{moment.day:2d}")
        elif kind == "day":
            written.append(str(moment.day))
        elif kind == "zero-yearday":
            written.append(f"{yearday:03d}")
        elif kind == "under-yearday":
            written.append(f"{yearday:3d}")
        elif kind == "hour":
            written.append(f"{moment.hour:02d}")
        elif kind == "zero-hour12":
            written.append(f"{hour12:02d}")
        elif kind == "hour12":
            written.append(str(hour12))
        elif kind == "zero-minute":
            written.append(f"{moment.minute:02d}")
        elif kind == "minute":
            written.append(str(moment.minute))
        elif kind == "zero-second":
            written.append(f"{moment.second:02d}")
        elif kind == "second":
            written.append(str(moment.second))
        elif kind == "pm-upper":
            written.append("PM" if moment.hour >= 12 else "AM")
        elif kind == "pm-lower":
            written.append("pm" if moment.hour >= 12 else "am")
        elif kind == "fraction":
            written.append(fraction_text(token, nanosecond))
        elif kind == "abbrev-zone":
            written.append(zone_name(moment, seconds))
        else:
            parts, colon = ZONE_SHAPE[kind]
            written.append(
                offset_text(seconds, parts=parts, colon=colon, iso=kind in ISO_KINDS)
            )
    return "".join(written)


class Reader:
    """A cursor over the text being parsed, and the errors it can raise.

    A class rather than an index passed from one reader to the next,
    because each of the twenty-odd token readers needs both the position
    and the whole value to name in a diagnostic.

    Attributes:
        text: The whole value being read.
        position: How far into it the parse has got.

    """

    def __init__(self, text: str) -> None:
        """Start at the beginning of the text.

        Args:
            text: The value to read.

        """
        self.text = text
        self.position = 0

    def fail(self, wanted: str) -> ExpressionError:
        """Return the error for text that is not what a token wanted.

        Args:
            wanted: What the token was looking for, as a noun phrase.

        """
        rest = self.text[self.position :]
        return ExpressionError(
            f"cannot parse {self.text!r} as a time: wanted {wanted} at {rest!r}",
            offset=self.position,
        )

    def literal(self, text: str) -> None:
        """Consume text the layout gives literally.

        Args:
            text: What the layout spelled between two tokens.

        """
        if not self.text.startswith(text, self.position):
            raise self.fail(repr(text))
        self.position += len(text)

    def digits(self, most: int, *, fixed: bool = False) -> int:
        """Consume a run of digits and return what it spells.

        ASCII digits only.  Python's ``isdigit`` says yes to a dozen other
        scripts, and a timestamp written in Devanagari is not one this
        engine and the reference would read the same way.

        Args:
            most: The greatest number of digits to take.
            fixed: Whether exactly that many are required.

        """
        start = self.position
        while self.position < len(self.text) and self.position - start < most:
            character = self.text[self.position]
            if not (character.isascii() and character.isdigit()):
                break
            self.position += 1
        taken = self.position - start
        if taken == 0 or (fixed and taken != most):
            self.position = start
            raise self.fail(f"{most} digits" if fixed else "a number")
        return int(self.text[start : self.position])

    def spaces(self) -> None:
        """Consume the padding an underscore token takes in place of a digit."""
        while self.text.startswith(" ", self.position):
            self.position += 1

    def name(self, names: tuple[str, ...], wanted: str) -> int:
        """Consume one of a set of names and return its index.

        Matching is case-insensitive, as Go's is, so a layout written
        ``Jan`` reads ``JAN`` and ``jan`` as well.

        Args:
            names: The names to try, the longest match winning.
            wanted: What to call them in a diagnostic.

        """
        rest = self.text[self.position :].lower()
        best = -1
        for index, name in enumerate(names):
            if rest.startswith(name.lower()) and (
                best < 0 or len(name) > len(names[best])
            ):
                best = index
        if best < 0:
            raise self.fail(wanted)
        self.position += len(names[best])
        return best

    def sign(self) -> int:
        """Consume the sign a numeric zone begins with, and return it."""
        if self.text.startswith("+", self.position):
            self.position += 1
            return 1
        if self.text.startswith("-", self.position):
            self.position += 1
            return -1
        raise self.fail("a zone offset")


def read_zone(reader: Reader, kind: str) -> int:
    """Return the offset a numeric zone token reads, in seconds east of UTC.

    Args:
        reader: The cursor to read from.
        kind: The token's kind, which gives its shape.

    """
    parts, colon = ZONE_SHAPE[kind]
    direction = reader.sign()
    fields = []
    for index in range(parts):
        if colon and index:
            reader.literal(":")
        fields.append(reader.digits(2, fixed=True))
    fields += [0] * (3 - parts)
    return direction * (fields[0] * 3600 + fields[1] * 60 + fields[2])


def read_fraction(reader: Reader, token: str) -> int:
    """Return the nanoseconds a fractional-second token reads.

    A trimming token (``.999``) matches nothing at all, which is
    what makes RFC 3339's fraction optional.

    Args:
        reader: The cursor to read from.
        token: The token, whose digit says whether it is optional.

    """
    optional = token[1] != "0"
    if not reader.text.startswith(token[0], reader.position):
        if optional:
            return 0
        raise reader.fail(f"a fraction beginning {token[0]!r}")
    start = reader.position
    reader.position += 1
    try:
        digits_start = reader.position
        reader.digits(9)
    except ExpressionError:
        if not optional:
            raise
        reader.position = start
        return 0
    return int(reader.text[digits_start : reader.position].ljust(9, "0"))


def parse_layout(layout: str, text: str, location: tzinfo) -> tuple[datetime, int]:
    """Return the moment a layout reads out of some text.

    The result is an aware datetime and the nanoseconds it cannot hold.
    A layout with no zone token leaves the text a wall clock, which
    is read in ``location``; one with a zone token takes the offset
    from the text, as Go does.

    Args:
        layout: The layout, in Go reference-time spelling.
        text: The value to read.
        location: Where a time that names no zone of its own is.

    Raises:
        ExpressionError: The text is not what the layout describes.

    """
    reader = Reader(text)
    fields = {"year": 1, "month": 1, "day": 1, "hour": 0, "minute": 0, "second": 0}
    nanosecond = 0
    afternoon: bool | None = None
    twelve = False
    offset: int | None = None
    for kind, token in split_layout(layout):
        if kind == "literal":
            reader.literal(token)
        elif kind == "long-month":
            fields["month"] = reader.name(LONG_MONTHS, "a month name") + 1
        elif kind == "month":
            fields["month"] = reader.name(SHORT_MONTHS, "a month name") + 1
        elif kind in ("long-weekday", "weekday"):
            names = LONG_WEEKDAYS if kind == "long-weekday" else SHORT_WEEKDAYS
            reader.name(names, "a weekday name")
        elif kind == "long-year":
            fields["year"] = reader.digits(4, fixed=True)
        elif kind == "year":
            two = reader.digits(2, fixed=True)
            fields["year"] = 1900 + two if two >= 69 else 2000 + two
        elif kind == "zero-month":
            fields["month"] = reader.digits(2, fixed=True)
        elif kind == "num-month":
            fields["month"] = reader.digits(2)
        elif kind == "zero-day":
            fields["day"] = reader.digits(2, fixed=True)
        elif kind in ("under-day", "day"):
            reader.spaces()
            fields["day"] = reader.digits(2)
        elif kind in ("zero-yearday", "under-yearday"):
            reader.spaces()
            reader.digits(3)
        elif kind == "hour":
            fields["hour"] = reader.digits(2)
        elif kind == "zero-hour12":
            fields["hour"], twelve = reader.digits(2, fixed=True), True
        elif kind == "hour12":
            fields["hour"], twelve = reader.digits(2), True
        elif kind == "zero-minute":
            fields["minute"] = reader.digits(2, fixed=True)
        elif kind == "minute":
            fields["minute"] = reader.digits(2)
        elif kind == "zero-second":
            fields["second"] = reader.digits(2, fixed=True)
        elif kind == "second":
            fields["second"] = reader.digits(2)
        elif kind in ("pm-upper", "pm-lower"):
            afternoon = reader.name(("AM", "PM"), "AM or PM") == 1
        elif kind == "fraction":
            nanosecond = read_fraction(reader, token)
        elif kind == "abbrev-zone":
            found = LETTERS.match(reader.text, reader.position)
            if found is None:
                raise reader.fail("a zone abbreviation")
            reader.position = found.end()
            if found.group() in UTC_NAMES:
                offset = 0
        elif kind in ISO_KINDS and reader.text.startswith("Z", reader.position):
            reader.position += 1
            offset = 0
        else:
            offset = read_zone(reader, kind)
    if reader.position != len(reader.text):
        raise reader.fail("the end of the value")
    if twelve and afternoon is not None:
        fields["hour"] = fields["hour"] % 12 + (12 if afternoon else 0)
    zone = location if offset is None else timezone(timedelta(seconds=offset))
    try:
        moment = datetime(
            fields["year"],
            fields["month"],
            fields["day"],
            fields["hour"],
            fields["minute"],
            fields["second"],
            nanosecond // 1000,
            tzinfo=zone,
        )
    except ValueError as error:
        raise ExpressionError(f"cannot parse {text!r} as a time: {error}") from None
    return moment, nanosecond
