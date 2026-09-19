"""The command line of doc/cli.md.

Partial, deliberately.  ``validate``, ``version`` and ``help`` are here;
``build``, ``render`` and ``inspect`` name the milestone that brings them
and exit 1, because a command that printed nothing and exited 0 would be
worse than one that says it is not there.  :data:`COMMANDS` holds what
works, which is also what the differential harness asks before deciding
whether this engine can be compared against the reference yet.

``validate`` is itself partial in one way that matters: it does
not resolve fonts.  doc/cli.md#sr-validate puts a `fonts` section
in the report and a `failures` section under it, and both wait
for M5, which is where font resolution is written.
Everything the document alone settles is checked and reported now.

Flag parsing follows doc/cli.md#flags: one dash or two, the value
attached or separate, and flags after positionals.
``--param`` is the one repeatable flag, and the two mistakes in it
are refused rather than absorbed: a name given twice, and a name
the template does not declare.  The first is a command-line mistake
and is caught before the template is read; the second needs the
template and is caught after.

"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from sr import meta
from sr.errors import BadValue
from sr.expr.values import go_shortest
from sr.template import load as load_template
from sr.template.load import Loaded, Options, levels_of, sections_of
from sr.template.model import Parameter, Report, parse_text

__all__ = ["COMMANDS", "PLANNED", "Usage", "main", "validate", "version"]

# What each command is for, in the order `sr.py help` lists them.
SUMMARY = {
    "build": "template plus data in, PDF or printout out",
    "validate": "check a template without data",
    "render": "printout in, PDF out",
    "inspect": "dump a printout as readable text",
    "version": "print the version",
    "help": "print usage, for one command or for all of them",
}

# The commands this engine does not have yet, and the milestone each
# arrives in.  A command here parses its arguments like any other and
# then says so, rather than failing as though the file were at fault.
PLANNED = {
    "build": "M6",
    "render": "M14",
    "inspect": "M13",
}

# The exit codes of doc/cli.md#exit-codes.
OK = 0
FAILED = 1
USAGE = 2


class Usage(Exception):
    """The command line was wrong, as opposed to the run having failed.

    Carries the message as its text; the caller prints it and exits 2.

    """


@dataclass
class Arguments:
    """One command's arguments, as the parser read them.

    Attributes:
        flags: Every flag given, by its long name, last one winning.
        params: The ``--param`` values, in the order they were given.
        accepted: The ``--accept`` names, which are a set because a name
            given twice asks for exactly what one name asks for.
        positional: What was given without a flag in front of it.

    """

    flags: dict[str, str | bool] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    accepted: set[str] = field(default_factory=set)
    positional: list[str] = field(default_factory=list)


# The flags `validate` takes: the long name, the short one,
# and whether it carries a value.  doc/cli.md gives `build`
# and `render` more; they are declared with the commands
# that use them.
VALIDATE_FLAGS = (
    ("template", "t", True),
    ("strict-fonts", None, False),
    ("strict-names", None, False),
    ("quiet", "q", False),
    ("verbose", "v", False),
)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run one command and return its exit code.

    Args:
        argv: The arguments after the program name; ``sys.argv[1:]``
            when the caller gives none.
        out: Where the report goes; standard output by default.

    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    stream = sys.stdout if out is None else out
    if not arguments:
        print("a command is required; try `sr.py help`", file=sys.stderr)
        return USAGE
    command = arguments[0]
    rest = arguments[1:]
    if command in ("-h", "--help", "help"):
        return help_for(rest, stream)
    if any(one in ("-h", "--help") for one in rest):
        return help_for([command], stream)
    try:
        if command == "version":
            return version(rest, stream)
        if command == "validate":
            return validate(rest, stream)
        if command in PLANNED:
            print(
                f"{command} is not implemented yet; it arrives in {PLANNED[command]}",
                file=sys.stderr,
            )
            return FAILED
    except Usage as refused:
        print(f"{refused}", file=sys.stderr)
        return USAGE
    print(f"unknown command {command!r}; try `sr.py help`", file=sys.stderr)
    return USAGE


def version(arguments: Sequence[str], out: TextIO) -> int:
    """Print the engine's name and version.

    Args:
        arguments: What followed the command; it takes none.
        out: Where to print.

    """
    if arguments:
        raise Usage(f"version takes no arguments, and got {arguments[0]!r}")
    print(meta.engine(), file=out)
    return OK


def help_for(arguments: Sequence[str], out: TextIO) -> int:
    """Print usage, for one command or for all of them.

    Args:
        arguments: The command to describe, where one was named.
        out: Where to print.

    """
    if arguments and arguments[0] in SUMMARY:
        name = arguments[0]
        print(f"sr.py {name}  {SUMMARY[name]}", file=out)
        if name in PLANNED:
            print(f"  not implemented yet; it arrives in {PLANNED[name]}", file=out)
        if name == "validate":
            print("  -t, --template FILE   the template to check", file=out)
            print("  --param NAME=VALUE    a report parameter, repeatable", file=out)
            print("  --strict-fonts        resolve only fonts named by path", file=out)
            print("  --strict-names        refuse an unknown name", file=out)
            print(
                "  --accept NAME         take one name in silence, repeatable", file=out
            )
            print("  -q, --quiet           print nothing on success", file=out)
            print("  -v, --verbose         include host font diagnostics", file=out)
        return OK
    print("usage: sr.py <command> [options]", file=out)
    for name, what in SUMMARY.items():
        planned = f"  (not implemented yet: {PLANNED[name]})" if name in PLANNED else ""
        print(f"  {name:<9}{what}{planned}", file=out)
    return OK


# -- validate ---------------------------------------------------------


def validate(arguments: Sequence[str], out: TextIO) -> int:
    """Load a template, check it, and report what was found.

    Args:
        arguments: What followed the command.
        out: Where the report goes.

    """
    given = parse(arguments, VALIDATE_FLAGS)
    template = given.flags.get("template")
    if template is None:
        if len(given.positional) != 1:
            raise Usage("validate needs a template, as -t or as the one argument")
        template = given.positional[0]
    elif given.positional:
        raise Usage(f"validate takes one template, and got {given.positional[0]!r} too")
    quiet = bool(given.flags.get("quiet"))
    options = Options(
        accepted=frozenset(given.accepted),
        strict=bool(given.flags.get("strict-names")),
    )
    loaded = load_template(Path(str(template)), options)
    if loaded.report is None or loaded.errors:
        for diagnostic in loaded.errors:
            print(diagnostic, file=sys.stderr)
        return FAILED
    failures = parameters_given(loaded.report, given.params)
    if failures:
        for message in failures:
            print(message, file=sys.stderr)
        return FAILED
    if not quiet:
        describe(loaded, str(template), out)
    return OK


def parameters_given(report: Report, params: dict[str, str]) -> list[str]:
    """Check ``--param`` values against what the template declares.

    Args:
        report: The template that was loaded.
        params: The values given, by name.

    """
    declared = {one.name: one for one in report.parameters}
    failures: list[str] = []
    for name, text in params.items():
        parameter = declared.get(name)
        if parameter is None:
            known = ", ".join(sorted(declared)) or "none"
            failures.append(
                f"--param {name}: the template declares no such parameter; "
                f"it declares: {known}"
            )
            continue
        try:
            parse_text(parameter.kind, text, parameter.format)
        except BadValue as refused:
            failures.append(f"--param {name}: {refused}")
    return failures


def describe(loaded: Loaded, file: str, out: TextIO) -> None:
    """Print the report doc/cli.md#sr-validate describes.

    The `fonts` and `failures` sections are not here: both are about
    resolution, which is M5.  Everything else the document alone settles
    is, and `ok` is still the last line, so a ``tail -1`` reads the
    verdict as it is meant to.

    Args:
        loaded: What the load produced.
        file: The template, as the caller named it.
        out: Where to print.

    """
    report = loaded.report
    if report is None:
        return
    print(f"template {file}", file=out)
    print(f"  {headline(report)}", file=out)
    if report.description:
        print(f"  {report.description}", file=out)
    if report.layout is not None:
        paper = report.layout.paper
        print(
            f"  page {number(paper.width)} x {number(paper.height)} pt, margins "
            f"left {number(paper.left)} right {number(paper.right)} "
            f"top {number(paper.top)} bottom {number(paper.bottom)}",
            file=out,
        )
    counts = tally(report)
    if counts:
        print(f"  {counts}", file=out)
    if report.parameters:
        print("parameters", file=out)
        for line in parameter_lines(report.parameters):
            print(f"  {line}", file=out)
    listed = subreport_lines(report)
    if listed:
        print("subreports", file=out)
        for line in listed:
            print(f"  {line}", file=out)
    if loaded.warnings:
        print("warnings", file=out)
        for warning in loaded.warnings:
            print(f"  {warning}", file=out)
    print("ok", file=out)


def headline(report: Report) -> str:
    """Return the one line that names a report.

    Args:
        report: The template that was loaded.

    """
    parts = ["report"]
    parts.append(f'"{report.name}"' if report.name else "(unnamed)")
    if report.version:
        parts.append(f"version {report.version}")
    if report.author:
        parts.append(f"by {report.author}")
    return " ".join(parts)


def tally(report: Report) -> str:
    """Return the line that counts what a template holds.

    Args:
        report: The template that was loaded.

    """
    layout = report.layout
    columns = layout.columns.count if layout is not None and layout.columns else 0
    groups = 0 if layout is None else len(levels_of(layout)) - 1
    members = len(report.records.members) if report.records is not None else 0
    counted = (
        (columns, "column", "columns"),
        (groups, "group", "groups"),
        (members, "member", "members"),
        (len(report.variables), "variable", "variables"),
        (len(report.fonts), "font", "fonts"),
        (len(report.data), "data blob", "data blobs"),
        (len(report.layouts), "embedded layout", "embedded layouts"),
    )
    return ", ".join(
        f"{count} {one if count == 1 else many}"
        for count, one, many in counted
        if count
    )


def parameter_lines(parameters: tuple[Parameter, ...]) -> list[str]:
    """Return one padded line per parameter.

    Args:
        parameters: What the template declares, in document order.

    """
    names = max(len(one.name) for one in parameters)
    kinds = max(len(one.kind) for one in parameters)
    lines = []
    for one in parameters:
        if one.default is not None:
            source = f'default "{one.default}"'
        elif one.defaultexpr is not None:
            source = "defaultexpr"
        else:
            source = "required"
        tail = f"{source}  prompt" if one.prompt else source
        lines.append(f"{one.name:<{names}}  {one.kind:<{kinds}}  {tail}")
    return lines


def subreport_lines(report: Report) -> list[str]:
    """Return one line per ``subreport`` node, by path.

    Args:
        report: The template that was loaded.

    """
    lines = []
    for band in sections_of(report):
        for subreport in band.subreports:
            names = subreport.template or f"embedded {subreport.embedded!r}"
            how = " inline" if subreport.inline else ""
            lines.append(f"{subreport.path}  {names}{how}")
    return lines


def number(value: float) -> str:
    """Return a dimension in the shortest form that round-trips.

    Args:
        value: The value in points.

    """
    return go_shortest(value)


# -- the flag parser --------------------------------------------------


def parse(
    arguments: Sequence[str], flags: tuple[tuple[str, str | None, bool], ...]
) -> Arguments:
    """Read one command's arguments.

    Args:
        arguments: What followed the command.
        flags: Each flag as its long name, its short name,
            and whether it carries a value.

    Raises:
        Usage: A flag is unknown, or is missing the value it takes.

    """
    long = {name: takes for name, _, takes in flags}
    short = {letter: name for name, letter, _ in flags if letter is not None}
    found = Arguments()
    pending = list(arguments)
    while pending:
        argument = pending.pop(0)
        if argument == "-" or not argument.startswith("-"):
            found.positional.append(argument)
            continue
        body = argument.lstrip("-")
        value: str | None = None
        if "=" in body:
            body, value = body.split("=", 1)
        name = short.get(body, body)
        if name == "param":
            parameter(found, value, pending)
            continue
        if name == "accept":
            found.accepted.add(taken(argument, value, pending))
            continue
        if name not in long:
            raise Usage(f"unknown flag {argument!r}")
        if not long[name]:
            if value is not None:
                raise Usage(f"{argument!r} takes no value")
            found.flags[name] = True
            continue
        if value is None:
            if not pending:
                raise Usage(f"{argument!r} needs a value")
            value = pending.pop(0)
        found.flags[name] = value
    return found


def taken(argument: str, value: str | None, pending: list[str]) -> str:
    """Return a flag's value, attached to it or following it.

    Args:
        argument: The flag as it was written, for the diagnostic.
        value: What was attached after an ``=``, where anything was.
        pending: The arguments still to read, for a detached value.

    Raises:
        Usage: The flag has no value after it.

    """
    if value is not None:
        return value
    if not pending:
        raise Usage(f"{argument!r} needs a value")
    return pending.pop(0)


def parameter(found: Arguments, value: str | None, pending: list[str]) -> None:
    """Read one ``--param NAME=VALUE``.

    ``--param`` and ``--accept`` are the repeatable flags.  A parameter
    given twice is refused rather than taken last-wins -- two places both
    think they own that parameter, and one of them is wrong -- where an
    ``--accept`` given twice is simply the one name.

    Args:
        found: What has been read so far, which this adds to.
        value: The value attached to the flag, where it was attached.
        pending: The arguments still to read, for a detached value.

    Raises:
        Usage: The value is not ``NAME=VALUE``, or repeats a name.

    """
    if value is None:
        if not pending:
            raise Usage("--param needs a NAME=VALUE")
        value = pending.pop(0)
    if "=" not in value:
        raise Usage(f"--param takes NAME=VALUE, and got {value!r}")
    name, text = value.split("=", 1)
    if name in found.params:
        raise Usage(f"--param {name} is given twice")
    found.params[name] = text


COMMANDS = {
    "validate": validate,
    "version": version,
}
