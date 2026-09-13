"""The twelve ways a variable folds values, and what an empty one reads as.

doc/expressions.md#calc is a table of twelve accumulators, and this
is that table.  What is here is the fold and nothing else: when a value
is folded and when the accumulator is cleared are `iter` and `reset`,
which belong to the record loop and arrive in layout.  The split is
the same one the value model draws everywhere -- ``sum`` over decimals
staying exact and ``avg`` quantizing like division are properties of the
values, and the scope they are accumulated over is a property of the report.

Three details are the ones worth reading twice.

* **An empty accumulator is not zero.**  ``sum`` of nothing is ``None``,
  which is what keeps "no rows" distinguishable from "rows summing to
  zero" and what makes ``total_amount or 0`` the documented spelling.
  Only ``count`` reads as a number, and only ``list``, ``set`` and
  ``chain`` read as something empty.

* **``std`` and ``var`` are sample statistics**, dividing by *n*-1,
  so a single value gives ``None`` as well as no values at all.

* **Only three of them retain what they were given.**  The rest are
  incremental and hold a fixed number of fields whatever the data does,
  which is what lets a report over a million records accumulate a total.

Every accumulator can also be snapshotted and restored, because
doc/expressions.md#ordering-against-section-printing requires it:
a detail that does not fit has its fold rolled back and reapplied
after the eject, so that a deferred row is not counted twice.

"""

from __future__ import annotations

from typing import Any, Final

from sr.errors import ExpressionError
from sr.expr.values import Decimal, FrozenList, Set, type_name

__all__ = ["CALCS", "Accumulator", "make"]


class Accumulator:
    """One variable's running value.

    Subclasses hold whatever their fold needs and answer :attr:`value`
    with what an expression reading the variable sees.

    """

    __slots__ = ()

    def fold(self, value: Any) -> None:
        """Take one value into the accumulator.

        Args:
            value: What the variable's ``expr`` produced.

        """
        raise NotImplementedError

    @property
    def value(self) -> Any:
        """Return what an expression reading this variable sees."""
        raise NotImplementedError

    def clear(self) -> None:
        """Empty the accumulator, as a `reset` does."""
        raise NotImplementedError

    def snapshot(self) -> Any:
        """Return the accumulator's state, to be restored later."""
        raise NotImplementedError

    def restore(self, state: Any) -> None:
        """Put the accumulator back to a state it was in.

        Args:
            state: What :meth:`snapshot` returned.

        """
        raise NotImplementedError


class First(Accumulator):
    """The first value folded since the last reset.

    Attributes:
        held: The value, once there is one.
        seen: Whether anything has been folded.

    """

    __slots__ = ("held", "seen")

    def __init__(self) -> None:
        """Start empty."""
        self.held: Any = None
        self.seen = False

    def fold(self, value: Any) -> None:
        """Keep the value if it is the first."""
        if not self.seen:
            self.held = value
            self.seen = True

    @property
    def value(self) -> Any:
        """Return the first value, or ``None``."""
        return self.held

    def clear(self) -> None:
        """Forget the value."""
        self.held, self.seen = None, False

    def snapshot(self) -> Any:
        """Return the value and whether there is one."""
        return (self.held, self.seen)

    def restore(self, state: Any) -> None:
        """Put back a snapshot."""
        self.held, self.seen = state


class Last(First):
    """The most recent value folded."""

    __slots__ = ()

    def fold(self, value: Any) -> None:
        """Keep the value, replacing whatever was there."""
        self.held = value
        self.seen = True


class Count(Accumulator):
    """How many values have been folded.

    Attributes:
        total: The count, which starts at zero rather than at nothing.

    """

    __slots__ = ("total",)

    def __init__(self) -> None:
        """Start at zero."""
        self.total = 0

    def fold(self, value: Any) -> None:
        """Count one more value, whatever it is."""
        self.total += 1

    @property
    def value(self) -> int:
        """Return the count, which is zero when nothing was folded."""
        return self.total

    def clear(self) -> None:
        """Go back to zero."""
        self.total = 0

    def snapshot(self) -> Any:
        """Return the count."""
        return self.total

    def restore(self, state: Any) -> None:
        """Put back a count."""
        self.total = state


class Sum(Accumulator):
    """The sum of the values folded, exact where they are exact.

    Attributes:
        total: The running sum, or ``None`` while nothing has been folded.
        seen: How many values have been folded, which `avg` divides by.

    """

    __slots__ = ("seen", "total")

    def __init__(self) -> None:
        """Start empty."""
        self.total: Any = None
        self.seen = 0

    def fold(self, value: Any) -> None:
        """Add one value to the running total."""
        self.total = value if self.total is None else self.total + value
        self.seen += 1

    @property
    def value(self) -> Any:
        """Return the sum, or ``None`` when nothing was folded."""
        return self.total

    def clear(self) -> None:
        """Forget the total."""
        self.total, self.seen = None, 0

    def snapshot(self) -> Any:
        """Return the total and the count behind it."""
        return (self.total, self.seen)

    def restore(self, state: Any) -> None:
        """Put back a snapshot."""
        self.total, self.seen = state


class Average(Sum):
    """The mean of the values folded, quantized the way division is."""

    __slots__ = ()

    @property
    def value(self) -> Any:
        """Return the mean, or ``None`` when nothing was folded.

        A decimal total divided by an int count is a decimal quantized
        to six places, because that is what ``/`` does; anything else
        is float division, for the same reason.

        """
        if self.total is None:
            return None
        return self.total / self.seen


