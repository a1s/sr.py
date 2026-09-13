"""The value model: what an expression computes with.

doc/expressions.md describes six kinds of value the host language
does not supply, and this module is those six: an exact :class:`Decimal`,
a :class:`Time` and the :class:`Duration` between two of them,
a :class:`Record` for a data row, an ordered :class:`Set`, and the
frozen list and dict a variable accumulator hands back.  Everything
else -- ``int``, ``float``, ``str``, ``bool``, ``None``, ``tuple`` --
is Python's own, because Starlark's semantics for those are Python's
semantics.

Four rules are the reason this is a module rather than a few aliases.

* **A decimal is exact, and mixes with an int but not with a float.**
  doc/expressions.md#the-decimal-type says the three parts move together:
  ordered comparison against an int works, ``decimal("1") == 1`` is
  therefore true, and the two hash equal.  A float is refused for
  arithmetic and for ordered comparison, and ``==`` against one is
  **false** rather than an error.  Python's own ``Decimal`` compares
  happily with a float and would give the last of those away, which
  is what the wrapper is for.

* **A time is Go's, not Python's.**  Nanosecond resolution, a zero time
  that is false, Go layout formatting, and durations whose accessors
  are floats.  The instant is held as a nanosecond count and the location
  beside it, so that arithmetic and comparison are integer operations and
  only display depends on the zone.

* **Frozen means frozen.**  A record, a parameter and an accumulator
  cannot be mutated from an expression.  The mutating methods exist
  and raise, which is what the specification describes and what tells
  a template author the difference between a value they may not change
  and a method that does not exist.

* **Text is what the reference writes.**  ``str`` and ``repr`` are
  Starlark's rather than Python's -- a set prints ``set([1, 2])``,
  a string quotes with ``"``, and a float follows Go's shortest form,
  which turns to exponent notation four orders of magnitude earlier
  than Python's does.

"""

from __future__ import annotations

import decimal
import math
from collections.abc import Hashable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from fractions import Fraction
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sr.errors import ExpressionError
from sr.expr import golayout

__all__ = [
    "DIVISION_PLACES",
    "Decimal",
    "Duration",
    "FrozenDict",
    "FrozenList",
    "Namespace",
    "Record",
    "Set",
    "Time",
    "find_location",
    "format_float",
    "go_shortest",
    "quantize",
    "starlark_repr",
    "starlark_str",
    "truthy",
    "type_name",
]

# doc/expressions.md: division "produces a decimal quantized to 6
# fractional digits, rounding half away from zero", and `avg` follows it.
DIVISION_PLACES: Final = 6

# Addition, subtraction and multiplication are exact, so they run
# in a context that will not round: `prec` is the number of significant
# digits an operation may keep, and this is as many as the module allows.
# A division under this context could run forever producing digits, which
# is why `/` quantizes with integer arithmetic instead of dividing here.
EXACT: Final = decimal.Context(
    prec=decimal.MAX_PREC,
    Emax=decimal.MAX_EMAX,
    Emin=decimal.MIN_EMIN,
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
)

# Nanoseconds in a second, and the instant Go calls the zero time:
# year 1, January 1, midnight UTC, which doc/expressions.md#truth-values
# says is false.
NANOSECONDS: Final = 1_000_000_000
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
ZERO_TIME_NANOS: Final = -62135596800 * NANOSECONDS


