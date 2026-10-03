"""The names an expression in a band can see.

doc/expressions.md#names-in-scope gives the order, first match wins:
the predefined variables, then the modules and builtins, then
`parameter` names, then `variable` names, then the declared fields
of the current record.  The compiler supplies the second of those,
so what is here is the other four and the order between them --
a record field shadowed by a variable of the same name is reachable
only as ``THIS.<name>``, which falls out of building the mapping
in that order.

Two of the predefined names are not constant for the whole band.
``VERTICAL_POSITION`` and ``VERTICAL_SPACE`` describe the frame
the band is being tried against, so they are supplied at measurement
rather than held here, and doc/layout.md#cost is why: a band that
reads either is not cached.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sr.errors import BuildError, ExpressionError, Location, NodePath
from sr.expr import Expression, Record, Time, truthy

__all__ = ["Context", "condition", "evaluate", "evaluate_at"]


@dataclass
class Context:
    """Everything an expression may read, as the report stands now.

    Attributes:
        parameters: The report's parameters, by name.
        variables: The accumulators, by name, as they stand.
        record: The current record, or ``None`` before the first one.
        item_number: 1-based index of that record in the data.
        data_count: How many records there are.
        report_count: Detail sections printed since the report began.
        page_count: Detail sections printed since the page began.
        column_count: Detail sections printed since the column began.
        page_number: The current page, 1-based.
        column_number: The current column, 1-based.
        build_time: When this run started.
        group_counts: Each group's ``X_COUNT``: detail sections printed
            since the group last began.
        group_pages: Each group's ``X_PAGE_NUMBER``: the page counted
            from the one the group last began on, from 1.
        file: The template, for a diagnostic.

    """

    parameters: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    record: Record | None = None
    item_number: int = 0
    data_count: int = 0
    report_count: int = 0
    page_count: int = 0
    column_count: int = 0
    page_number: int = 1
    column_number: int = 1
    build_time: Time | None = None
    group_counts: dict[str, int] = field(default_factory=dict)
    group_pages: dict[str, int] = field(default_factory=dict)
    file: str | None = None

    @property
    def record_index(self) -> int | None:
        """Return the current record's 0-based index, for a diagnostic.

        ``ITEM_NUMBER`` is 1-based and is 0 before the first record,
        which is where a header or a title is built; there the index
        a diagnostic wants is no index at all.

        """
        return self.item_number - 1 if self.item_number else None

    def predefined(self) -> dict[str, Any]:
        """Return the predefined variables and their current values.

        A group named ``X`` adds ``X_COUNT`` and ``X_PAGE_NUMBER``,
        per doc/template.md#group.

        """
        found: dict[str, Any] = {
            "THIS": self.record,
            "ITEM_NUMBER": self.item_number,
            "DATA_COUNT": self.data_count,
            "REPORT_COUNT": self.report_count,
            "PAGE_COUNT": self.page_count,
            "COLUMN_COUNT": self.column_count,
            "PAGE_NUMBER": self.page_number,
            "COLUMN_NUMBER": self.column_number,
            "BUILD_TIME": self.build_time,
        }
        for name, count in self.group_counts.items():
            found[f"{name}_COUNT"] = count
        for name, page in self.group_pages.items():
            found[f"{name}_PAGE_NUMBER"] = page
        return found

    def environment(self, position: float, space: float) -> dict[str, Any]:
        """Return every name in scope, in resolution order.

        Later keys win, so the mapping is built from the weakest source
        to the strongest: record fields, then variables, then
        parameters, then the predefined names.

        Args:
            position: ``VERTICAL_POSITION``, the distance from the top
                of the frame to where the band being measured begins.
            space: ``VERTICAL_SPACE``, what it has left to grow into.

        """
        names: dict[str, Any] = {}
        if self.record is not None:
            names.update(self.record.fields)
        names.update(self.variables)
        names.update(self.parameters)
        names.update(self.predefined())
        names["VERTICAL_POSITION"] = position
        names["VERTICAL_SPACE"] = space
        return names


def evaluate(
    expression: Expression,
    names: dict[str, Any],
    path: NodePath,
    context: Context,
    prop: str | None = None,
) -> Any:
    """Evaluate one expression, naming the node if it fails.

    Args:
        expression: The compiled expression.
        names: The environment to evaluate against.
        path: The node the expression was written on.
        context: The report as it stands, for the file and the record.
        prop: The property it was written in.

    Raises:
        BuildError: The expression would not evaluate.

    """
    return evaluate_at(
        expression, names, context.file, path, prop, context.record_index
    )


def evaluate_at(
    expression: Expression,
    names: dict[str, Any],
    file: str | None,
    path: NodePath,
    prop: str | None,
    record: int | None,
) -> Any:
    """Evaluate one expression, naming where it was written if it fails.

    :func:`evaluate` names the record the report stands at.
    A deferred expression is evaluated when its scope ends,
    and names the record its band was built for instead.

    Args:
        expression: The compiled expression.
        names: The environment to evaluate against.
        file: The template, as the caller named it.
        path: The node the expression was written on.
        prop: The property it was written in.
        record: The record the diagnostic names.

    Raises:
        BuildError: The expression would not evaluate.

    """
    try:
        return expression.evaluate(names)
    except ExpressionError as failed:
        raise BuildError(
            str(failed),
            Location(file=file, path=path, prop=prop, record=record),
        ) from None


def condition(
    expression: Expression | None,
    names: dict[str, Any],
    path: NodePath,
    context: Context,
    prop: str,
) -> bool:
    """Return whether a condition holds, an absent one holding.

    ``printwhen`` and a `style`'s ``when`` are both this: an expression
    whose value is read by doc/expressions.md#truth-values rather than
    required to be a boolean.

    Args:
        expression: The condition, or ``None`` where none was written.
        names: The environment to evaluate against.
        path: The node it was written on.
        context: The report as it stands.
        prop: Which property it is, for the diagnostic.

    """
    if expression is None:
        return True
    return truthy(evaluate(expression, names, path, context, prop))
