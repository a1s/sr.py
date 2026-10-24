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

The records come one of two ways, and the steps are the same for both.
:func:`build` reads them as JSON, as the command line does, and
:func:`build_records` takes them from the caller as Python values,
such as the rows a database query fetched.

"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from sr.data import read_records, records_given
from sr.errors import BuildError, BuildWarning, Diagnostic
from sr.expr import Record, Time
from sr.expr.builtins import parse_time
from sr.expr.golayout import RFC3339
from sr.layout.loop import Build, Builder
from sr.printout.model import Printout
from sr.settle import blob_bytes, parameter_values, resolve_fonts
from sr.template import load as load_template
from sr.template.load import Options as LoadOptions
from sr.template.model import Records

__all__ = ["Options", "Result", "build", "build_records", "build_time_of"]


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
            under ``verbose`` and only where the build looked a typeface
            up on the host at all.  They are the same on every build
            the process runs until a font on the machine changes,
            since the table they come with is kept between builds.

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

    def read(declared: Records | None) -> tuple[Record, ...]:
        return read_records(data, declared) if data is not None else ()

    return applied(template, read, options)


def build_records(
    template: Path | str,
    records: Iterable[Any],
    options: Options | None = None,
) -> Result:
    """Apply a template to records the caller holds and return the printout.

    Each record is a mapping, or an object whose fields have names:
    a named tuple, a dataclass, an object with attributes, or a row
    a database driver fetched, such as SQLAlchemy's ``Row``.
    doc/template.md#records-from-the-library says which of the host's
    types each value may have and what it becomes.

    Args:
        template: The template file.
        records: The records, in order: a list, a query's result,
            or any other iterable, which is read once.
        options: What the run asks for; the defaults otherwise.

    Raises:
        TemplateError: The template did not load or did not validate.
        BuildError: A font did not resolve, a parameter has no value,
            a record is not one or will not coerce, or a band will not fit.

    """

    def read(declared: Records | None) -> tuple[Record, ...]:
        return records_given(records, declared)

    return applied(template, read, options)


def applied(
    template: Path | str,
    read: Callable[[Records | None], tuple[Record, ...]],
    options: Options | None,
) -> Result:
    """Apply a template to the records ``read`` returns.

    The records are read after the fonts resolve, which is the order
    doc/cli.md#sr-build gives, and by the template's `records` node.

    Args:
        template: The template file.
        read: What returns the records, given the `records` node.
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
    (fonts, resolver) = resolve_fonts(report, blobs, strict=asked.strict_fonts)
    records = read(report.records)
    # A font's own warnings stay on its resolution: the builder carries
    # them only for the fonts the document turns out to use.
    warnings = tuple(carried(one) for one in loaded.warnings if one.kind is not None)
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
            resolver=resolver,
        )
    )
    printout = builder.run()
    notes = tuple(one for one in loaded.warnings if one.kind is None)
    # Read after the build, since a subreport resolves its fonts when it runs.
    diagnostics = resolver.diagnostics if asked.verbose else ()
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
