"""Subreports: what a host hands a nested builder before it runs.

doc/layout.md#subreports describes a nested builder with its own context:
its own parameters, records, variables, groups and record loop.  The loop
that runs it is the same loop as the report's, in ``loop``; what is here
is everything settled in the **host's** context before the child has one.

* **What it runs.**  A ``template=`` names a document of its own,
  and an ``embedded=`` a layout of the document the node is written in,
  resolved when the template loaded.  The declarations come from the one
  or the other: a document's `parameter`, `records` and `variable` nodes
  sit on its `report`, an embedded layout's on itself.
* **Its style walk** continues outward from its own `layout` through the
  document it is written in: an embedded layout's reaches each layout
  enclosing it, out to the document's own, and a ``template=`` document's
  ends at its own `layout`.  The walk is lexical, so the host band's own
  styles are never part of it.
* **Its records** are the elements of its ``data``, coerced by its own
  `records` as the input file is by the report's, except that a `string`
  member refuses a float.  ``None`` is an empty sequence; text is no
  sequence at all, and neither is anything that cannot be iterated.
* **Its parameters** are bound from the `arg` nodes, each evaluated in
  the host's context and checked against its declared type rather than
  parsed for it; a default fills in for an `arg` that is not there.

Nesting is bounded at :data:`DEPTH`: a layout may invoke itself
and walk a tree, and nothing but the data ends the walk.

"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sr.data import coerce
from sr.errors import BadValue, BuildError, ExpressionError, Location
from sr.expr import FrozenDict, Record, Time, type_name
from sr.expr.builtins import iterate
from sr.layout.context import Context, evaluate
from sr.settle import is_of_type, settle_parameters
from sr.template.model import (
    Arg,
    Embedded,
    Layout,
    Member,
    Parameter,
    Records,
    Report,
    Style,
    Subreport,
    Variable,
    freeze,
)

__all__ = [
    "DEPTH",
    "Target",
    "Walk",
    "arguments",
    "records_of",
    "target_of",
]

# How deep subreports may nest: a 33rd nested invocation is an error.
DEPTH = 32

# A `style` walk: one tuple of nodes per scope, innermost first.
Walk = tuple[tuple[Style, ...], ...]


class Target:
    """What a `subreport` node runs, as the template resolved it.

    Attributes:
        report: The document its layout is written in: the one the node
            is written in for ``embedded=``, the one named for ``template=``.
        nesting: Its layout: an `embedded` node, or a document's `layout`.
        outward: The `style` scopes outward of its layout, innermost first.
        parameters: Its declared parameters, in document order.
        records: Its record declaration.
        variables: Its accumulators, in document order.

    """

    def __init__(
        self,
        report: Report,
        nesting: Layout | Embedded,
        outward: Walk,
        parameters: tuple[Parameter, ...],
        records: Records | None,
        variables: tuple[Variable, ...],
    ) -> None:
        """Hold what a subreport runs.

        Args:
            report: The document its layout is written in.
            nesting: Its layout.
            outward: The `style` scopes outward of it.
            parameters: Its declared parameters.
            records: Its record declaration.
            variables: Its accumulators.

        """
        self.report = report
        self.nesting = nesting
        self.outward = outward
        self.parameters = parameters
        self.records = records
        self.variables = variables


def target_of(report: Report, subreport: Subreport) -> Target:
    """Return what a `subreport` node runs.

    Args:
        report: The document the node is written in.
        subreport: The node.  Validation has checked that it sets
            exactly one of ``embedded=`` and ``template=``, and the
            loader has resolved it, so ``scope`` is set for the first
            and ``report`` holds the loaded document for the second.

    """
    if subreport.report is not None:
        named = subreport.report
        assert named.layout is not None
        return Target(
            named,
            named.layout,
            (),
            named.parameters,
            named.records,
            named.variables,
        )
    assert subreport.scope is not None and report.layout is not None
    embedded = report.layouts[subreport.scope]
    outward: list[tuple[Style, ...]] = []
    for depth in range(len(subreport.scope) - 1, 0, -1):
        outward.append(report.layouts[subreport.scope[:depth]].styles)
    outward.append(report.layout.styles)
    return Target(
        report,
        embedded,
        tuple(outward),
        embedded.parameters,
        embedded.records,
        embedded.variables,
    )


def records_of(
    value: Any, declared: Records | None, where: Location
) -> tuple[Record, ...]:
    """Return a subreport's records: its data's elements, coerced.

    Each element is an object, a record or a dict, and its declared
    members are coerced by the subreport's own `records`; one it does
    not declare is passed through frozen, reachable as ``THIS["name"]``.

    Args:
        value: What the ``data`` expression yielded.
        declared: The subreport's `records` node, where it has one.
        where: The `subreport` node's ``data``, for a diagnostic.

    Raises:
        BuildError: The value is not a sequence, an element is not
            an object, or a member will not coerce.

    """
    if value is None:
        return ()
    refused = BuildError(f"want a sequence of records, got {type_name(value)}", where)
    if isinstance(value, str | bytes | Time):
        raise refused
    try:
        elements = list(iterate(value))
    except ExpressionError:
        raise refused from None
    members = {} if declared is None else {one.name: one for one in declared.members}
    found: list[Record] = []
    for index, element in enumerate(elements):
        if isinstance(element, Record):
            row: Mapping[Any, Any] = element.fields
        elif isinstance(element, dict | FrozenDict):
            row = element
        else:
            raise BuildError(
                f"element {index} is {type_name(element)}, and a record is an object",
                where,
            )
        fields: dict[str, Any] = {}
        for name, item in row.items():
            if not isinstance(name, str):
                raise BuildError(
                    f"element {index} has a key that is {type_name(name)},"
                    " and a record's names are strings",
                    where,
                )
            member = members.get(name)
            if member is None:
                fields[name] = freeze(item)
                continue
            try:
                fields[name] = typed(item, member)
            except BadValue as refusal:
                raise BuildError(
                    f"element {index}: member {name!r}: {refusal}", where
                ) from None
        found.append(Record(fields))
    return tuple(found)


def typed(item: Any, member: Member) -> Any:
    """Return one field of a subreport's element as its member declares.

    The input file's coercion, and one refusal more: a `string` member
    takes no float, since a float the host computed has no one spelling
    as text.

    Args:
        item: The field's value.
        member: Its declaration in the subreport's `records`.

    Raises:
        BadValue: The value is not of that type.

    """
    if member.kind == "string" and isinstance(item, float):
        raise BadValue(f"want text, got {type_name(item)}")
    return coerce(item, member)


def arguments(
    subreport: Subreport,
    parameters: tuple[Parameter, ...],
    names: dict[str, Any],
    context: Context,
) -> dict[str, Any]:
    """Return a subreport's parameter values, from its `arg` nodes.

    Each `arg` is evaluated in the host's context, ``names``, and its
    value must already be of the parameter's declared type.  A parameter
    with no `arg` takes its ``default``, or its ``defaultexpr`` evaluated
    against ``BUILD_TIME`` and the parameters settled before it, as a
    report's does; validation has seen to it that one of them is there.

    Args:
        subreport: The node.
        parameters: What the layout it runs declares.
        names: The host's environment.
        context: The host's report as it stands.

    Raises:
        BuildError: An `arg` would not evaluate or is not of the type,
            or a ``defaultexpr`` failed.

    """
    declared = {one.name: one for one in parameters}
    supplied = {
        one.name: argument(one, declared[one.name], names, context)
        for one in subreport.args
        if one.name in declared
    }
    return settle_parameters(
        parameters,
        supplied,
        context.build_time,
        Location(file=context.file, path=subreport.path),
    )


def argument(
    arg: Arg, parameter: Parameter, names: dict[str, Any], context: Context
) -> Callable[[], Any]:
    """Return what produces one `arg`'s value, when it is called.

    Args:
        arg: The node.
        parameter: The declaration it supplies.
        names: The host's environment.
        context: The host's report as it stands.

    """

    def value() -> Any:
        assert arg.value is not None
        found = evaluate(arg.value, names, arg.path, context, "value")
        if not is_of_type(parameter.kind, found):
            raise BuildError(
                f"the value is {type_name(found)},"
                f" and {parameter.name!r} is declared {parameter.kind}",
                Location(
                    file=context.file,
                    path=arg.path,
                    prop="value",
                    record=context.record_index,
                ),
            )
        return found

    return value