class Decimal:
    """An exact decimal number, with the scale exactness requires.

    Wraps Python's :class:`decimal.Decimal`, whose scale rules are already
    the specification's -- two 2-place values add to 2 places and multiply
    to 4 -- and whose comparison against an ``int`` is already exact.  What
    the wrapper adds is the refusal: Python compares a decimal with a float
    and this type does not, because doc/expressions.md makes that mixing an
    error rather than a silent conversion through binary64.

    Attributes:
        value: The number, as Python holds it.

    """

    __slots__ = ("value",)

    value: decimal.Decimal

    def __init__(self, value: Any) -> None:
        """Hold a number given exactly.

        Args:
            value: A Python decimal, an int, or the text of a decimal.

        Raises:
            ExpressionError: The text is not a decimal, or the value is
                a float, which cannot become one without losing something.

        """
        if isinstance(value, Decimal):
            self.value = value.value
        elif isinstance(value, bool):
            raise ExpressionError("decimal() wants a number or a string, got bool")
        elif isinstance(value, int):
            self.value = decimal.Decimal(value)
        elif isinstance(value, decimal.Decimal):
            self.value = value
        elif isinstance(value, str):
            self.value = parse_decimal(value)
        elif isinstance(value, float):
            raise ExpressionError(
                "decimal() will not take a float, which is not exact; "
                f"write decimal(str(x)) to accept the conversion: {value!r}"
            )
        else:
            raise ExpressionError(
                f"decimal() wants a number or a string, got {type_name(value)}"
            )

    def __repr__(self) -> str:
        """Spell the number the way the language does, with no exponent."""
        return format(self.value, "f")

    __str__ = __repr__

    def __bool__(self) -> bool:
        """Report whether the number is non-zero, per the truth table."""
        return bool(self.value)

    def __hash__(self) -> int:
        """Hash as the equal int does, so a dict keyed on either agrees."""
        return hash(self.value)

    def __float__(self) -> float:
        """Return the nearest float, which is the lossy conversion."""
        return float(self.value)

    def __int__(self) -> int:
        """Return the integer part, truncated toward zero."""
        return int(self.value)

    def __neg__(self) -> Decimal:
        """Return the number with its sign flipped.

        ``copy_negate`` rather than ``-``, which applies the current
        decimal context and would round a number with more digits
        than it allows.  Every operation in this class is context-free
        or names :data:`EXACT`, for that reason.

        """
        return Decimal(self.value.copy_negate())

    def __pos__(self) -> Decimal:
        """Return the number unchanged."""
        return self

    def __abs__(self) -> Decimal:
        """Return the number without its sign, and without rounding it."""
        return Decimal(self.value.copy_abs())

    def __add__(self, other: object) -> Any:
        """Add exactly, to a decimal or an int."""
        return self.exactly(other, EXACT.add, "+")

    __radd__ = __add__

    def __sub__(self, other: object) -> Any:
        """Subtract exactly, a decimal or an int."""
        return self.exactly(other, EXACT.subtract, "-")

    def __rsub__(self, other: object) -> Any:
        """Subtract this from a decimal or an int, exactly."""
        return self.exactly(
            other, lambda mine, theirs: EXACT.subtract(theirs, mine), "-"
        )

    def __mul__(self, other: object) -> Any:
        """Multiply exactly, by a decimal or an int."""
        return self.exactly(other, EXACT.multiply, "*")

    __rmul__ = __mul__

    def __truediv__(self, other: object) -> Any:
        """Divide, quantized to six places, halves away from zero."""
        theirs = self.operand(other, "/")
        if theirs is None:
            return NotImplemented
        return divide_exactly(self.value, theirs)

    def __rtruediv__(self, other: object) -> Any:
        """Divide a decimal or an int by this one."""
        theirs = self.operand(other, "/")
        if theirs is None:
            return NotImplemented
        return divide_exactly(theirs, self.value)

    def __eq__(self, other: object) -> Any:
        """Compare for equality, which a float loses rather than refuses.

        ``decimal("1") == 1`` is true and ``decimal("1") == 1.0`` is false.
        The second is the dialect's answer for two values that cannot be
        compared, and is deliberately not an error -- doc/expressions.md
        makes that the one thing a float may do with a decimal.

        """
        if isinstance(other, Decimal):
            return self.value == other.value
        if isinstance(other, bool):
            return False
        if isinstance(other, int):
            return self.value == other
        return NotImplemented

    def __ne__(self, other: object) -> Any:
        """Negate equality, keeping a float unequal rather than an error."""
        equal = self.__eq__(other)
        return NotImplemented if equal is NotImplemented else not equal

    def __lt__(self, other: object) -> Any:
        """Order against a decimal or an int, exactly."""
        theirs = self.operand(other, "<")
        return NotImplemented if theirs is None else self.value < theirs

    def __le__(self, other: object) -> Any:
        """Order against a decimal or an int, exactly."""
        theirs = self.operand(other, "<=")
        return NotImplemented if theirs is None else self.value <= theirs

    def __gt__(self, other: object) -> Any:
        """Order against a decimal or an int, exactly."""
        theirs = self.operand(other, ">")
        return NotImplemented if theirs is None else self.value > theirs

    def __ge__(self, other: object) -> Any:
        """Order against a decimal or an int, exactly."""
        theirs = self.operand(other, ">=")
        return NotImplemented if theirs is None else self.value >= theirs

    def operand(self, other: object, operator: str) -> decimal.Decimal | None:
        """Return another value as a decimal, or ``None`` if it is not one.

        Args:
            other: The other operand.
            operator: How the two were combined, for the diagnostic.

        Raises:
            ExpressionError: The other operand is a float.

        """
        if isinstance(other, Decimal):
            return other.value
        if isinstance(other, bool):
            return None
        if isinstance(other, int):
            return decimal.Decimal(other)
        if isinstance(other, float):
            raise ExpressionError(
                f"decimal {operator} float: the two do not mix, because the "
                "conversion loses digits; write float(d) or decimal(str(f))"
            )
        return None

    def exactly(self, other: object, combine: Any, operator: str) -> Any:
        """Return the exact result of combining this with another value.

        Args:
            other: The other operand.
            combine: The context operation to apply, taking
                this value first and the other second.
            operator: How the two were combined, for the diagnostic.

        """
        theirs = self.operand(other, operator)
        if theirs is None:
            return NotImplemented
        return Decimal(combine(self.value, theirs))


