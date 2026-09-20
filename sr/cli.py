"""The command line of doc/cli.md.

Partial, deliberately.  ``build``, ``validate``, ``inspect``, ``version``
and ``help`` are here; ``render`` names the milestone that brings it and
exits 1, because a command that printed nothing and exited 0 would be
worse than one that says it is not there.  :data:`COMMANDS` holds what
works, which is also what the differential harness asks before deciding
whether this engine can be compared against the reference yet.
:data:`PARTIAL` holds the two that work but not to the end of their
specification, so ``help`` says which part is missing rather than
leaving a reader to find out from the output.

``validate`` resolves the fonts a template declares, which is the one
part of the check that depends on the machine rather than on the
document.  doc/cli.md#sr-validate puts that where a reader will look
at it: a `fonts` section saying which step of the chain produced each
face, a `failures` section for the ones that did not resolve, and,
under ``--verbose``, what enumerating the host had to say.  Enumeration
is lazy, so a template whose fonts all name a file never touches it.

Flag parsing follows doc/cli.md#flags: one dash or two,
the value attached or separate, and flags after positionals.
``--param`` is the one repeatable flag, and the two mistakes in it
are refused rather than absorbed: a name given twice, and a name
the template does not declare.  The first is a command-line mistake
and is caught before the template is read; the second needs the
template and is caught after.

"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from sr import api, meta
from sr.errors import (
    BadValue,
    BuildWarning,
    Diagnostic,
    ExpressionError,
    FontError,
    SrError,
    TemplateError,
)
from sr.expr import evaluate
from sr.expr.values import go_shortest, quote
from sr.fonts.resolve import Resolution, Resolver
from sr.printout.inspect import dump, pages_wanted, read_jsonl
from sr.printout.write import write_jsonl
from sr.template import load as load_template
from sr.template.load import (
    Loaded,
    Options,
    levels_of,
    sections_of,
    subreports_of,
)
from sr.template.model import Blob, Font, Parameter, Report, parse_text

__all__ = [
    "COMMANDS",
    "FORMATS",
    "PARTIAL",
    "PLANNED",
    "Fonts",
    "Usage",
    "as_written",
    "build",
    "inspect_printout",
    "main",
    "resolve_fonts",
    "validate",
    "version",
]

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
    "render": "M14",
}

# The commands that work, and the part of doc/cli.md each of them does
# not reach yet.  Kept apart from PLANNED because the difference matters
# to a reader: one of these produces the right answer over a narrower
# input, and a planned command produces nothing at all.
PARTIAL = {
    "build": ("M14", "PDF output; a printout is written as NDJSON"),
    "inspect": ("M13", "the invariant check; the dump itself is complete"),
}

# What each output extension names, per doc/cli.md#sr-build.
FORMATS = {
    ".pdf": "pdf",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".cbor": "cbor",
}

# The milestone that brings each output format this one cannot write.
FORMAT_MILESTONE = {"pdf": "M14", "cbor": "M13"}

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
        accepted: The ``--accept`` names, which are a set because
            a name given twice asks for exactly what one name asks for.
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

# The flags `build` takes, per doc/cli.md#sr-build.
BUILD_FLAGS = (
    ("template", "t", True),
    ("data", "d", True),
    ("out", "o", True),
    ("format", None, True),
    ("build-time", None, True),
    ("strict-fonts", None, False),
    ("strict-names", None, False),
    ("allow-overflow", None, False),
    ("uncompressed", None, False),
    ("verbose", "v", False),
)

# The flags `inspect` takes, per doc/cli.md#sr-inspect.
INSPECT_FLAGS = (
    ("pages", None, True),
    ("summary", None, False),
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
        if command == "build":
            return build(rest, stream)
        if command == "inspect":
            return inspect_printout(rest, stream)
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
        if name in PARTIAL:
            milestone, missing = PARTIAL[name]
            print(f"  not yet: {missing} ({milestone})", file=out)
        if name == "build":
            print("  -t, --template FILE   the template to apply", file=out)
            print("  -d, --data FILE       the records, - for stdin", file=out)
            print("  -o, --out FILE        where to write, - for stdout", file=out)
            print("  --format NAME         pdf, jsonl or cbor", file=out)
            print("  --build-time TIME     RFC 3339, fixes BUILD_TIME", file=out)
            print("  --param NAME=VALUE    a report parameter, repeatable", file=out)
            print("  --strict-fonts        resolve only fonts named by path", file=out)
            print("  --strict-names        refuse an unknown name", file=out)
            print(
                "  --accept NAME         take one name in silence, repeatable", file=out
            )
            print("  --allow-overflow      an oversized band is a warning", file=out)
            print("  -v, --verbose         report host font diagnostics", file=out)
        if name == "inspect":
            print("  --pages RANGES        which pages to dump, as 1,4-6", file=out)
            print("  --summary             the header only, no pages", file=out)
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


# -- build ------------------------------------------------------------


def build(arguments: Sequence[str], out: TextIO) -> int:
    """Apply a template to data and write a printout.

    What the command produces goes to the file ``--out`` names,
    and everything about the run to standard error: the warnings,
    one per line, and then the summary line.

    Args:
        arguments: What followed the command.
        out: Where a document written to standard output goes.

    """
    given = parse(arguments, BUILD_FLAGS)
    if given.positional:
        raise Usage(f"build takes no arguments, and got {given.positional[0]!r}")
    template = given.flags.get("template")
    if template is None:
        raise Usage("build needs a template, as -t")
    destination = given.flags.get("out")
    if destination is None:
        raise Usage("build needs an output, as -o")
    target = str(destination)
    encoding = format_of(target, given.flags.get("format"))
    if encoding in FORMAT_MILESTONE:
        print(
            f"sr: {encoding} output arrives in {FORMAT_MILESTONE[encoding]}",
            file=sys.stderr,
        )
        return FAILED
    options = api.Options(
        params=dict(given.params),
        build_time=text_of(given.flags.get("build-time")),
        strict_fonts=bool(given.flags.get("strict-fonts")),
        strict_names=bool(given.flags.get("strict-names")),
        accepted=frozenset(given.accepted),
        allow_overflow=bool(given.flags.get("allow-overflow")),
        verbose=bool(given.flags.get("verbose")),
    )
    try:
        result = api.build(Path(str(template)), data_source(given), options)
        # Inside the same handler as the build: a directory that is
        # not there and a template that will not load are both the
        # run failing, and both are reported as `sr:` and exit 1
        # rather than as a traceback.
        write_printout(result, target, out)
    except TemplateError as refused:
        for diagnostic in refused.diagnostics:
            print(diagnostic, file=sys.stderr)
        return FAILED
    except SrError as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    except OSError as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    for line in result.diagnostics:
        print(line, file=sys.stderr)
    for note in result.notes:
        print(f"warning: {note}", file=sys.stderr)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"{target}: {counted(result)}", file=sys.stderr)
    return OK


def data_source(given: Arguments) -> Path | TextIO | None:
    """Return where the records come from.

    Args:
        given: The arguments, for ``--data``.

    """
    data = given.flags.get("data")
    if data is None:
        return None
    return sys.stdin if data == "-" else Path(str(data))


def format_of(target: str, asked: str | bool | None) -> str:
    """Return the encoding to write, and complain where the two disagree.

    doc/cli.md#sr-build: the format comes from ``--format``, or from
    what ``--out`` ends in.  ``--format`` over a recognized extension
    warns and proceeds, because the file it writes will be read back
    by its extension; over an unrecognized one, or none, it is silent,
    since overriding is what the flag is for.

    Args:
        target: What ``--out`` named.
        asked: What ``--format`` asked for, where it was given.

    Raises:
        Usage: Neither says what to write,
            or ``--format`` names nothing this format has.

    """
    suffix = suffix_of(target)
    named = FORMATS.get(suffix)
    if asked is None:
        if named is None:
            if target == "-":
                raise Usage("--out - needs --format, since there is no extension")
            raise Usage(
                f"the extension {suffix!r} names no format; "
                "expected .pdf, .jsonl or .cbor, or give --format"
            )
        return named
    wanted = str(asked)
    if wanted not in set(FORMATS.values()):
        raise Usage(f"--format {wanted}: expected pdf, jsonl or cbor")
    if named is not None and named != wanted:
        print(
            f"warning: --format {wanted} over an output named {suffix}, "
            f"which render and inspect will read as {named}",
            file=sys.stderr,
        )
    return wanted


def suffix_of(target: str) -> str:
    """Return the extension an output path ends in.

    ``.srp.jsonl`` is two extensions and the last is what names
    the format, so this is the last one and not the pair.

    Args:
        target: What ``--out`` named.

    """
    return Path(target).suffix.lower()


def write_printout(result: api.Result, target: str, out: TextIO) -> None:
    """Write the printout where ``--out`` said.

    The directory it lands in is what its paths are written relative
    to, which is why the destination reaches the writer rather than
    only the stream.  A document going to standard output has no
    directory, and the working directory stands in.

    Args:
        result: What the build produced.
        target: What ``--out`` named.
        out: Where a document written to standard output goes.

    """
    if target == "-":
        write_jsonl(result.printout, as_written(out), None)
        return
    path = Path(target)
    base = path.parent
    # newline="": doc/printout.md#encoding ends every line with U+000A,
    # and a platform whose text mode translates that would write CRLF
    # and make the same document two different files.
    with path.open("w", encoding="utf-8", newline="") as handle:
        write_jsonl(result.printout, handle, base)


def as_written(out: TextIO) -> TextIO:
    """Return a stream that writes a printout as the format spells it.

    Standard output is a text stream the platform set up,
    and on Windows that means it translates U+000A into two bytes
    and encodes in the console's codepage.  A printout is
    [LF-terminated UTF-8](doc/printout.md#encoding) wherever it
    is written, so the stream is reconfigured where it can be.
    A stream that cannot be -- a test's buffer, which
    translates nothing -- is returned as it is.

    Args:
        out: Where a document written to standard output goes.

    """
    reconfigure = getattr(out, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", newline="")
    return out


def counted(result: api.Result) -> str:
    """Return the summary line doc/cli.md#sr-build ends a build with.

    What it names is the document rather than the run: pages, fonts,
    blobs, warnings.  The file it went to is the caller's to print,
    because the caller is the one that knows what the user wrote.

    Args:
        result: What the build produced.

    """
    printout = result.printout
    counts = (
        (len(printout.pages), "page", "pages"),
        (len(printout.fonts), "font", "fonts"),
        (len(printout.data), "data blob", "data blobs"),
        (len(result.warnings), "warning", "warnings"),
    )
    return ", ".join(
        f"{value} {one if value == 1 else many}"
        for value, one, many in counts
        if value or one == "page"
    )


# -- inspect ----------------------------------------------------------


def inspect_printout(arguments: Sequence[str], out: TextIO) -> int:
    """Dump a printout as readable text.

    Args:
        arguments: What followed the command.
        out: Where the dump goes.

    """
    given = parse(arguments, INSPECT_FLAGS)
    if len(given.positional) != 1:
        raise Usage("inspect takes one printout")
    path = Path(given.positional[0])
    if path.suffix.lower() == ".cbor":
        print("sr: reading CBOR arrives in M13", file=sys.stderr)
        return FAILED
    try:
        records = read_jsonl(path)
        wanted = pages_wanted(text_of(given.flags.get("pages")), len(records) - 1)
    except SrError as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    except OSError as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    for line in dump(path, records, wanted, bool(given.flags.get("summary"))):
        print(line, file=out)
    return OK


def text_of(value: str | bool | None) -> str | None:
    """Return a flag's value as text, where it carries one.

    Args:
        value: What the parser recorded for the flag.

    """
    return None if value is None or isinstance(value, bool) else value


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
    refused = parameters_given(loaded.report, given.params)
    if refused:
        for message in refused:
            print(message, file=sys.stderr)
        return FAILED
    fonts = resolve_fonts(
        loaded.report,
        given.params,
        strict=bool(given.flags.get("strict-fonts")),
        verbose=bool(given.flags.get("verbose")),
    )
    if not quiet:
        describe(loaded, str(template), fonts, out)
    if fonts.failures:
        count = len(fonts.failures)
        one = "font" if count == 1 else "fonts"
        print(f"sr: {count} {one} did not resolve", file=sys.stderr)
        return FAILED
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


# -- resolving the fonts ----------------------------------------------


@dataclass(frozen=True)
class Fonts:
    """What resolving one report's `font` nodes produced.

    Attributes:
        resolved: One per `font` that found a face, in document order.
        failures: The name of each `font` that did not, with why.
        warnings: What the printout header would carry: a substituted
            typeface, and a declared style the face does not have.
        diagnostics: What enumerating the host had to say,
            under ``--verbose`` and only when enumeration happened at all.

    """

    resolved: tuple[Resolution, ...] = ()
    failures: tuple[tuple[str, str], ...] = ()
    warnings: tuple[BuildWarning, ...] = ()
    diagnostics: tuple[str, ...] = ()


def resolve_fonts(
    report: Report,
    params: dict[str, str],
    *,
    strict: bool = False,
    verbose: bool = False,
) -> Fonts:
    """Resolve every `font` the document tree declares, collecting failures.

    Resolution runs to the end rather than stopping at the first failure,
    for the reason validation does: one run of the tool should report
    every font that has to be dealt with.

    Every document is resolved, not only the host.  doc/cli.md#sr-validate
    has a ``template=`` subreport checked with its host, and a font
    it declares is a font the build will need.  Only the host's fonts
    are listed, though: the table is what this template declares, and a
    subreport's faces belong to its own file.  Its failures and warnings
    are the host's problem and do surface.

    Args:
        report: The template that was loaded.
        params: The ``--param`` values, by name.
        strict: Whether ``--strict-fonts`` is set.
        verbose: Whether the host diagnostics are wanted.

    """
    resolver = Resolver(strict=strict)
    resolved: list[Resolution] = []
    failures: list[tuple[str, str]] = []
    warnings: list[BuildWarning] = []
    for document in documents(report):
        # A subreport has no command line: its parameters come from
        # the `arg` nodes of the node that invokes it, which are values
        # a build has and a check does not.  So `--param` is the host's.
        host = document is report
        blobs, refused = blob_bytes(document, params if host else {})
        resolver.reading(document.basedir, blobs)
        for font in document.fonts:
            try:
                one = resolver.resolve(font)
            except FontError as beaten:
                failures.append((font.name, blob_reason(font, refused) or str(beaten)))
                continue
            if host:
                resolved.append(one)
            warnings.extend(one.warnings)
    return Fonts(
        tuple(resolved),
        tuple(failures),
        tuple(warnings),
        resolver.diagnostics if verbose else (),
    )


def documents(report: Report, seen: set[int] | None = None) -> Iterator[Report]:
    """Yield a report and every distinct template its subreports name.

    Depth first, in document order, and each document once however many
    `subreport` nodes name it: the loader reads a template once and hands
    the same document to every node that named it, so a template invoked
    twice is one set of fonts and one set of failures.

    Args:
        report: The document to start from.
        seen: The documents already yielded, by identity.

    """
    seen = set() if seen is None else seen
    if id(report) in seen:
        return
    seen.add(id(report))
    yield report
    for subreport in subreports_of(report):
        if subreport.report is not None:
            yield from documents(subreport.report, seen)


def blob_reason(font: Font, refused: dict[str, str]) -> str | None:
    """Return why the blob a `font` names has no bytes, where that is why.

    doc/cli.md#sr-validate asks for exactly this: a `data` blob whose
    ``expr`` reads a parameter with no value is reported against the font
    that needed the blob, rather than as a missing parameter, because
    a template whose values arrive at build time is not thereby invalid.

    Args:
        font: The node that did not resolve.
        refused: Why each ``expr`` blob produced nothing, by name.

    """
    if font.data is None:
        return None
    reason = refused.get(font.data)
    if reason is None:
        return None
    return f"the data node {quote(font.data)} is computed at build time: {reason}"


def blob_bytes(
    report: Report, params: dict[str, str]
) -> tuple[dict[str, bytes], dict[str, str]]:
    """Return the bytes of every ``data`` node, and why any produced none.

    A blob written as ``content`` is decoded at load, so it is here
    as it stands.  One written as ``expr`` is produced at build time,
    and whether it can be produced now depends on the parameters a caller
    supplied: with them it resolves and the font it feeds is checked,
    and without them the font is reported rather than the parameter.

    Args:
        report: The template that was loaded.
        params: The ``--param`` values, by name.

    """
    found: dict[str, bytes] = {}
    refused: dict[str, str] = {}
    names = parameter_values(report, params)
    for blob in report.data:
        if blob.content is not None:
            found[blob.name] = blob.content
            continue
        if blob.expr is None:
            continue
        try:
            found[blob.name] = as_bytes(blob, names)
        except (ExpressionError, BadValue, ValueError, TypeError) as beaten:
            refused[blob.name] = str(beaten)
    return found, refused


def as_bytes(blob: Blob, names: dict[str, Any]) -> bytes:
    """Return the bytes an ``expr`` blob evaluates to.

    Args:
        blob: The node.
        names: The values its expression may read.

    Raises:
        ExpressionError: The expression would not evaluate.

    """
    assert blob.expr is not None
    value = evaluate(blob.expr.source, names)
    if isinstance(value, bytes):
        return value
    return str(value).encode()


def parameter_values(report: Report, params: dict[str, str]) -> dict[str, Any]:
    """Return every parameter that has a value, for a blob's expression.

    A ``--param`` wins over a ``default``, a ``default`` over a
    ``defaultexpr``, and a parameter with none of the three is
    simply absent -- which is what makes an expression that reads it
    fail with the name it could not resolve.

    Args:
        report: The template that was loaded.
        params: The ``--param`` values, by name.

    """
    found: dict[str, Any] = {}
    for one in report.parameters:
        if one.name in params:
            try:
                found[one.name] = parse_text(one.kind, params[one.name], one.format)
            except BadValue:
                continue
        elif one.value is not None:
            found[one.name] = one.value
        elif one.defaultexpr is not None:
            try:
                found[one.name] = evaluate(one.defaultexpr.source, dict(found))
            except (ExpressionError, ValueError, TypeError):
                continue
    return found


def describe(loaded: Loaded, file: str, fonts: Fonts, out: TextIO) -> None:
    """Print the report doc/cli.md#sr-validate describes.

    A name, and then something short and uniform after it, are padded
    to line up; the rest of a line runs on, because what follows differs
    from row to row and lining up fields that are not the same kind
    of thing reads as though they were.

    `ok` is the last line, so a ``tail -1`` reads the verdict,
    and a check that failed does not print it.

    Args:
        loaded: What the load produced.
        file: The template, as the caller named it.
        fonts: What resolving the template's fonts produced.
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
    print(f"  {tally(report)}", file=out)
    if report.parameters:
        print("parameters", file=out)
        for line in parameter_lines(report.parameters):
            print(f"  {line}", file=out)
    section("subreports", subreport_lines(report), out)
    section("fonts", font_lines(fonts.resolved), out)
    section("warnings", warning_lines(loaded.warnings, fonts.warnings), out)
    section("failures", failure_lines(fonts.failures), out)
    section("diagnostics", list(fonts.diagnostics), out)
    if not fonts.failures:
        print("ok", file=out)


def section(name: str, lines: list[str], out: TextIO) -> None:
    """Print one heading and its lines, or nothing where it has none.

    Args:
        name: The heading.
        lines: What goes under it, already padded.
        out: Where to print.

    """
    if not lines:
        return
    print(name, file=out)
    for line in lines:
        print(f"  {line}", file=out)


def font_lines(resolved: tuple[Resolution, ...]) -> list[str]:
    """Return one padded line per font that resolved.

    Each is the name the template gave it, its size and style,
    the step of the chain that produced it, the file, and the face
    inside that file.  The typeface is named only where it is not the
    face's own family, since repeating a name that matched says nothing.

    Sorted by name, as doc/printout.md#fonts sorts the header's font
    table and for the same reason: a reader looks a font up by the name
    the template gave it, and document order puts that name wherever the
    declarations happened to fall.

    Args:
        resolved: The resolutions, in document order.

    """
    if not resolved:
        return []
    names = max(len(one.font.name) for one in resolved)
    sizes = max(len(f"{one.font.size}pt") for one in resolved)
    lines = []
    for one in sorted(resolved, key=lambda found: found.font.name):
        parts = [f"{one.font.name:<{names}}", f"{one.font.size}pt".ljust(sizes)]
        parts.extend(style_words(one.font))
        wanted = one.requested
        if wanted is not None and wanted != one.face.family:
            parts.append(f'"{wanted}" wanted')
        parts.append(one.step)
        parts.append(one.face.origin.shown())
        parts.append(f'"{one.face.family}"')
        lines.append("  ".join(parts))
    return lines


def style_words(font: Font) -> list[str]:
    """Return the style a `font` node declares, as words.

    Args:
        font: The node.

    """
    return [
        word
        for word, on in (
            ("bold", font.bold),
            ("italic", font.italic),
            ("underline", font.underline),
        )
        if on
    ]


def warning_lines(
    load: tuple[Diagnostic, ...], fonts: tuple[BuildWarning, ...]
) -> list[str]:
    """Return one line per warning, the load's first.

    Both kinds are spelled the way their own class spells one, which for
    a font warning means the kind it will carry into the printout header
    and the node it happened at.  Printing the message alone loses both,
    and the reader of a `warnings` section is left with a substituted
    typeface that does not say which `font` node asked for it.

    Args:
        load: What reading the template had to say.
        fonts: What resolving its fonts had to say.

    """
    return [str(one) for one in load] + [str(one) for one in fonts]


def failure_lines(failures: tuple[tuple[str, str], ...]) -> list[str]:
    """Return one line per font that did not resolve.

    These are why the exit code is 1, so doc/cli.md#sr-validate
    keeps them out of `warnings` and under a heading of their own.

    Args:
        failures: Each font's name and why it failed.

    """
    return [f"font {quote(name)}: {why}" for name, why in failures]


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

    doc/cli.md#sr-validate fixes the terms and their order.
    A term whose count is zero is left out, which is why
    a template with no groups says nothing about groups;
    the column count is the one that is never zero, because
    a layout with no `columns` node has one column rather than none.

    The last term counts `subreport` nodes rather than `embedded`
    layouts: what a reader wants to know is how many subreports
    the document runs, and an embedded layout invoked three times
    is three of them.  The layouts themselves are not counted,
    because the section below names every node that uses one.

    Args:
        report: The template that was loaded.

    """
    layout = report.layout
    columns = layout.columns.count if layout is not None and layout.columns else 1
    groups = 0 if layout is None else len(levels_of(layout)) - 1
    members = len(report.records.members) if report.records is not None else 0
    counted = (
        (columns, "column", "columns"),
        (groups, "group", "groups"),
        (members, "member", "members"),
        (len(report.variables), "variable", "variables"),
        (len(report.fonts), "font", "fonts"),
        (len(report.data), "data blob", "data blobs"),
        (len(subreports_of(report)), "subreport", "subreports"),
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

    The path alone, as doc/cli.md#sr-validate asks: it already ends
    in the band and the node, and what the node names is the template's
    business rather than the reader's here.

    Args:
        report: The template that was loaded.

    """
    return [
        str(subreport.path)
        for band in sections_of(report)
        for subreport in band.subreports
    ]


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
    "build": build,
    "inspect": inspect_printout,
    "validate": validate,
    "version": version,
}
