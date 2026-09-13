"""The twelve `calc` modes: what they fold, and what an empty one reads as."""

from __future__ import annotations

from typing import Any

import pytest

from sr.errors import ExpressionError
from sr.expr.calc import CALCS, make
from sr.expr.values import Decimal, FrozenList, Set

# doc/template.md#enumerations lists the `calc` values; a mode
# missing from the engine, or one invented by it, fails here.
DECLARED = "count list set chain first last sum avg min max std var".split()


def folded(calc: str, *values: Any) -> Any:
    """Return what an accumulator reads after folding some values."""
    accumulator = make(calc)
    for value in values:
        accumulator.fold(value)
    return accumulator.value


def test_the_modes_are_exactly_the_ones_the_enumeration_lists() -> None:
    assert set(CALCS) == set(DECLARED)


def test_an_unknown_mode_is_refused_and_names_the_ones_there_are() -> None:
    with pytest.raises(ExpressionError, match="unknown calc 'median'"):
        make("median")


@pytest.mark.parametrize(
    ("calc", "answer"),
    [
        ("first", None),
        ("last", None),
        ("count", 0),
        ("sum", None),
        ("avg", None),
        ("min", None),
        ("max", None),
        ("std", None),
        ("var", None),
    ],
)
def test_an_empty_accumulator_reads_as_the_table_says(
    calc: str, answer: object
) -> None:
    """`sum` of nothing is None, so "no rows" stays distinguishable."""
    assert folded(calc) == answer


def test_an_empty_collection_reads_as_an_empty_one() -> None:
    assert list(folded("list")) == []
    assert list(folded("set")) == []
    assert list(folded("chain")) == []


def test_first_and_last_keep_the_ends() -> None:
    assert folded("first", 1, 2, 3) == 1
    assert folded("last", 1, 2, 3) == 3


def test_first_keeps_a_none_it_was_actually_given() -> None:
    """Folded `None` is a value, and is not the same as nothing folded."""
    accumulator = make("first")
    accumulator.fold(None)
    accumulator.fold(2)
    assert accumulator.value is None


def test_count_counts_values_rather_than_true_ones() -> None:
    assert folded("count", 0, None, "") == 3


def test_sum_over_decimals_stays_exact() -> None:
    total = folded("sum", Decimal("0.10"), Decimal("0.20"))
    assert isinstance(total, Decimal)
    assert str(total) == "0.30"


def test_sum_over_ints_stays_an_int() -> None:
    assert folded("sum", 1, 2, 3) == 6


def test_avg_over_decimals_quantizes_the_way_division_does() -> None:
    mean = folded("avg", Decimal("1"), Decimal("2"))
    assert isinstance(mean, Decimal)
    assert str(mean) == "1.500000"
    assert str(folded("avg", Decimal("1"), Decimal("1"), Decimal("1.000001"))) == (
        "1.000000"
    )


def test_avg_over_ints_is_float_division() -> None:
    assert folded("avg", 1, 2) == 1.5


def test_min_and_max_compare_rather_than_convert() -> None:
    assert folded("min", Decimal("2"), Decimal("1.5")) == Decimal("1.5")
    assert folded("max", 1, Decimal("1.5")) == Decimal("1.5")


def test_std_and_var_are_sample_statistics() -> None:
    """Dividing by n-1, so two values of 1 and 3 have a variance of 2."""
    assert folded("var", 1, 3) == 2.0
    assert folded("std", 1, 3) == pytest.approx(2.0**0.5)


def test_std_and_var_need_two_values() -> None:
    assert folded("std", 1) is None
    assert folded("var", 1) is None


def test_std_and_var_are_floats_even_over_decimals() -> None:
    spread = folded("var", Decimal("1"), Decimal("3"))
    assert isinstance(spread, float)


def test_std_refuses_a_value_that_is_not_a_number() -> None:
    with pytest.raises(ExpressionError, match="want numbers"):
        folded("std", "a")


def test_list_keeps_every_value_in_order_and_frozen() -> None:
    kept = folded("list", 2, 1, 2)
    assert isinstance(kept, FrozenList)
    assert list(kept) == [2, 1, 2]


def test_set_keeps_the_distinct_values_in_first_seen_order() -> None:
    kept = folded("set", "b", "a", "b")
    assert isinstance(kept, Set)
    assert list(kept) == ["b", "a"]
    assert kept.frozen


def test_chain_concatenates_the_sequences_it_is_given() -> None:
    assert list(folded("chain", [1, 2], [3])) == [1, 2, 3]


def test_chain_refuses_a_value_that_is_not_a_sequence() -> None:
    with pytest.raises(ExpressionError, match="wants a sequence"):
        folded("chain", 1)


def test_a_reset_empties_the_accumulator() -> None:
    accumulator = make("sum")
    accumulator.fold(1)
    accumulator.clear()
    assert accumulator.value is None


@pytest.mark.parametrize("calc", sorted(CALCS))
def test_a_fold_can_be_rolled_back_and_reapplied(calc: str) -> None:
    """A detail that does not fit is deferred, and must not count twice."""
    values: dict[str, Any] = {"chain": [1]}
    value = values.get(calc, 1)
    accumulator = make(calc)
    accumulator.fold(value)
    saved = accumulator.snapshot()
    before = as_comparable(accumulator.value)
    accumulator.fold(value)
    accumulator.restore(saved)
    assert as_comparable(accumulator.value) == before
    accumulator.fold(value)
    twice = make(calc)
    twice.fold(value)
    twice.fold(value)
    assert as_comparable(accumulator.value) == as_comparable(twice.value)


def as_comparable(value: Any) -> Any:
    """Return a value in a form two accumulators can be compared by."""
    if isinstance(value, FrozenList | Set):
        return list(value)
    return value