def parse_decimal(text: str) -> decimal.Decimal:
    """Return the decimal a string spells.

    Args:
        text: The number, as ``decimal()`` was given it.

    Raises:
        ExpressionError: The text is not a decimal, or is one
            of the non-finite values a decimal has no room for.

    """
    try:
        value = decimal.Decimal(text.strip())
    except decimal.InvalidOperation:
        raise ExpressionError(f"not a decimal: {text!r}") from None
    if not value.is_finite():
        raise ExpressionError(f"not a finite decimal: {text!r}")
    return value


def divide_exactly(numerator: decimal.Decimal, denominator: decimal.Decimal) -> Decimal:
    """Return a quotient quantized to six places, halves away from zero.

    Done as one exact ratio of integers rather than by dividing
    and then rounding, because a quotient rounded twice is not
    always the quotient rounded once: a value that lands exactly
    on a half at the seventh place has already moved by the time
    a fixed-precision division hands it over.

    Args:
        numerator: The value to divide.
        denominator: What to divide it by.

    Raises:
        ExpressionError: The denominator is zero.

    """
    if not denominator:
        raise ExpressionError("decimal division by zero")
    ratio = Fraction(numerator.scaleb(DIVISION_PLACES, EXACT)) / Fraction(denominator)
    whole, remainder = divmod(abs(ratio.numerator), ratio.denominator)
    if remainder * 2 >= ratio.denominator:
        whole += 1
    if ratio < 0:
        whole = -whole
    return Decimal(decimal.Decimal(whole).scaleb(-DIVISION_PLACES, EXACT))


def quantize(value: Decimal | int, places: int) -> Decimal:
    """Return a decimal rounded to a number of places, halves away from zero.

    The ``quantize`` builtin, and how ``%f`` reaches an exact decimal
    without going through a float.

    Args:
        value: The number to round.
        places: How many fractional digits to keep.

    Raises:
        ExpressionError: The value is not a decimal or an int.

    """
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise ExpressionError(f"quantize() wants a decimal, got {type_name(value)}")
    if isinstance(places, bool) or not isinstance(places, int):
        raise ExpressionError(
            f"quantize() wants an int for places, got {type_name(places)}"
        )
    number = value.value if isinstance(value, Decimal) else decimal.Decimal(value)
    step = decimal.Decimal(1).scaleb(-places, EXACT)
    return Decimal(number.quantize(step, decimal.ROUND_HALF_UP, EXACT))


def find_location(name: str) -> tzinfo:
    """Return the location a zone name refers to.

    ``UTC`` and ``Local`` are Go's own two special names.
    ``Local`` resolves to the host's zone and is therefore the one name
    in the language whose meaning depends on the machine; a template
    that wants the same output everywhere names a zone.

    Args:
        name: An IANA zone name, ``UTC``, or ``Local``.

    Raises:
        ExpressionError: No such zone, or no zone database to look in.

    """
    if name in ("", "UTC"):
        return UTC
    if name == "Local":
        return datetime.now().astimezone().tzinfo or UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        note = missing_database()
        raise ExpressionError(f"unknown timezone: {name!r}{note}") from None


def missing_database() -> str:
    """Return a note about the zone database when there is not one.

    Windows ships no IANA database, so a zone name that is perfectly good
    everywhere else fails there unless the `tzdata` package is installed.
    A diagnostic that said only "unknown timezone" would send the reader
    looking for a typo instead.

    """
    try:
        ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return " (no zone database is installed: pip install tzdata)"
    return ""


def valid_timezone(name: str) -> bool:
    """Report whether a zone name resolves, as ``time.is_valid_timezone`` does.

    Args:
        name: The name to try.

    """
    try:
        find_location(name)
    except ExpressionError:
        return False
    return True


