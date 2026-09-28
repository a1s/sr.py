"""Variables: accumulators, and the scopes that fold and clear them.

doc/expressions.md#iter-and-reset gives each `variable` two scopes:
``iter`` says when its expression is folded in and ``reset`` when
the accumulator is cleared.  The record loop and the eject sequence
decide when a scope's boundary is crossed; this is what happens when
one is.

A reset seeds the accumulator again from ``init``, which is folded in
as the first value of the new scope.  The report scope is never
cleared here: doc/expressions.md#the-report-boundary resets it
after the summary band has read it, and nothing is built after that,
so the reset is nominal.

Every accumulator can be snapshotted, which is what a rollback is:
doc/layout.md#rollback undoes a band's fold before an eject the band
caused and applies it again after, so that no value is counted twice
and none is lost to a reset the eject fired in between.

"""

from __future__ import annotations

from typing import Any

from sr.errors import BuildError, ExpressionError, Location
from sr.expr import Accumulator, make
from sr.layout.context import Context
from sr.template.model import Variable

__all__ = ["Variables"]


class Variables:
    """A report's accumulators, as the report stands.

    Attributes:
        declared: The `variable` nodes, in document order.
        context: The names in scope, whose ``variables`` this keeps current.
        accumulators: The running values, by name.

    """

    def __init__(self, declared: tuple[Variable, ...], context: Context) -> None:
        """Hold a report's variables against its context.

        Args:
            declared: The `variable` nodes, in document order.
            context: The names in scope.

        """
        self.declared = declared
        self.context = context
        self.accumulators: dict[str, Accumulator] = {}

    # -- scopes -----------------------------------------------------------

    def clear(self, scope: str, group: str | None = None) -> None:
        """Reset every variable whose `reset` names this scope.

        Args:
            scope: The scope whose boundary has just been crossed.
            group: The group that broke, for the ``group`` scope.

        """
        for variable in self.declared:
            if not matches(variable.reset, variable.resetgrp, scope, group):
                continue
            if variable.name not in self.accumulators:
                continue
            self.accumulators[variable.name] = self.seeded(variable)
            self.publish()

    def iterate(self, scope: str, group: str | None = None) -> None:
        """Fold every variable whose `iter` names this scope.

        A variable seen for the first time is seeded first, so that every
        name has an accumulator behind it from the report scope onward.

        Args:
            scope: The scope whose boundary has just been crossed.
            group: The group that broke, for the ``group`` scope.

        Raises:
            BuildError: A variable's expression would not evaluate.

        """
        for variable in self.declared:
            if variable.name not in self.accumulators:
                self.accumulators[variable.name] = self.seeded(variable)
                self.publish()
            if not matches(variable.iterate, variable.itergrp, scope, group):
                continue
            accumulator = self.accumulators[variable.name]
            accumulator.fold(self.value(variable, "expr"))
            self.publish()

    # -- rollback ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the state of every accumulator, to restore later."""
        return {
            name: (accumulator, accumulator.snapshot())
            for name, accumulator in self.accumulators.items()
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Put every accumulator back as :meth:`snapshot` found it.

        A reset replaces an accumulator rather than clearing it,
        so the snapshot keeps the object as well as its state.

        Args:
            state: What :meth:`snapshot` returned.

        """
        self.accumulators = {}
        for name, (accumulator, kept) in state.items():
            accumulator.restore(kept)
            self.accumulators[name] = accumulator
        self.publish()

    # -- values -----------------------------------------------------------

    def seeded(self, variable: Variable) -> Accumulator:
        """Return a variable's accumulator, seeded by its ``init``.

        Args:
            variable: The node.

        """
        accumulator = make(variable.calc)
        if variable.init is not None:
            accumulator.fold(self.value(variable, "init"))
        return accumulator

    def value(self, variable: Variable, prop: str) -> Any:
        """Return what one of a variable's expressions evaluates to.

        Args:
            variable: The node.
            prop: ``expr`` or ``init``.

        Raises:
            BuildError: The expression would not evaluate.

        """
        expression = variable.expr if prop == "expr" else variable.init
        assert expression is not None
        names = self.context.environment(0.0, 0.0)
        try:
            return expression.evaluate(names)
        except ExpressionError as failed:
            raise BuildError(
                f"variable {variable.name!r}: {failed}",
                Location(
                    file=self.context.file,
                    path=variable.path,
                    prop=prop,
                    record=self.context.record_index,
                ),
            ) from None

    def publish(self) -> None:
        """Copy the accumulators' values into the names in scope."""
        self.context.variables = {
            name: accumulator.value for name, accumulator in self.accumulators.items()
        }


def matches(declared: str, named: str | None, scope: str, group: str | None) -> bool:
    """Report whether a variable's scope is the one whose boundary fired.

    Args:
        declared: The variable's ``iter`` or ``reset``.
        named: Its ``itergrp`` or ``resetgrp``.
        scope: The scope whose boundary fired.
        group: The group that broke, for the ``group`` scope.

    """
    if declared != scope:
        return False
    return scope != "group" or named == group