class Extremum(Accumulator):
    """The largest or the smallest value folded.

    Attributes:
        held: The extreme value so far, or ``None``.
        keep_larger: Whether this is ``max`` rather than ``min``.

    """

    __slots__ = ("held", "keep_larger", "seen")

    def __init__(self, *, keep_larger: bool) -> None:
        """Start empty.

        Args:
            keep_larger: Whether to keep the larger of two values.

        """
        self.held: Any = None
        self.seen = False
        self.keep_larger = keep_larger

    def fold(self, value: Any) -> None:
        """Keep the value if it is more extreme than what is held."""
        if not self.seen:
            self.held, self.seen = value, True
        elif (value > self.held) if self.keep_larger else (value < self.held):
            self.held = value

    @property
    def value(self) -> Any:
        """Return the extreme value, or ``None``."""
        return self.held

    def clear(self) -> None:
        """Forget the value."""
        self.held, self.seen = None, False

    def snapshot(self) -> Any:
        """Return the value and whether there is one."""
        return (self.held, self.seen)

    def restore(self, state: Any) -> None:
        """Put back a snapshot."""
        self.held, self.seen = state


class Spread(Accumulator):
    """The sample variance of the values folded, or its square root.

    Welford's recurrence rather than a sum of squares, so that
    the answer does not lose its significant digits to cancellation
    when the values are large and close together.  Both are floats,
    as doc/expressions.md#calc says, and both are ``None`` for fewer
    than two values, because a sample of one has no spread.

    Attributes:
        seen: How many values have been folded.
        mean: Their running mean.
        square: The running sum of squared deviations.
        root: Whether this is ``std`` rather than ``var``.

    """

    __slots__ = ("mean", "root", "seen", "square")

    def __init__(self, *, root: bool) -> None:
        """Start empty.

        Args:
            root: Whether to answer with the standard deviation.

        """
        self.seen = 0
        self.mean = 0.0
        self.square = 0.0
        self.root = root

    def fold(self, value: Any) -> None:
        """Take one value into the running mean and deviation."""
        number = as_float(value)
        self.seen += 1
        delta = number - self.mean
        self.mean += delta / self.seen
        self.square += delta * (number - self.mean)

    @property
    def value(self) -> float | None:
        """Return the spread, or ``None`` for fewer than two values."""
        if self.seen < 2:
            return None
        variance = self.square / (self.seen - 1)
        return variance**0.5 if self.root else variance

    def clear(self) -> None:
        """Forget everything folded."""
        self.seen, self.mean, self.square = 0, 0.0, 0.0

    def snapshot(self) -> Any:
        """Return the three running fields."""
        return (self.seen, self.mean, self.square)

    def restore(self, state: Any) -> None:
        """Put back a snapshot."""
        self.seen, self.mean, self.square = state


def as_float(value: Any) -> float:
    """Return a value as the float a statistic is computed in.

    A decimal converts here, which is the one place the engine does that
    without being asked: doc/expressions.md#calc says ``std`` and ``var``
    produce floats, so the conversion is the specification's rather than
    an accident of arithmetic.

    Args:
        value: What the variable's ``expr`` produced.

    Raises:
        ExpressionError: The value is not a number.

    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ExpressionError(f"std and var want numbers, got {type_name(value)}")
    return float(value)


class Collecting(Accumulator):
    """The base of the three accumulators that retain what they are given.

    Attributes:
        items: What has been folded.

    """

    __slots__ = ("items",)

    def __init__(self) -> None:
        """Start empty."""
        self.items: list[Any] = []

    def fold(self, value: Any) -> None:
        """Add one value to the end."""
        self.items.append(value)

    def clear(self) -> None:
        """Forget the values."""
        self.items = []

    def snapshot(self) -> Any:
        """Return a copy of the values."""
        return list(self.items)

    def restore(self, state: Any) -> None:
        """Put back a snapshot."""
        self.items = list(state)


class Listing(Collecting):
    """Every value folded, in order."""

    __slots__ = ()

    @property
    def value(self) -> FrozenList:
        """Return the values as a frozen list."""
        return FrozenList(self.items)


class Distinct(Collecting):
    """The distinct values folded, in first-seen order."""

    __slots__ = ()

    @property
    def value(self) -> Set:
        """Return the distinct values as a frozen set."""
        return Set(self.items, frozen=True)


class Chain(Collecting):
    """The values folded, each of which is a sequence, concatenated."""

    __slots__ = ()

    @property
    def value(self) -> FrozenList:
        """Return the concatenation as a frozen list."""
        return FrozenList(self.items)

    def fold(self, value: Any) -> None:
        """Add the elements of one sequence to the end.

        Raises:
            ExpressionError: The value is not a sequence.

        """
        if isinstance(value, str | bytes) or not hasattr(value, "__iter__"):
            raise ExpressionError(
                f"chain wants a sequence to concatenate, got {type_name(value)}"
            )
        self.items.extend(value)


# The `calc` enumeration of doc/template.md#enumerations, and what
# each one builds.  A `calc` this table does not have is a validation
# error rather than a default, which is why the mapping is the whole
# of the knowledge about which names are legal.
CALCS: Final[dict[str, Any]] = {
    "first": First,
    "last": Last,
    "count": Count,
    "sum": Sum,
    "avg": Average,
    "min": lambda: Extremum(keep_larger=False),
    "max": lambda: Extremum(keep_larger=True),
    "std": lambda: Spread(root=True),
    "var": lambda: Spread(root=False),
    "list": Listing,
    "set": Distinct,
    "chain": Chain,
}


def make(calc: str) -> Accumulator:
    """Return a fresh accumulator for one ``calc`` mode.

    Args:
        calc: The mode, as the template spelled it.

    Raises:
        ExpressionError: There is no such mode.

    """
    build = CALCS.get(calc)
    if build is None:
        raise ExpressionError(
            f"unknown calc {calc!r}; expected one of {', '.join(sorted(CALCS))}"
        )
    accumulator: Accumulator = build()
    return accumulator