class Duration:
    """A length of time, held in nanoseconds and read out as floats.

    doc/expressions.md gives every accessor as a float, including
    ``.nanoseconds``, so ``(a - b).hours / 24`` is arithmetic rather than
    a division that silently floors.  The count itself stays an integer,
    which is what keeps ``t + 3 * time.hour`` exact.

    Attributes:
        nanos: The length, in whole nanoseconds.

    """

    __slots__ = ("nanos",)

    def __init__(self, nanos: int) -> None:
        """Hold a length in nanoseconds.

        Args:
            nanos: The length, in whole nanoseconds.

        """
        self.nanos = int(nanos)

    @property
    def hours(self) -> float:
        """Return the length in hours."""
        return self.nanos / (3600 * NANOSECONDS)

    @property
    def minutes(self) -> float:
        """Return the length in minutes."""
        return self.nanos / (60 * NANOSECONDS)

    @property
    def seconds(self) -> float:
        """Return the length in seconds."""
        return self.nanos / NANOSECONDS

    @property
    def milliseconds(self) -> float:
        """Return the length in milliseconds."""
        return self.nanos / 1_000_000

    @property
    def microseconds(self) -> float:
        """Return the length in microseconds."""
        return self.nanos / 1_000

    @property
    def nanoseconds(self) -> float:
        """Return the length in nanoseconds, as a float like the rest."""
        return float(self.nanos)

    def __repr__(self) -> str:
        """Spell the duration the way Go does: ``1h30m0s``, ``1.5s``, ``0s``."""
        return duration_text(self.nanos)

    __str__ = __repr__

    def __bool__(self) -> bool:
        """Report whether the duration is non-zero, per the truth table."""
        return self.nanos != 0

    def __hash__(self) -> int:
        """Hash on the length."""
        return hash(("duration", self.nanos))

    def __eq__(self, other: object) -> bool:
        """Compare two durations by length."""
        if isinstance(other, Duration):
            return self.nanos == other.nanos
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        """Order two durations by length."""
        if isinstance(other, Duration):
            return self.nanos < other.nanos
        return NotImplemented

    def __le__(self, other: object) -> bool:
        """Order two durations by length."""
        if isinstance(other, Duration):
            return self.nanos <= other.nanos
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        """Order two durations by length."""
        if isinstance(other, Duration):
            return self.nanos > other.nanos
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        """Order two durations by length."""
        if isinstance(other, Duration):
            return self.nanos >= other.nanos
        return NotImplemented

    def __neg__(self) -> Duration:
        """Return the duration pointing the other way."""
        return Duration(-self.nanos)

    def __abs__(self) -> Duration:
        """Return the duration without its sign."""
        return Duration(abs(self.nanos))

    def __add__(self, other: object) -> Duration | Time:
        """Add another duration, or offset a time."""
        if isinstance(other, Duration):
            return Duration(self.nanos + other.nanos)
        if isinstance(other, Time):
            return other + self
        return NotImplemented

    __radd__ = __add__

    def __sub__(self, other: object) -> Duration:
        """Subtract another duration."""
        if isinstance(other, Duration):
            return Duration(self.nanos - other.nanos)
        return NotImplemented

    def __mul__(self, other: object) -> Duration:
        """Scale the duration by an int, as ``3 * time.hour`` does."""
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, int):
            return Duration(self.nanos * other)
        if isinstance(other, float):
            return Duration(round(self.nanos * other))
        return NotImplemented

    __rmul__ = __mul__

    def __truediv__(self, other: object) -> Duration | float:
        """Divide by a number to shorten it, or by a duration for a ratio."""
        if isinstance(other, Duration):
            return self.nanos / other.nanos
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, int | float):
            return Duration(round(self.nanos / other))
        return NotImplemented


def duration_text(nanos: int) -> str:
    """Return a duration written the way Go's ``String`` writes one.

    Under a second the unit is the largest that leaves a value
    at least one, so 1500 nanoseconds is ``1.5µs``; at a second
    and over the form is hours, minutes and seconds with the
    leading empty ones dropped.

    Args:
        nanos: The length, in whole nanoseconds.

    """
    if nanos == 0:
        return "0s"
    sign = "-" if nanos < 0 else ""
    left = abs(nanos)
    if left < NANOSECONDS:
        for unit, scale in (("ns", 1), ("µs", 1_000), ("ms", 1_000_000)):
            if left < scale * 1000:
                return sign + trim_number(left, scale) + unit
    seconds, fraction = divmod(left, NANOSECONDS)
    text = trim_number(seconds % 60 * NANOSECONDS + fraction, NANOSECONDS) + "s"
    minutes = seconds // 60
    if minutes:
        text = f"{minutes % 60}m{text}"
        if minutes // 60:
            text = f"{minutes // 60}h{text}"
    return sign + text


def trim_number(value: int, scale: int) -> str:
    """Return a scaled integer with no trailing zeros in its fraction.

    Args:
        value: The number to scale.
        scale: What to divide it by, a power of ten.

    """
    whole, fraction = divmod(value, scale)
    if not fraction:
        return str(whole)
    digits = str(fraction).rjust(len(str(scale)) - 1, "0").rstrip("0")
    return f"{whole}.{digits}"


