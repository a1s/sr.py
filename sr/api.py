"""The library surface: a template and records in, a printout out.

doc/cli.md opens by saying that the command line decides nothing --
every flag maps to a library option, every diagnostic comes from the
engine.  This is the other side of that sentence.  :func:`build`
takes the options the flags map to, and the command line's own work
is parsing arguments and choosing a stream.

The steps are the ones doc/cli.md#sr-build lists, in the order a
diagnostic reads best: load the template, settle the parameters,
resolve the fonts, read the records, lay them out.  A parameter with
no value is an error here and not at `validate`, which is the one
place the two commands differ about the same template: a check asks
whether the document is well formed, and a build needs the values.

"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from sr.data import read_records
from sr.errors import (
    BadValue,
    BuildError,
    BuildWarning,
    Diagnostic,
    ExpressionError,
    FontError,
    Location,
)
from sr.expr import Time, evaluate
from sr.expr.builtins import parse_time
from sr.expr.golayout import RFC3339
from sr.fonts.resolve import Resolution, Resolver
from sr.layout.loop import Build, Builder
from sr.printout.model import Printout
from sr.template import load as load_template
from sr.template.load import Options as LoadOptions
from sr.template.model import Blob, Report, parse_text

__all__ = ["Options", "Result", "build", "build_time_of"]


@dataclass(frozen=True)
class Options:
    """What a build takes besides the template and the data.

    Attributes:
        params: ``--param`` values, by name, as text.
        build_time: What fixes ``BUILD_TIME``, as RFC 3339.
        strict_fonts: Resolve only fonts the template names by path.
        strict_names: Refuse an unknown node or property.
        accepted: Names to take without a warning.
        allow_overflow: Record an oversized band as a warning.
        verbose: Collect the host font diagnostics, which are
            about the machine rather than the document and are
            asked for rather than produced.

    """

    params: dict[str, str] = field(default_factory=dict)
    build_time: str | None = None
    strict_fonts: bool = False
    strict_names: bool = False
    accepted: frozenset[str] = frozenset()
    allow_overflow: bool = False
    verbose: bool = False


@dataclass(frozen=True)
class Result:
    """What a build produced.

    Attributes:
        printout: The document.
        warnings: What the run had to say about the document,
            which is what the header carries.
        notes: What it had to say about the template,
            which the header does not carry: a diagnostic with
            no [kind](doc/printout.md#header-line) is about
            the document that was written rather than about
            the one that was read, and belongs on standard error.
        diagnostics: What enumerating the host's fonts had to say,
            under ``verbose`` and only where enumeration happened at all.

    """

    printout: Printout
    warnings: tuple[BuildWarning, ...] = ()
    notes: tuple[Diagnostic, ...] = ()
    diagnostics: tuple[str, ...] = ()


def build(
    template: Path | str,
    data: Path | str | TextIO | None = None,
    options: Options | None = None,
) -> Result:
    """Apply a template to data and return the printout.

    Args:
        template: The template file.
        data: The records, as a path or an open stream;
            ``None`` for a template that runs over no records at all.
        options: What the run asks for; the defaults otherwise.

    Raises:
        TemplateError: The template did not load or did not validate.
        BuildError: A font did not resolve, a parameter has no value,
            a record will not coerce, or a band will not fit.

    """
    asked = options or Options()
    loaded = load_template(
        Path(template),
        LoadOptions(accepted=asked.accepted, strict=asked.strict_names),
    )
    report = loaded.require()
    built = build_time_of(asked.build_time)
    parameters = parameter_values(report, asked.params, built)
    blobs = blob_bytes(report, parameters)
    fonts, diagnostics = resolve_fonts(
        report, blobs, strict=asked.strict_fonts, verbose=asked.verbose
    )
    records = read_records(data, report.records) if data is not None else ()
    warnings = tuple(carried(one) for one in loaded.warnings if one.kind is not None)
    for resolution in fonts:
        warnings += resolution.warnings
    builder = Builder(
        Build(
            report=report,
            records=tuple(records),
            parameters=parameters,
            fonts=fonts,
            blobs=blobs,
            built=built,
            strict_fonts=asked.strict_fonts,
            allow_overflow=asked.allow_overflow,
            warnings=warnings,
        )
    )
    printout = builder.run()
    notes = tuple(one for one in loaded.warnings if one.kind is None)
    return Result(printout, printout.warnings, notes, diagnostics)


def build_time_of(given: str | None) -> Time:
    """Return the run's ``BUILD_TIME``, in UTC.

    The zone is not the caller's to choose.  A `--build-time`
    carrying an offset names an instant, and the instant is
    what the run has; reading it anywhere else is what
    ``.in_location`` is for.  The header's `built` field
    is that instant written RFC 3339, so two machines in
    different zones building one template with one
    `--build-time` write one byte sequence.

    Args:
        given: What ``--build-time`` supplied, or ``None`` for now.

    Raises:
        BuildError: The value is not an RFC 3339 timestamp.

    """
    if given is None:
        return Time(time.time_ns())
    try:
        return Time(parse_time(given, RFC3339).nanos)
    except BuildError:
        raise
    except Exception as refused:
        raise BuildError(f"--build-time: not an RFC 3339 time: {given!r}") from refused


def carried(diagnostic: Diagnostic) -> BuildWarning:
    """Return a load warning as the printout header carries it.

    Only a diagnostic that carries one of the four
    [kinds](doc/printout.md#header-line) belongs in the header.
    The rest are about the template rather than about the document --
    a band that collapses to nothing is the example -- and they are
    said on standard error, where the person editing the template is.

    A warning raised while the template was read has no record index,
    because no data had been read when it was found.

    Args:
        diagnostic: What the load had to say.

    """
    where = diagnostic.location
    assert diagnostic.kind is not None
    return BuildWarning(
        diagnostic.kind,
        diagnostic.message,
        node=str(where.path) if where.path else None,
        prop=where.prop,
    )


def parameter_values(
    report: Report, params: dict[str, str], built: Time | None = None
) -> dict[str, Any]:
    """Return every parameter's value, refusing one that has none.

    A ``--param`` wins over a ``default``, and a ``default`` over
    a ``defaultexpr``.  A parameter with none of the three is an
    error naming it: the report would otherwise be built over a value
    nothing supplied, and nothing in the document would say so.

    A ``defaultexpr`` is evaluated against the parameters settled
    before it and against ``BUILD_TIME``, which is the one predefined
    name that has a value this early: the data has not been read,
    so every other one describes a report that has not started.

    Args:
        report: The template.
        params: The ``--param`` values, by name.
        built: The run's ``BUILD_TIME``.

    Raises:
        BuildError: A name is not declared, or a value is missing
            or will not parse.

    """
    declared = {one.name: one for one in report.parameters}
    for name in params:
        if name not in declared:
            known = ", ".join(sorted(declared)) or "none"
            raise BuildError(
                f"--param {name}: the template declares no such parameter; "
                f"it declares: {known}"
            )
    found: dict[str, Any] = {}
    constants: dict[str, Any] = {"BUILD_TIME": built} if built is not None else {}
    for one in report.parameters:
        where = Location(file=report.file, path=one.path)
        if one.name in params:
            try:
                found[one.name] = parse_text(one.kind, params[one.name], one.format)
            except BadValue as refused:
                raise BuildError(f"--param {one.name}: {refused}") from None
        elif one.value is not None:
            found[one.name] = one.value
        elif one.defaultexpr is not None:
            try:
                found[one.name] = one.defaultexpr.evaluate({**constants, **found})
            except ExpressionError as refused:
                raise BuildError(f"parameter {one.name!r}: {refused}", where) from None
        else:
            raise BuildError(
                f"parameter {one.name!r} has no value, no default and no defaultexpr",
                where,
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
    verbose: bool = False,
) -> tuple[tuple[Resolution, ...], tuple[str, ...]]:
    """Resolve every `font` node the template declares.

    Args:
        report: The template.
        blobs: Its `data` nodes' contents, for a `font data=`.
        strict: Whether ``--strict-fonts`` is set.
        verbose: Whether to keep what enumerating the host had to say.

    Raises:
        BuildError: A font did not resolve.

    """
    resolver = Resolver(basedir=report.basedir, blobs=blobs, strict=strict)
    resolved = []
    for font in report.fonts:
        try:
            resolved.append(resolver.resolve(font))
        except FontError as beaten:
            raise BuildError(
                f"font {font.name!r}: {beaten}",
                Location(file=report.file, path=font.path),
            ) from None
    return tuple(resolved), resolver.diagnostics if verbose else ()
