"""What a template needs settled before its bands are laid out.

Three things, each the same for the report a command line builds and
for a [subreport](doc/layout.md#subreports) a band runs: the values
of its parameters, the bytes of its `data` nodes, and its resolved
fonts.  They live here, below both :mod:`sr.api` and :mod:`sr.layout`,
because both need them: the command line's build settles the report's,
and the layout settles a subreport's when it runs.

A parameter's value comes from one of three places, in this order:
something supplied for it, its ``default``, its ``defaultexpr``.
What is supplied differs.  A report's values arrive from ``--param``
as text and are parsed per the declared ``type``; a subreport's arrive
from its `arg` nodes as values, and are checked against the ``type``
rather than parsed for it, per doc/template.md#arg.  A ``defaultexpr``
is checked the same way, since it too produces a value rather than text.

"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from sr.errors import (
    BadValue,
    BuildError,
    ExpressionError,
    FontError,
    Location,
    UnknownParameter,
)
from sr.expr import Decimal, FrozenList, Record, Time, evaluate, type_name
from sr.fonts.resolve import Resolution, Resolver
from sr.template.model import Blob, Parameter, Report, parse_text

__all__ = [
    "as_bytes",
    "blob_bytes",
    "declared_parameters",
    "is_of_type",
    "parameter_values",
    "resolve_fonts",
    "settle_parameters",
]


def is_of_type(kind: str, value: Any) -> bool:
    """Report whether a value is of a declared parameter type.

    The check is exact, as doc/template.md#parameter-values-as-text
    asks of a value that is not text: an `int` is not a `decimal`,
    and a `bool` is not an `int` although Python says it is.
    A `date` and a `datetime` are both a time; a `list` is a list,
    frozen or not, and not a tuple; an `object` is a record, and not a
    dict literal, which has no attribute access.  ``None`` is of no type.

    Args:
        kind: One of the declared types.
        value: The value.

    """
    if kind == "string":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "decimal":
        return isinstance(value, Decimal)
    if kind == "float":
        return isinstance(value, float)
    if kind == "bool":
        return isinstance(value, bool)
    if kind in ("date", "datetime"):
        return isinstance(value, Time)
    if kind == "list":
        return isinstance(value, list | FrozenList)
    if kind == "object":
        return isinstance(value, Record)
    return False


def parameter_values(
    report: Report, params: dict[str, str], built: Time | None = None
) -> dict[str, Any]:
    """Return every parameter's value, refusing one that has none.

    A ``--param`` wins over a ``default``, and a ``default`` over
    a ``defaultexpr``.  A parameter with none of the three is an
    error naming it: the report would otherwise be built over a value
    nothing supplied, and nothing in the document would say so.

    Args:
        report: The template.
        params: The ``--param`` values, by name.
        built: The run's ``BUILD_TIME``.

    Raises:
        UnknownParameter: A name is not declared.
        BuildError: A value is missing, will not parse,
            or is not of the declared type.

    """
    declared = declared_parameters(report, params)
    supplied = {name: parsing(declared[name], text) for name, text in params.items()}
    return settle_parameters(
        report.parameters, supplied, built, Location(file=report.file)
    )


def declared_parameters(
    report: Report,
    params: Iterable[str],
) -> dict[str, Parameter]:
    """Return what a template declares, by name, refusing a name it does not.

    A value for a parameter that is not declared would go nowhere,
    and the report would build with the default in its place.
    ``build`` and ``validate`` both refuse it here, so that
    the two say the same thing about it.

    Args:
        report: The template.
        params: The names a ``--param`` was given for.

    Raises:
        UnknownParameter: A name is not declared.

    """
    declared = {one.name: one for one in report.parameters}
    for name in params:
        if name not in declared:
            known = ", ".join(sorted(declared)) or "none"
            raise UnknownParameter(
                f"--param {name}: the template declares no such parameter; "
                f"it declares: {known}"
            )
    return declared


def parsing(parameter: Parameter, text: str) -> Callable[[], Any]:
    """Return what parses a ``--param`` value, when it is called.

    Args:
        parameter: The declaration.
        text: The value as the command line spelled it.

    """

    def parsed() -> Any:
        try:
            return parse_text(parameter.kind, text, parameter.format)
        except BadValue as refused:
            raise BuildError(f"--param {parameter.name}: {refused}") from None

    return parsed


def settle_parameters(
    declared: tuple[Parameter, ...],
    supplied: Mapping[str, Callable[[], Any]],
    built: Time | None,
    where: Location,
) -> dict[str, Any]:
    """Return every declared parameter's value, in declaration order.

    A ``defaultexpr`` is evaluated against the parameters settled
    before it and against ``BUILD_TIME``, which is the one predefined
    name that has a value this early: no record has been read,
    so every other one describes a report that has not started.

    Args:
        declared: The `parameter` nodes, in document order.
        supplied: What supplies a value, by parameter name: a callable,
            so that each is produced in declaration order and
            an error names the first parameter that fails.
        built: The run's ``BUILD_TIME``.
        where: The file, and for a subreport the `subreport` node,
            that a diagnostic about a ``defaultexpr`` names.

    Raises:
        BuildError: A value is missing, or a ``defaultexpr`` would
            not evaluate or is not of the declared type.

    """
    found: dict[str, Any] = {}
    constants: dict[str, Any] = {"BUILD_TIME": built} if built is not None else {}
    for one in declared:
        here = where if where.path is not None else Location(where.file, one.path)
        if one.name in supplied:
            found[one.name] = supplied[one.name]()
        elif one.value is not None:
            found[one.name] = one.value
        elif one.defaultexpr is not None:
            try:
                value = one.defaultexpr.evaluate({**constants, **found})
            except ExpressionError as refused:
                raise BuildError(f"parameter {one.name!r}: {refused}", here) from None
            if not is_of_type(one.kind, value):
                raise BuildError(
                    f"parameter {one.name!r}: defaultexpr produced"
                    f" {type_name(value)}, and the declared type is {one.kind}",
                    here,
                )
            found[one.name] = value
        else:
            raise BuildError(
                f"parameter {one.name!r} has no value, no default and no defaultexpr",
                here,
            )
    return found


def blob_bytes(report: Report, names: dict[str, Any]) -> dict[str, bytes]:
    """Return the bytes of every `data` node.

    A blob written as ``content`` was decoded when the template loaded;
    one written as ``expr`` is produced now, which is why this needs
    the parameters.

    Args:
        report: The template.
        names: The parameter values its expressions may read.

    Raises:
        BuildError: A blob's expression would not evaluate.

    """
    found: dict[str, bytes] = {}
    for blob in report.data:
        if blob.content is not None:
            found[blob.name] = blob.content
        elif blob.expr is not None:
            found[blob.name] = as_bytes(blob, names, report.file)
    return found


def as_bytes(blob: Blob, names: dict[str, Any], file: str) -> bytes:
    """Return the bytes an ``expr`` blob evaluates to.

    Args:
        blob: The node.
        names: The values its expression may read.
        file: The template, for the diagnostic.

    Raises:
        BuildError: The expression would not evaluate.

    """
    assert blob.expr is not None
    try:
        value = evaluate(blob.expr.source, names)
    except ExpressionError as refused:
        raise BuildError(
            f"data {blob.name!r}: {refused}",
            Location(file=file, path=blob.path, prop="expr"),
        ) from None
    if isinstance(value, bytes):
        return value
    return str(value).encode()


def resolve_fonts(
    report: Report,
    blobs: dict[str, bytes],
    *,
    strict: bool = False,
    resolver: Resolver | None = None,
) -> tuple[tuple[Resolution, ...], Resolver]:
    """Resolve every `font` node a template declares.

    Args:
        report: The template.
        blobs: Its `data` nodes' contents, for a `font data=`.
        strict: Whether ``--strict-fonts`` is set.
        resolver: The chain an earlier document of the same build
            resolved its fonts with, whose host table and substitute
            are the machine's and are not looked for twice.

    Returns:
        The resolutions, in declaration order, and the resolver,
        which carries what enumerating the host had to say.

    Raises:
        BuildError: A font did not resolve.

    """
    if resolver is None:
        resolver = Resolver(basedir=report.basedir, blobs=blobs, strict=strict)
    else:
        resolver.reading(report.basedir, blobs)
    resolved = []
    for font in report.fonts:
        try:
            resolved.append(resolver.resolve(font))
        except FontError as beaten:
            raise BuildError(
                f"font {font.name!r}: {beaten}",
                Location(file=report.file, path=font.path),
            ) from None
    return tuple(resolved), resolver