class Time:
    """An instant, with the location it is read in.

    The instant is a nanosecond count from the Unix epoch, which is
    what makes comparison and arithmetic integer operations and keeps
    the resolution doc/expressions.md asks for -- a datetime holds only
    microseconds.  The location changes what the fields say and never
    which instant it is, so ``.in_location`` is not a conversion.

    Attributes:
        nanos: Nanoseconds since the Unix epoch, which may be negative.
        zone: Where the instant is read.
        zone_name: What that location was called, for ``.in_location``
            to report and for a new time to inherit.

    """

    __slots__ = ("nanos", "zone", "zone_name")

    def __init__(
        self, nanos: int, zone: tzinfo | None = None, zone_name: str = "UTC"
    ) -> None:
        """Hold an instant and where it is read.

        Args:
            nanos: Nanoseconds since the Unix epoch.
            zone: The location, defaulting to UTC.
            zone_name: What that location is called.

        """
        self.nanos = int(nanos)
        self.zone = UTC if zone is None else zone
        self.zone_name = zone_name

    @classmethod
    def from_datetime(cls, moment: datetime, nanosecond: int | None = None) -> Time:
        """Return the time an aware datetime names.

        Args:
            moment: The instant, which must carry a zone.
            nanosecond: The sub-second part, where the caller has it
                at full precision; the datetime's microseconds otherwise.

        """
        offset = moment.utcoffset()
        if offset is None:
            raise ExpressionError("a time needs a location")
        naive = moment.replace(tzinfo=None) - offset
        whole = round((naive - EPOCH.replace(tzinfo=None)).total_seconds())
        sub = moment.microsecond * 1000 if nanosecond is None else nanosecond
        name = moment.tzname() or "UTC"
        zone = moment.tzinfo
        return cls(whole * NANOSECONDS + sub, zone, name)

    @property
    def moment(self) -> datetime:
        """Return the instant as an aware datetime in its own location."""
        seconds, _ = divmod(self.nanos, NANOSECONDS)
        try:
            return (EPOCH + timedelta(seconds=seconds)).astimezone(self.zone)
        except (OverflowError, OSError, ValueError):
            raise ExpressionError(
                f"time out of range in {self.zone_name}: {self.nanos} ns"
            ) from None

    @property
    def year(self) -> int:
        """Return the calendar year in this location."""
        return self.moment.year

    @property
    def month(self) -> int:
        """Return the month, from 1."""
        return self.moment.month

    @property
    def day(self) -> int:
        """Return the day of the month, from 1."""
        return self.moment.day

    @property
    def hour(self) -> int:
        """Return the hour, from 0."""
        return self.moment.hour

    @property
    def minute(self) -> int:
        """Return the minute, from 0."""
        return self.moment.minute

    @property
    def second(self) -> int:
        """Return the second, from 0."""
        return self.moment.second

    @property
    def nanosecond(self) -> int:
        """Return the sub-second part, in nanoseconds."""
        return self.nanos % NANOSECONDS

    @property
    def unix(self) -> int:
        """Return whole seconds since the Unix epoch."""
        return self.nanos // NANOSECONDS

    @property
    def unix_nano(self) -> int:
        """Return nanoseconds since the Unix epoch."""
        return self.nanos

    def in_location(self, name: str) -> Time:
        """Return the same instant, read in another location.

        Args:
            name: An IANA zone name, ``UTC``, or ``Local``.

        """
        return Time(self.nanos, find_location(name), name)

    def format(self, layout: str) -> str:
        """Return the time written the way a Go layout spells it.

        Args:
            layout: The layout, in Go reference-time spelling.

        """
        return golayout.format_layout(layout, self.moment, self.nanosecond)

    def __repr__(self) -> str:
        """Spell the time the way Go's ``String`` does."""
        text = golayout.format_layout(
            "2006-01-02 15:04:05.999999999 -0700", self.moment, self.nanosecond
        )
        return f"{text} {golayout.zone_name(self.moment, self.offset_seconds())}"

    __str__ = __repr__

    def offset_seconds(self) -> int:
        """Return the location's offset east of UTC at this instant."""
        offset = self.moment.utcoffset() or timedelta(0)
        return int(offset.total_seconds())

    def __bool__(self) -> bool:
        """Report whether this is not the zero time, per the truth table."""
        return self.nanos != ZERO_TIME_NANOS

    def __hash__(self) -> int:
        """Hash on the instant, so two spellings of it agree."""
        return hash(("time", self.nanos))

    def __eq__(self, other: object) -> bool:
        """Compare two times by instant, whatever locations they carry."""
        if isinstance(other, Time):
            return self.nanos == other.nanos
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        """Order two times by instant."""
        if isinstance(other, Time):
            return self.nanos < other.nanos
        return NotImplemented

    def __le__(self, other: object) -> bool:
        """Order two times by instant."""
        if isinstance(other, Time):
            return self.nanos <= other.nanos
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        """Order two times by instant."""
        if isinstance(other, Time):
            return self.nanos > other.nanos
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        """Order two times by instant."""
        if isinstance(other, Time):
            return self.nanos >= other.nanos
        return NotImplemented

    def __add__(self, other: object) -> Time:
        """Offset the instant by a duration, keeping the location."""
        if isinstance(other, Duration):
            return Time(self.nanos + other.nanos, self.zone, self.zone_name)
        return NotImplemented

    __radd__ = __add__

    def __sub__(self, other: object) -> Time | Duration:
        """Subtract a duration for a time, or a time for the duration between."""
        if isinstance(other, Duration):
            return Time(self.nanos - other.nanos, self.zone, self.zone_name)
        if isinstance(other, Time):
            return Duration(self.nanos - other.nanos)
        return NotImplemented


