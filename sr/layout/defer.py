"""Deferred evaluation: elements that wait for the end of a scope.

doc/layout.md#deferred-evaluation: a `field` with ``evaltime``
is not evaluated when its band is built.  It is measured from
its placeholder, the band is laid out around that, and the value
replaces the placeholder when the scope the element names ends.
``FINAL`` is what the expression reads from that moment;
every other name it reads where it sat.

Three things carry it from one moment to the other.

* **The snapshot.**  When the band is measured, every name the expression
  references except ``FINAL`` is looked up and kept with the element.
  The compiler already reduced the expression to a function of exactly
  those names, so this costs a lookup per name and no analysis, and it
  is per element rather than per band: two fields in one footer may sit
  at the same place and name different things.
* **The placeholder's mark.**  It is on the page, translated wherever
  the band was committed, split, or balanced, and its box is the room
  the placeholder reserved.  The resolved text is set inside that box,
  so the translations a mark went through never have to be replayed.
* **The register.**  A deferral is registered when its band is placed,
  not when it is measured.  A measurement that is thrown away leaves
  nothing behind: a header's reservation, a keep-together lookahead,
  and a band measured again after an eject are all thrown away.
  A scope's end resolves its own deferrals in the order they were placed.

"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sr.expr import Expression, Namespace
from sr.layout.context import Context
from sr.printout.model import Mark
from sr.printout.model import Xref as XrefMark
from sr.template.model import EVALTIME_SCOPES, Barcode, Element, Field, Xref

__all__ = [
    "COLUMN",
    "PAGE",
    "Deferral",
    "Register",
    "Scope",
    "Waiting",
    "final",
    "find",
    "scope_of",
    "snapshot",
    "swap",
    "waits_for",
]

# A scope a deferral waits for: ``report``, ``page``, or ``column`` with
# no name, or ``group`` with the group's.  A group may be called `page`,
# and `evaltime="page"` still means the page, so the two are kept apart.
Scope = tuple[str, str | None]

# The two scopes an eject ends.
COLUMN: Scope = ("column", None)
PAGE: Scope = ("page", None)


def scope_of(evaltime: str) -> Scope:
    """Return the scope an ``evaltime`` names.

    Args:
        evaltime: The property as written, which validation has checked.

    """
    if evaltime in EVALTIME_SCOPES:
        return (evaltime, None)
    return ("group", evaltime)


def waits_for(elements: Sequence[Element | Xref], scope: Scope) -> bool:
    """Return whether an element among these waits for a scope.

    This reads the template, not a band that was built,
    so an element counts whether or not its band would print it.
    The children of an `xref` are among the elements.

    Args:
        elements: A band's elements, in paint order.
        scope: The scope.

    """
    for element in elements:
        if isinstance(element, Xref):
            if waits_for(element.elements, scope):
                return True
        elif isinstance(element, Field | Barcode):
            evaltime = element.evaltime
            if evaltime is not None and scope_of(evaltime) == scope:
                return True
    return False


def snapshot(expression: Expression, names: dict[str, Any]) -> dict[str, Any]:
    """Return the values a deferred expression reads where it sits.

    ``FINAL`` is not among them: it is bound when the scope ends.
    A name the environment does not hold is left out, so that evaluating
    the expression later fails on it as evaluating it now would have.

    Args:
        expression: The element's ``expr``.
        names: The environment the band is measured in.

    """
    return {
        name: names[name]
        for name in expression.names
        if name != "FINAL" and name in names
    }


def final(context: Context) -> Namespace:
    """Return the ``FINAL`` a scope that is ending binds.

    doc/expressions.md#final holds the predefined variables and
    the accumulators, as they stand.  ``VERTICAL_POSITION`` and
    ``VERTICAL_SPACE`` describe a band being measured, and none is
    when a scope ends, so validation refuses them and they are not here.

    Args:
        context: The report as the scope ends.

    """
    return Namespace("FINAL", {**context.variables, **context.predefined()})


@dataclass(frozen=True, eq=False)
class Deferral:
    """A deferred element, measured from its placeholder.

    Each is one placement of the element, so two are equal only
    when they are the same object, and hashing one never looks
    at its snapshot, which is a dict.

    Attributes:
        element: The node.
        scope: The scope whose end it waits for.
        names: The snapshot: every name its ``expr`` reads,
            as the band was measured, except ``FINAL``.
        record: The record the band was built for, for a diagnostic.
        path: Where its mark is among the band's marks: an index,
            and more indices where the mark is inside an `xref`'s.

    """

    element: Field
    scope: Scope
    names: dict[str, Any]
    record: int | None
    path: tuple[int, ...] = ()

    def at(self, path: tuple[int, ...]) -> Deferral:
        """Return the deferral with its mark somewhere else.

        Args:
            path: The new path.

        """
        return replace(self, path=path)


@dataclass(frozen=True)
class Waiting:
    """A deferral on a page, waiting for its scope to end.

    Attributes:
        deferral: The element.
        page: The page it is on, from 0, in the order the pages were made.
        path: Where its mark is among that page's marks.
        order: How many deferrals were placed before it.

    """

    deferral: Deferral
    page: int
    path: tuple[int, ...]
    order: int


class Register:
    """The deferrals placed and not resolved yet.

    They are kept apart by scope, so that a scope's end looks only
    at its own.  A ``report`` deferral waits for the whole report,
    and a report with a group break on every record would otherwise
    pass over all of them at every break.

    """

    def __init__(self) -> None:
        """Start with nothing waiting."""
        self.waiting: dict[Scope, list[Waiting]] = {}
        self.placed = 0

    def __len__(self) -> int:
        """Return how many deferrals are waiting."""
        return sum(len(waiting) for waiting in self.waiting.values())

    def add(self, deferral: Deferral, page: int, start: int) -> None:
        """Register a deferral its band has just placed.

        Args:
            deferral: The element, its path relative to its band's marks.
            page: The page the band went on.
            start: Where the band's first mark is among the page's.

        """
        first, *rest = deferral.path
        path = (start + first, *rest)
        waiting = Waiting(deferral, page, path, self.placed)
        self.waiting.setdefault(deferral.scope, []).append(waiting)
        self.placed += 1

    def due(self, scopes: Sequence[Scope] | None) -> list[Waiting]:
        """Take every deferral waiting for one of these scopes.

        They come back in the order they were placed, across scopes as
        well as within each.

        Args:
            scopes: The scopes that have ended, or ``None`` for all
                of them, which is the end of the report.

        """
        ended = list(self.waiting) if scopes is None else scopes
        taken = [self.waiting.pop(scope, []) for scope in ended]
        return list(heapq.merge(*taken, key=lambda one: one.order))


def find(marks: Sequence[Mark], path: tuple[int, ...]) -> Mark:
    """Return the mark at a path, looking inside `xref` marks.

    Args:
        marks: The marks the first index is into.
        path: The indices.

    """
    first, *rest = path
    mark = marks[first]
    if not rest:
        return mark
    assert isinstance(mark, XrefMark)
    return find(mark.marks, tuple(rest))


def swap(marks: list[Mark], path: tuple[int, ...], new: Mark) -> None:
    """Replace the mark at a path.

    An `xref` holding it is replaced by a copy holding the new one,
    since an xref's marks are a tuple.  Nothing else moves, and
    the marks keep the order they are painted in.

    Args:
        marks: The marks the first index is into, changed in place.
        path: The indices.
        new: The replacement.

    """
    first, *rest = path
    if rest:
        holder = marks[first]
        assert isinstance(holder, XrefMark)
        inner = list(holder.marks)
        swap(inner, tuple(rest), new)
        new = replace(holder, marks=tuple(inner))
    marks[first] = new