class Record(Mapping[str, Any]):
    """One data row: a frozen mapping whose members are also attributes.

    doc/expressions.md reaches a field three ways -- ``amount``,
    ``THIS.amount`` and ``THIS["odd name"]`` -- and a nested object
    the same two of them.  All three end here.  An empty record is **true**,
    which is the one place the truth table departs from "a container is
    true when it holds something", so ``__bool__`` is written out.

    Attributes:
        fields: The members, in the order the data gave them.

    """

    __slots__ = ("fields",)

    def __init__(self, fields: Mapping[str, Any]) -> None:
        """Hold the members of one row.

        Args:
            fields: The members, in the order they are to be iterated.

        """
        self.fields = dict(fields)

    def __getitem__(self, key: str) -> Any:
        """Return a member by name."""
        return self.fields[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate the member names, in the order the data gave them."""
        return iter(self.fields)

    def __len__(self) -> int:
        """Count the members."""
        return len(self.fields)

    def __bool__(self) -> bool:
        """Report truth, which for a record is being a record at all."""
        return True

    def __hash__(self) -> int:
        """Hash on identity, since the members need not be hashable."""
        return object.__hash__(self)

    def __eq__(self, other: object) -> bool:
        """Compare two records member by member."""
        if isinstance(other, Record):
            return self.fields == other.fields
        return NotImplemented

    def __repr__(self) -> str:
        """Spell the record with its members, as a value rather than a dict."""
        inside = ", ".join(
            f"{name}={starlark_repr(value)}" for name, value in self.fields.items()
        )
        return f"record({inside})"


class FrozenList(Sequence[Any]):
    """A list that cannot be changed: an accumulator, or a data member.

    The mutating methods are not missing -- they are in the method table
    and they raise -- because doc/expressions.md says they "exist but fail
    on a frozen receiver".  A template author who writes ``tags.append(x)``
    should be told the value is frozen, not that lists have no ``append``.

    Attributes:
        items: What the list holds.

    """

    __slots__ = ("items",)

    def __init__(self, items: Iterable[Any] = ()) -> None:
        """Hold the elements.

        Args:
            items: What the list holds, in order.

        """
        self.items = tuple(items)

    def __getitem__(self, index: Any) -> Any:
        """Return one element, or a slice as a plain list."""
        if isinstance(index, slice):
            return list(self.items[index])
        return self.items[index]

    def __len__(self) -> int:
        """Count the elements."""
        return len(self.items)

    def __iter__(self) -> Iterator[Any]:
        """Iterate the elements, in order."""
        return iter(self.items)

    def __bool__(self) -> bool:
        """Report whether the list holds anything."""
        return bool(self.items)

    def __eq__(self, other: object) -> bool:
        """Compare with another list, frozen or not, element by element."""
        if isinstance(other, FrozenList):
            return self.items == other.items
        if isinstance(other, list):
            return list(self.items) == other
        return NotImplemented

    def __hash__(self) -> int:
        """Refuse to hash, as a list of either kind does."""
        raise ExpressionError("unhashable type: list")

    def __add__(self, other: object) -> list[Any]:
        """Concatenate with another list, giving a new, mutable one."""
        if isinstance(other, FrozenList):
            return list(self.items) + list(other.items)
        if isinstance(other, list):
            return list(self.items) + other
        return NotImplemented

    def __radd__(self, other: object) -> list[Any]:
        """Concatenate after another list."""
        if isinstance(other, list):
            return other + list(self.items)
        return NotImplemented

    def __mul__(self, other: object) -> list[Any]:
        """Repeat the list, giving a new, mutable one."""
        if isinstance(other, bool) or not isinstance(other, int):
            return NotImplemented
        return list(self.items) * other

    __rmul__ = __mul__

    def __repr__(self) -> str:
        """Spell the list the way the language does."""
        return starlark_repr(list(self.items))


class FrozenDict(Mapping[Any, Any]):
    """A dict that cannot be changed, for the same reason as a frozen list.

    Attributes:
        entries: What the dict holds, in insertion order.

    """

    __slots__ = ("entries",)

    def __init__(self, entries: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = ()):
        """Hold the entries.

        Args:
            entries: What the dict holds, in the order to iterate them.

        """
        self.entries = dict(entries)

    def __getitem__(self, key: Any) -> Any:
        """Return one value by key."""
        return self.entries[key]

    def __iter__(self) -> Iterator[Any]:
        """Iterate the keys, in insertion order."""
        return iter(self.entries)

    def __len__(self) -> int:
        """Count the entries."""
        return len(self.entries)

    def __bool__(self) -> bool:
        """Report whether the dict holds anything."""
        return bool(self.entries)

    def __eq__(self, other: object) -> bool:
        """Compare with another dict, frozen or not."""
        if isinstance(other, FrozenDict):
            return self.entries == other.entries
        if isinstance(other, dict):
            return self.entries == other
        return NotImplemented

    def __hash__(self) -> int:
        """Refuse to hash, as a dict of either kind does."""
        raise ExpressionError("unhashable type: dict")

    def __repr__(self) -> str:
        """Spell the dict the way the language does."""
        return starlark_repr(dict(self.entries))


class Set:
    """A set that iterates in first-seen order.

    doc/expressions.md#determinism makes the order part of the language:
    ``calc="set"`` keeps distinct values "in first-seen order", and
    a report whose set iterated by hash would lay out differently
    on two machines.  A dict with ignored values is exactly that set,
    so that is what this holds.

    Attributes:
        members: The elements, as the keys of a dict.
        frozen: Whether the mutating methods refuse.

    """

    __slots__ = ("frozen", "members")

    def __init__(self, items: Iterable[Any] = (), *, frozen: bool = False) -> None:
        """Hold the distinct elements, in the order they first appear.

        Args:
            items: The elements, with repeats allowed.
            frozen: Whether this set may be changed.

        """
        self.members: dict[Any, None] = {}
        for item in items:
            self.members[hashable(item)] = None
        self.frozen = frozen

    def __iter__(self) -> Iterator[Any]:
        """Iterate the elements, in first-seen order."""
        return iter(self.members)

    def __len__(self) -> int:
        """Count the elements."""
        return len(self.members)

    def __contains__(self, item: object) -> bool:
        """Report whether an element is a member."""
        try:
            return item in self.members
        except TypeError:
            return False

    def __bool__(self) -> bool:
        """Report whether the set holds anything."""
        return bool(self.members)

    def __eq__(self, other: object) -> bool:
        """Compare two sets by membership, not by order."""
        if isinstance(other, Set):
            return set(self.members) == set(other.members)
        return NotImplemented

    def __hash__(self) -> int:
        """Refuse to hash, as a set is not a value a dict may be keyed on."""
        raise ExpressionError("unhashable type: set")

    def __or__(self, other: object) -> Set:
        """Return the union, keeping this set's order first."""
        if isinstance(other, Set):
            return Set([*self.members, *other.members])
        return NotImplemented

    def __and__(self, other: object) -> Set:
        """Return the intersection, in this set's order."""
        if isinstance(other, Set):
            return Set(item for item in self.members if item in other.members)
        return NotImplemented

    def __sub__(self, other: object) -> Set:
        """Return the difference, in this set's order."""
        if isinstance(other, Set):
            return Set(item for item in self.members if item not in other.members)
        return NotImplemented

    def __repr__(self) -> str:
        """Spell the set the way the language does: ``set([1, 2])``."""
        inside = ", ".join(starlark_repr(item) for item in self.members)
        return f"set([{inside}])"


def hashable(item: Any) -> Any:
    """Return a value that may be a set member, or refuse it.

    Args:
        item: The candidate member.

    Raises:
        ExpressionError: The value cannot be hashed.

    """
    if isinstance(item, Hashable):
        try:
            hash(item)
        except (TypeError, ExpressionError):
            raise ExpressionError(f"unhashable type: {type_name(item)}") from None
        return item
    raise ExpressionError(f"unhashable type: {type_name(item)}")


class Namespace:
    """A module: a name, and the members reachable through it.

    ``math`` and ``time`` are these, and so is the ``FINAL`` that
    doc/expressions.md#final describes, which layout builds once
    a scope has ended.  Nothing here is callable itself; the members are.

    Attributes:
        name: What the namespace is called, for diagnostics.
        members: What it holds.

    """

    __slots__ = ("members", "name")

    def __init__(self, name: str, members: Mapping[str, Any]) -> None:
        """Hold a namespace's members under its name.

        Args:
            name: What the namespace is called.
            members: What it holds.

        """
        self.name = name
        self.members = dict(members)

    def __repr__(self) -> str:
        """Spell the namespace the way a module prints."""
        return f"<module {self.name!r}>"

    def __bool__(self) -> bool:
        """Report truth, which for a module is being one."""
        return True


def truthy(value: Any) -> bool:
    """Report whether a value is true, per doc/expressions.md#truth-values.

    Every rule in that table is one of these types' own ``__bool__``,
    including the two that are not Python's: a record is true when
    it exists and a time is true when it is not the zero time.
    The function is here so that ``printwhen`` and its three companions
    name the rule they are applying rather than calling ``bool`` and leaving
    a reader to work out that the table is implemented somewhere else.

    Args:
        value: What the expression produced.

    """
    return bool(value)


# What `type()` answers, for the types whose Python name is
# not the language's.  Everything absent from here answers
# with its class name, which is already right for `int`, `float`,
# `bool`, `list`, `dict`, `tuple`, `bytes` and `range`.
# `time.time` and `time.duration` carry the module they come from,
# which is what the reference answers and what starlark-go's own
# time module names them; the truth table's "time" and "duration"
# are prose.
TYPE_NAMES: Final = {
    type(None): "NoneType",
    str: "string",
    FrozenList: "list",
    FrozenDict: "dict",
    Decimal: "decimal",
    Time: "time.time",
    Duration: "time.duration",
    Record: "record",
    Set: "set",
    Namespace: "module",
}


def type_name(value: Any) -> str:
    """Return the language's name for a value's type.

    Args:
        value: The value to name the type of.

    """
    named = TYPE_NAMES.get(type(value))
    if named is not None:
        return named
    if callable(value):
        return "builtin_function_or_method"
    return type(value).__name__


# Go's shortest float formatting turns to exponent notation when
# the decimal exponent reaches this, which is four orders of magnitude
# earlier than Python's repr does.  See `format_float`.
GO_EXPONENT_LIMIT: Final = 6


def format_float(value: float) -> str:
    """Return a float as ``str`` and ``%s`` write one.

    Go's shortest form, and then a ``.0`` where that came out looking
    like an integer, which is what keeps a float distinguishable from
    an int in a printout.  ``123456.0`` stays itself; ``1e7`` does not
    become ``10000000.0``.

    Args:
        value: The number to write.

    """
    text = go_shortest(value)
    if any(mark in text for mark in ".ein"):
        return text
    return text + ".0"


def go_shortest(value: float) -> str:
    """Return a float in the shortest form that round-trips.

    What doc/expressions.md asks for -- but written Go's way rather than
    Python's, because the two disagree about where to switch to exponent
    notation and a printout carries the difference.  Go turns over at a
    decimal exponent of 6, so ``1234567.0`` is ``1.234567e+06``; Python's
    ``repr`` holds off until 1e16.  The digits themselves are the same
    either way and come from ``repr``, which produces the same shortest
    digits Go's algorithm does.

    Args:
        value: The number to write.

    """
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "+inf" if value > 0 else "-inf"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    mantissa, _, exponent_text = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    point = len(whole) + (int(exponent_text) if exponent_text else 0)
    trimmed = digits.lstrip("0")
    point -= len(digits) - len(trimmed)
    trimmed = trimmed.rstrip("0")
    if not trimmed:
        return f"{sign}0"
    exponent = point - 1
    if exponent < -4 or exponent >= GO_EXPONENT_LIMIT:
        head, tail = trimmed[0], trimmed[1:]
        shown = f"{head}.{tail}" if tail else head
        return f"{sign}{shown}e{'+' if exponent >= 0 else '-'}{abs(exponent):02d}"
    if point <= 0:
        return f"{sign}0.{'0' * -point}{trimmed}"
    if point >= len(trimmed):
        return f"{sign}{trimmed}{'0' * (point - len(trimmed))}"
    return f"{sign}{trimmed[:point]}.{trimmed[point:]}"


# What a quoted string escapes.  Everything printable and non-ASCII
# is left alone: the reference's own output quotes `Š` as itself
# and reaches for `\x` only where the bytes are not a character.
ESCAPES: Final = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def quote(text: str) -> str:
    """Return a string quoted the way the language writes one.

    Double quotes, because that is what ``repr`` and ``%q`` produce
    and what the reference's printouts carry.

    Args:
        text: The string to quote.

    """
    written = ['"']
    for character in text:
        escape = ESCAPES.get(character)
        if escape is not None:
            written.append(escape)
        elif character < " " or character == "\x7f":
            written.append(f"\\x{ord(character):02x}")
        else:
            written.append(character)
    written.append('"')
    return "".join(written)


def starlark_str(value: Any) -> str:
    """Return a value as ``str`` and ``%s`` write it.

    A string is itself and everything else is its ``repr``, which is
    the language's rule and the reason ``str(["a"])`` keeps the quotes
    on the element while ``str("a")`` does not.

    Args:
        value: The value to write.

    """
    if isinstance(value, str):
        return value
    return starlark_repr(value)


def starlark_repr(value: Any) -> str:
    """Return a value as ``repr`` and ``%r`` write it.

    Containers are walked rather than handed to Python's own ``repr``,
    because Python spells a tuple, a float and a nested string in ways
    the language does not.

    Args:
        value: The value to write.

    """
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, float):
        return format_float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, FrozenList):
        value = list(value)
    if isinstance(value, list):
        return "[" + ", ".join(starlark_repr(item) for item in value) + "]"
    if isinstance(value, tuple):
        inside = ", ".join(starlark_repr(item) for item in value)
        return f"({inside},)" if len(value) == 1 else f"({inside})"
    if isinstance(value, FrozenDict):
        value = dict(value)
    if isinstance(value, dict):
        inside = ", ".join(
            f"{starlark_repr(key)}: {starlark_repr(item)}"
            for key, item in value.items()
        )
        return "{" + inside + "}"
    if isinstance(value, range):
        if value.step != 1:
            return f"range({value.start}, {value.stop}, {value.step})"
        if value.start:
            return f"range({value.start}, {value.stop})"
        return f"range({value.stop})"
    if isinstance(value, bytes):
        return "b" + quote(value.decode("utf-8", "surrogateescape"))
    return repr(value)
