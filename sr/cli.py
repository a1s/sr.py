"""The command line of doc/cli.md.

Every command is here: ``build``, ``validate``, ``render``, ``inspect``,
``version`` and ``help``.  :data:`COMMANDS` holds them, which is also
what the differential harness asks before deciding whether this engine
can be compared against the reference.

The two routes to a PDF are one route.  ``build`` with a ``.pdf`` output
hands the printout it built straight to the renderer, and ``render``
reads one back from a file first; doc/render.md#reproducibility makes
the two give the same bytes.  Either renders the whole document before
it opens the output, so a render that fails leaves the previous file
where it was.

``validate`` resolves the fonts a template declares, which is the one
part of the check that depends on the machine rather than on the
document.  doc/cli.md#sr-validate puts that where a reader will look
at it: a `fonts` section saying which step of the chain produced each
face, a `failures` section for the ones that did not resolve, and,
under ``--verbose``, what enumerating the host had to say.  Enumeration
is lazy, so a template whose fonts all name a file never touches it.

Flag parsing follows doc/cli.md#flags: one dash or two,
the value attached or separate, flags after positionals, and ``--``
to end them.  ``--param`` and ``--accept`` are the repeatable flags,
and the two mistakes in ``--param`` are refused rather than absorbed:
a name given twice, and a name the template does not declare.
The first is caught before the template is read; the second needs
the template and is caught after.  Both are usage errors.

"""

from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from sr import api, meta
from sr.errors import (
    BadValue,
    BuildError,
    BuildWarning,
    Diagnostic,
    ExpressionError,
    FontError,
    SrError,
    TemplateError,
    UnknownParameter,
)
from sr.expr import evaluate
from sr.expr.values import go_shortest, quote
from sr.fonts.resolve import Resolution, Resolver
from sr.pdf import render as render_pdf
from sr.printout.inspect import dump, page_ranges, pages_wanted, read_printout
from sr.printout.model import Printout
from sr.printout.read import read as read_model
from sr.printout.verify import violations
from sr.printout.write import write_cbor, write_jsonl
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
    "Fonts",
    "Usage",
    "as_written",
    "build",
    "inspect_printout",
    "main",
    "render",
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

# What each output extension names, per doc/cli.md#sr-build.
FORMATS = {
    ".pdf": "pdf",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".cbor": "cbor",
}

# What ``--format`` takes: the extensions above without their dot,
# in any case, which is how the extensions themselves are compared.
FORMAT_NAMES = {suffix[1:]: name for suffix, name in FORMATS.items()}

# What a printout does with the one thing UTF-8 cannot encode, a lone
# surrogate from a file name that was not valid text, on either route.
# The backslash escape it writes is a `u` and four hex digits, which
# inside a JSON string is the escape for that same code point: so the
# printout stays JSON, and a font path in it reads back as it was given.
PRINTOUT_ERRORS = "backslashreplace"

# What the summary line calls an output that went to standard output,
# since `-` names nothing a reader would recognize.
STANDARD_OUTPUT = "standard output"

# The values a switch may be given, per doc/cli.md#flags.
SWITCH_VALUES = {"true": True, "false": False}

# The exit codes of doc/cli.md#exit-codes.
OK = 0
FAILED = 1
USAGE = 2


class Usage(Exception):
    """The command line was wrong, as opposed to the run having failed.

    Carries the message as its text; the caller prints it and exits 2.

    """


class Help(Exception):
    """The command line asked for a command's usage.

    Raised by the parser where it meets ``-h`` or ``--help``,
    so that a help flag anywhere among a command's arguments
    is read as the parser reads every other flag.

    """


@dataclass(frozen=True)
class Flag:
    """One flag a command takes.

    Attributes:
        name: The long name, without the two dashes it is written with.
        letter: The one-letter name, where there is one,
            without the one dash it is written with.
        value: Whether it takes a value.  One that does not
            is a switch, which may be given ``=true`` or ``=false``.

    """

    name: str
    letter: str | None = None
    value: bool = True


@dataclass
class Arguments:
    """One command's arguments, as the parser read them.

    Attributes:
        flags: Every flag given, by its long name, last one winning.
            A switch is a boolean, and a flag given an empty value
            is not here at all, since that is the flag not given.
        params: The ``--param`` values, in the order they were given.
        accepted: The ``--accept`` names, which are a set because
            a name given twice asks for exactly what one name asks for.
        positional: What was given without a flag in front of it.

    """

    flags: dict[str, str | bool] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    accepted: set[str] = field(default_factory=set)
    positional: list[str] = field(default_factory=list)


# The flags `validate` takes, per doc/cli.md#sr-validate.
VALIDATE_FLAGS = (
    Flag("template", "t"),
    Flag("param"),
    Flag("strict-fonts", value=False),
    Flag("strict-names", value=False),
    Flag("accept"),
    Flag("quiet", "q", value=False),
    Flag("verbose", "v", value=False),
)

# The flags `build` takes, per doc/cli.md#sr-build.
BUILD_FLAGS = (
    Flag("template", "t"),
    Flag("data", "d"),
    Flag("out", "o"),
    Flag("format"),
    Flag("param"),
    Flag("build-time"),
    Flag("strict-fonts", value=False),
    Flag("strict-names", value=False),
    Flag("accept"),
    Flag("allow-overflow", value=False),
    Flag("uncompressed", value=False),
    Flag("verbose", "v", value=False),
)

# The flags `render` takes, per doc/cli.md#sr-render.
RENDER_FLAGS = (
    Flag("out", "o"),
    Flag("uncompressed", value=False),
)

# The flags `inspect` takes, per doc/cli.md#sr-inspect.
INSPECT_FLAGS = (
    Flag("pages"),
    Flag("summary", value=False),
)

# The flag every command takes, which asks for its usage.
HELP_FLAG = Flag("help", "h", value=False)

# How the command line spells a help flag and a version flag,
# with one dash for a letter and two for a long name, as every flag is.
HELP = {"-h", "--help"}
VERSION = {"--version"}

# What doc/cli.md#flags says about dashes, for a flag that breaks it.
DASHES = "a one-letter flag takes one dash, and a long one two"


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run one command and return its exit code.

    Before anything else the standard streams are set to UTF-8, all
    three of them: :func:`utf8_streams` says how.  A caller running this
    in-process should know that it changes them, standard input included.

    Args:
        argv: The arguments after the program name; ``sys.argv[1:]``
            when the caller gives none.
        out: Where the report goes; standard output by default.
            It is set to write UTF-8 as standard output is.

    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    stream = utf8_streams(out)
    if not arguments:
        print("sr: a command is required; try `sr.py help`", file=sys.stderr)
        return USAGE
    command = arguments[0]
    rest = arguments[1:]
    try:
        if command == "help" or command in HELP:
            return help_for(rest, stream)
        if command in VERSION:
            command = "version"
        run = COMMANDS.get(command)
        if run is None:
            raise Usage(unknown_command(command))
        try:
            return run(rest, stream)
        except Help:
            return help_for([command], stream)
    except Usage as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return USAGE


def unknown_command(command: str) -> str:
    """Return what to say about a first argument that names no command.

    Args:
        command: The first argument.

    """
    if command.startswith("-") and command != "-":
        if respelled(command) in HELP | VERSION:
            return f"{command!r}: {DASHES}, as {respelled(command)}"
        return f"{command!r} is a flag, and flags come after the command"
    return f"unknown command {command!r}; try `sr.py help`"


def utf8_streams(out: TextIO | None) -> TextIO:
    """Set the standard streams to UTF-8, and return where output goes.

    doc/cli.md#streams: what a command writes is UTF-8 on every machine,
    and records read from standard input are UTF-8, as a data file is.
    A Windows console takes its text through the console's own API and
    was never the problem.  A pipe or a file there gets the ANSI code
    page instead: a dump of a printout holding text outside that page
    ended in a traceback, and an `é` piped in as a record became two
    other characters without a word said.

    On the way out, a character that UTF-8 still cannot carry, a lone
    surrogate from a file name that was not valid text, is written as
    a backslash escape: nothing a command prints is worth failing the
    run over.  On the way in, a byte that is not UTF-8 is kept apart
    by ``surrogateescape``, which is what lets reading the records
    turn it into U+FFFD, one per byte, exactly as for a file.

    Args:
        out: Where output goes, where the caller named somewhere;
            standard output otherwise.

    """
    stream = reconfigured(
        sys.stdout if out is None else out,
        encoding="utf-8",
        errors="backslashreplace",
        newline="\n",
    )
    reconfigured(
        sys.stderr,
        encoding="utf-8",
        errors="backslashreplace",
        newline="\n",
    )
    reconfigured(sys.stdin, encoding="utf-8", errors="surrogateescape")
    return stream


def reconfigured(stream: TextIO, **settings: str) -> TextIO:
    """Return a stream with its text settings changed, where it allows it.

    A stream that cannot be reconfigured is returned as it is: a test's
    buffer, which encodes nothing, or ``None``, which is what Python
    leaves in place of a standard stream the process was started without.
    So is one that refuses, as standard input does once a caller running
    :func:`main` in-process has read part of it.  That caller keeps the
    encoding it was reading in, rather than every command failing at
    its first line.

    Args:
        stream: The stream to change.
        **settings: What :meth:`io.TextIOWrapper.reconfigure` takes.

    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(io.UnsupportedOperation):
            reconfigure(**settings)
    return stream


def version(arguments: Sequence[str], out: TextIO) -> int:
    """Print the engine's name and version.

    Args:
        arguments: What followed the command; it takes none.
        out: Where to print.

    """
    given = parse(arguments, ())
    if given.positional:
        raise Usage(f"version takes no arguments, and got {given.positional[0]!r}")
    print(meta.engine(), file=out)
    return OK


# What `sr.py help` says about each command: the arguments it takes,
# and then one line per flag.
USAGES = {
    "build": (
        "sr.py build -t TEMPLATE [-d DATA] -o OUT [flags]",
        (
            ("-t, --template FILE", "the template to apply"),
            ("-d, --data FILE", "the records, - for standard input"),
            ("-o, --out FILE", "where to write, - for standard output"),
            ("--format NAME", "pdf, jsonl or cbor, where --out does not say"),
            ("--param NAME=VALUE", "a report parameter, repeatable"),
            ("--build-time TIME", "RFC 3339, fixes BUILD_TIME"),
            ("--strict-fonts", "resolve only fonts named by path"),
            ("--strict-names", "refuse an unknown name"),
            ("--accept NAME", "take one name in silence, repeatable"),
            ("--allow-overflow", "an oversized band is a warning"),
            ("--uncompressed", "leave PDF streams uncompressed"),
            ("-v, --verbose", "report host font diagnostics"),
        ),
    ),
    "validate": (
        "sr.py validate [-t] TEMPLATE [flags]",
        (
            ("-t, --template FILE", "the template to check"),
            ("--param NAME=VALUE", "a report parameter, repeatable"),
            ("--strict-fonts", "resolve only fonts named by path"),
            ("--strict-names", "refuse an unknown name"),
            ("--accept NAME", "take one name in silence, repeatable"),
            ("-q, --quiet", "print nothing on success"),
            ("-v, --verbose", "include host font diagnostics"),
        ),
    ),
    "render": (
        "sr.py render PRINTOUT -o OUT [flags]",
        (
            ("-o, --out FILE", "where to write, - for standard output"),
            ("--uncompressed", "leave PDF streams uncompressed"),
        ),
    ),
    "inspect": (
        "sr.py inspect PRINTOUT [flags]",
        (
            ("--pages RANGES", "which pages to dump, as 1,4-6"),
            ("--summary", "the header only, no pages"),
        ),
    ),
    "version": ("sr.py version", ()),
    "help": ("sr.py help [COMMAND]", ()),
}


def help_for(arguments: Sequence[str], out: TextIO) -> int:
    """Print usage, for one command or for all of them.

    doc/cli.md#subcommands: ``help`` takes one command at most,
    and one it does not know is a usage error, as it would be
    named first.

    Args:
        arguments: The command to describe, where one was named.
        out: Where to print.

    Raises:
        Usage: More than one command was named, or one that is not.

    """
    named = ["help" if one in HELP else one for one in arguments]
    if len(named) > 1:
        raise Usage(f"help takes one command, and got {named[1]!r} too")
    if named:
        name = named[0]
        if name not in USAGES:
            raise Usage(unknown_command(name))
        usage, flags = USAGES[name]
        print(f"usage: {usage}", file=out)
        print(f"  {SUMMARY[name]}", file=out)
        width = max((len(spelling) for spelling, _ in flags), default=0)
        for spelling, what in flags:
            print(f"  {spelling:<{width}}  {what}", file=out)
        return OK
    print("usage: sr.py <command> [flags]", file=out)
    for name, what in SUMMARY.items():
        print(f"  {name:<9}{what}", file=out)
    print("sr.py help <command> describes one of them.", file=out)
    return OK


# -- build ------------------------------------------------------------


def build(arguments: Sequence[str], out: TextIO) -> int:
    """Apply a template to data and write a printout or a PDF.

    What the command produces goes to the file ``--out`` names,
    and everything about the run to standard error: the warnings,
    one per line, and then the summary line.

    Every check on the command line comes before the template
    is read, ``--build-time`` among them, so that a mistyped flag
    is reported as itself.  A ``--param`` the template does not
    declare is a usage error too, and is found once it has been read.

    Args:
        arguments: What followed the command.
        out: Where a document written to standard output goes.

    """
    given = parse(arguments, BUILD_FLAGS)
    if given.positional:
        raise Usage(f"build takes no arguments, and got {given.positional[0]!r}")
    template = text_of(given.flags.get("template"))
    if template is None:
        raise Usage("build needs a template, as -t")
    target = text_of(given.flags.get("out"))
    if target is None:
        raise Usage("build needs an output, as -o")
    encoding = format_of(target, text_of(given.flags.get("format")))
    built = text_of(given.flags.get("build-time"))
    if built is not None:
        try:
            api.build_time_of(built)
        except BuildError as refused:
            raise Usage(str(refused)) from None
    options = api.Options(
        params=dict(given.params),
        build_time=built,
        strict_fonts=bool(given.flags.get("strict-fonts")),
        strict_names=bool(given.flags.get("strict-names")),
        accepted=frozenset(given.accepted),
        allow_overflow=bool(given.flags.get("allow-overflow")),
        verbose=bool(given.flags.get("verbose")),
    )
    try:
        result = api.build(Path(template), data_source(given), options)
    except UnknownParameter as refused:
        raise Usage(str(refused)) from None
    except TemplateError as refused:
        for diagnostic in refused.diagnostics:
            print(diagnostic, file=sys.stderr)
        return FAILED
    except (SrError, OSError) as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    # What the build said comes first, even where what follows fails:
    # a render that refuses a face is explained by the warning that
    # the face was a substitute, and by the diagnostics of `-v`.
    for line in result.diagnostics:
        print(line, file=sys.stderr)
    for note in result.notes:
        print(f"warning: {note}", file=sys.stderr)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    try:
        if encoding == "pdf":
            document = render_pdf(
                result.printout,
                compress=not given.flags.get("uncompressed"),
            )
            write_document(document, target, out)
        else:
            write_printout(result, target, out, encoding)
    except (SrError, OSError) as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    print(f"{shown(target)}: {summary(result.printout)}", file=sys.stderr)
    return OK


def data_source(given: Arguments) -> Path | TextIO | None:
    """Return where the records come from.

    Standard input was set to read UTF-8 by :func:`utf8_streams`,
    so that it reads as a data file does.  That is :func:`main`'s doing:
    a caller that runs :func:`build` directly, without it, gets standard
    input as Python set it up.

    Args:
        given: The arguments, for ``--data``.

    """
    data = given.flags.get("data")
    if data is None:
        return None
    return sys.stdin if data == "-" else Path(str(data))


def format_of(target: str, asked: str | None) -> str:
    """Return the encoding to write, and complain where the two disagree.

    doc/cli.md#sr-build: the format comes from ``--format``, or from
    what ``--out`` ends in.  ``--format`` names one as an extension
    does, without the dot and in any case.  Over a recognized extension
    that names another format it warns and proceeds, because the file
    it writes will be read back by its extension; over an unrecognized
    one, or none, it is silent, since overriding is what the flag is for.

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
    wanted = FORMAT_NAMES.get(asked.lower())
    if wanted is None:
        raise Usage(f"--format {asked}: expected pdf, jsonl or cbor")
    if named is not None and named != wanted:
        print(
            f"warning: --format {asked} over an output named {suffix}, "
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


def write_printout(
    result: api.Result, target: str, out: TextIO, encoding: str = "jsonl"
) -> None:
    """Write the printout where ``--out`` said, in the encoding asked for.

    The directory it lands in is what its paths are written relative
    to, which is why the destination reaches the writer rather than
    only the stream.  A document going to standard output has no
    directory, and the working directory stands in.

    Args:
        result: What the build produced.
        target: What ``--out`` named.
        out: Where a document written to standard output goes.
        encoding: ``jsonl`` or ``cbor``.

    Raises:
        OSError: Standard output takes no bytes, for a CBOR printout.

    """
    if encoding == "cbor":
        if target == "-":
            write_cbor(result.printout, byte_stream(out, "a CBOR printout"))
            return
        path = Path(target)
        with path.open("wb") as handle:
            write_cbor(result.printout, handle, path.parent)
        return
    if target == "-":
        write_jsonl(result.printout, as_written(out), None)
        return
    path = Path(target)
    base = path.parent
    # newline="": doc/printout.md#encoding ends every line with U+000A,
    # and a platform whose text mode translates that would write CRLF
    # and make the same document two different files.  The handler
    # is the one standard output gets, so the two routes stay one.
    with path.open(
        "w",
        encoding="utf-8",
        errors=PRINTOUT_ERRORS,
        newline="",
    ) as handle:
        write_jsonl(result.printout, handle, base)


def as_written(out: TextIO) -> TextIO:
    """Return a stream that writes a printout as the format spells it.

    A printout is [LF-terminated UTF-8](doc/printout.md#encoding)
    wherever it is written, and on Windows standard output translates
    U+000A into two bytes, so the line ending is set here.  The encoding
    is set with it, because this is also handed streams :func:`main`
    never saw, and so is the error handler: Python resets that to
    ``strict`` whenever the encoding is set and the handler is not.

    Args:
        out: Where a document written to standard output goes.

    """
    return reconfigured(
        out,
        encoding="utf-8",
        errors=PRINTOUT_ERRORS,
        newline="",
    )


def byte_stream(out: TextIO, document: str) -> BinaryIO:
    """Return the byte stream beneath a text stream, for a binary document.

    Whatever the text stream still holds is written first, so
    nothing printed to it earlier lands among the document's bytes.

    Args:
        out: Where a document written to standard output goes.
        document: What is written, as the error names it:
            ``a CBOR printout``, ``a PDF``.

    Raises:
        OSError: The stream has no bytes beneath it, as a test's buffer has not.

    """
    out.flush()
    buffer = getattr(out, "buffer", None)
    if buffer is None:
        said = f"standard output takes no bytes, and {document} is bytes"
        raise OSError(said)
    return buffer  # type: ignore[no-any-return]


def summary(printout: Printout) -> str:
    """Return what the summary line doc/cli.md#sr-build says of a document.

    Its pages and its fonts, always, and its warnings where there
    are any.  What it names is the document rather than the run.
    The file it went to is the caller's to print, because
    the caller is the one that knows what the user wrote.

    Args:
        printout: The document.

    """
    terms = [counted(len(printout.pages), "page"), counted(len(printout.fonts), "font")]
    if printout.warnings:
        terms.append(counted(len(printout.warnings), "warning"))
    return ", ".join(terms)


def counted(value: int, noun: str) -> str:
    """Return a count and its noun, which is plural unless the count is 1.

    Args:
        value: The count.
        noun: What is counted, in the singular.

    """
    return f"{value} {noun if value == 1 else noun + 's'}"


def shown(target: str) -> str:
    """Return an output as the summary line names it.

    Args:
        target: What ``--out`` named.

    """
    return STANDARD_OUTPUT if target == "-" else target


def write_document(document: bytes, target: str, out: TextIO) -> None:
    """Write a rendered PDF where ``--out`` said.

    The document is whole before this is called, so a render that
    failed never reaches here and the previous file stays as it was.

    Args:
        document: The PDF.
        target: What ``--out`` named.
        out: Where a document written to standard output goes.

    Raises:
        OSError: The file will not write, or standard output takes no bytes.

    """
    if target == "-":
        byte_stream(out, "a PDF").write(document)
        return
    Path(target).write_bytes(document)


# -- render -----------------------------------------------------------


def render(arguments: Sequence[str], out: TextIO) -> int:
    """Read a printout and write it as a PDF.

    doc/cli.md#sr-render: the printout is positional, and its extension
    says which encoding to read.  The warnings it carries, recorded when
    it was built, are repeated on standard error, because this is where
    somebody looks at the document; then a summary line that counts
    the pages the PDF has.

    Args:
        arguments: What followed the command.
        out: Where a document written to standard output goes.

    """
    given = parse(arguments, RENDER_FLAGS)
    source = printout_named(given, "render")
    target = text_of(given.flags.get("out"))
    if target is None:
        raise Usage("render needs an output, as -o")
    try:
        printout = read_model(Path(source))
        compress = not given.flags.get("uncompressed")
        document = render_pdf(printout, compress=compress)
        write_document(document, target, out)
    except (SrError, OSError) as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    for warning in printout.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"{shown(target)}: {counted(len(printout.pages), 'page')}", file=sys.stderr)
    return OK


def printout_named(given: Arguments, command: str) -> str:
    """Return the one printout ``render`` or ``inspect`` reads.

    doc/cli.md#--for-a-stream: a printout being read is always a file,
    because the paths inside it resolve against the directory it was
    read from, and standard input has none.

    Args:
        given: The arguments, for the positional one.
        command: Which command it is, for the diagnostic.

    Raises:
        Usage: There is not exactly one, or it is ``-``.

    """
    if len(given.positional) != 1:
        raise Usage(f"{command} takes one printout")
    named = given.positional[0]
    if named == "-":
        raise Usage(
            f"{command} reads a printout from a file, not from standard input: "
            "the paths inside it resolve against the directory it came from"
        )
    return named


# -- inspect ----------------------------------------------------------


def inspect_printout(arguments: Sequence[str], out: TextIO) -> int:
    """Dump a printout as readable text, and hold it to its invariants.

    doc/cli.md#sr-inspect: the encoding comes from the extension,
    ``.cbor`` for CBOR and anything else NDJSON.  Every invariant the
    printout breaks is written to standard error after the dump, and
    then the exit code is 1, whatever ``--pages`` or ``--summary``
    left out of the dump: an inspector that read a broken printout
    without saying so would be the wrong tool.

    A ``--pages`` that is not a list of ranges is a usage error,
    and is refused before the printout is read.

    Args:
        arguments: What followed the command.
        out: Where the dump goes.

    """
    given = parse(arguments, INSPECT_FLAGS)
    name = printout_named(given, "inspect")
    asked = text_of(given.flags.get("pages"))
    try:
        ranges = None if asked is None else page_ranges(asked)
    except BadValue as refused:
        raise Usage(str(refused)) from None
    try:
        records = read_printout(Path(name))
    except (SrError, OSError) as refused:
        print(f"sr: {refused}", file=sys.stderr)
        return FAILED
    wanted = pages_wanted(ranges, len(records) - 1)
    for line in dump(name, records, wanted, bool(given.flags.get("summary"))):
        print(line, file=out)
    broken = violations(records)
    if not broken:
        return OK
    out.flush()
    for one in broken:
        print(f"invariant: {one}", file=sys.stderr)
    said = counted(len(broken), "invariant")
    print(f"sr: {name}: {said} broken", file=sys.stderr)
    return FAILED


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
    template = text_of(given.flags.get("template"))
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
    loaded = load_template(Path(template), options)
    if loaded.report is None or loaded.errors:
        for diagnostic in loaded.errors:
            print(diagnostic, file=sys.stderr)
        return FAILED
    refused = parameters_given(loaded.report, given.params)
    if refused:
        for message in refused:
            print(f"sr: {message}", file=sys.stderr)
        return FAILED
    fonts = resolve_fonts(
        loaded.report,
        given.params,
        strict=bool(given.flags.get("strict-fonts")),
        verbose=bool(given.flags.get("verbose")),
    )
    if not quiet:
        describe(loaded, template, fonts, out)
    if fonts.failures:
        said = counted(len(fonts.failures), "font")
        print(f"sr: {said} did not resolve", file=sys.stderr)
        return FAILED
    return OK


def parameters_given(report: Report, params: dict[str, str]) -> list[str]:
    """Check ``--param`` values against what the template declares.

    A name the template does not declare is a usage error, as it is
    for ``build``; a value that does not parse as its declared type
    is a failure of the check, one line for each.

    Args:
        report: The template that was loaded.
        params: The values given, by name.

    Raises:
        Usage: A name is not declared.

    """
    declared = {one.name: one for one in report.parameters}
    for name in params:
        if name not in declared:
            known = ", ".join(sorted(declared)) or "none"
            raise Usage(
                f"--param {name}: the template declares no such parameter; "
                f"it declares: {known}"
            )
    failures: list[str] = []
    for name, text in params.items():
        parameter = declared[name]
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


def parse(arguments: Sequence[str], flags: Sequence[Flag]) -> Arguments:
    """Read one command's arguments, as doc/cli.md#flags spells them.

    A flag is one dash and its letter, or two dashes and its long name,
    with its value attached after an ``=`` or in the next argument.
    Flags may follow positional arguments, and ``--`` ends them: what
    comes after it is positional, whatever it starts with.  A lone ``-``
    is a positional argument, since it names a stream.

    Args:
        arguments: What followed the command.
        flags: The flags the command takes.

    Raises:
        Usage: A flag is unknown, is spelled with the wrong number
            of dashes, or is missing the value it takes, or a switch
            was given a value other than ``true`` or ``false``.
        Help: ``-h`` or ``--help`` was given.

    """
    known = (*flags, HELP_FLAG)
    names = {one.name: one for one in known}
    letters = {one.letter: one for one in known if one.letter is not None}
    found = Arguments()
    pending = list(arguments)
    while pending:
        argument = pending.pop(0)
        if argument == "--":
            found.positional.extend(pending)
            break
        if argument == "-" or not argument.startswith("-"):
            found.positional.append(argument)
            continue
        if argument.startswith("---"):
            raise Usage(f"{argument!r}: {DASHES}")
        long = argument.startswith("--")
        name, equals, attached = argument[2 if long else 1 :].partition("=")
        flag = (names if long else letters).get(name)
        if flag is None:
            if name in (letters if long else names):
                spelled = respelled(argument)
                raise Usage(f"{argument!r}: {DASHES}, as {spelled}")
            raise Usage(f"unknown flag {argument!r}")
        if flag is HELP_FLAG:
            raise Help
        if not flag.value:
            found.flags[flag.name] = switched(argument, attached if equals else None)
            continue
        value = attached if equals else taken(argument, pending)
        record(found, flag, value)
    return found


def respelled(argument: str) -> str:
    """Return a flag written with the other number of dashes.

    ``--t`` becomes ``-t``, and ``-template`` becomes ``--template``,
    which is what to say to a command line that has the two mixed up.

    Args:
        argument: The flag as it was written.

    """
    return argument[1:] if argument.startswith("--") else "-" + argument


def switched(argument: str, value: str | None) -> bool:
    """Return what a switch was set to.

    Given alone it is on.  ``=true`` and ``=false`` say so outright,
    which is what lets a script that always writes the flag turn it off.

    Args:
        argument: The flag as it was written, for the diagnostic.
        value: What was attached after an ``=``, where anything was.

    Raises:
        Usage: The value is neither ``true`` nor ``false``.

    """
    if value is None:
        return True
    if value not in SWITCH_VALUES:
        raise Usage(f"{argument!r}: a switch takes true or false, or nothing")
    return SWITCH_VALUES[value]


def taken(argument: str, pending: list[str]) -> str:
    """Return the argument after a flag, which is its value.

    Args:
        argument: The flag as it was written, for the diagnostic.
        pending: The arguments still to read.

    Raises:
        Usage: There is no argument after the flag.

    """
    if not pending:
        raise Usage(f"{argument!r} needs a value")
    return pending.pop(0)


def record(found: Arguments, flag: Flag, value: str) -> None:
    """Record one flag's value.

    ``--param`` and ``--accept`` are the repeatable flags,
    and are kept apart from the rest.  Any other flag given twice
    takes the last value given, and one given an empty value
    is a flag not given, so that a script can pass a variable
    that may be empty without a test around the flag.

    Args:
        found: What has been read so far, which this adds to.
        flag: The flag.
        value: Its value, as it was written.

    Raises:
        Usage: A ``--param`` or an ``--accept`` with nothing in it.

    """
    if flag.name == "param":
        parameter(found, value)
        return
    if flag.name == "accept":
        if not value:
            raise Usage("--accept needs a name")
        found.accepted.add(value)
        return
    if value:
        found.flags[flag.name] = value
    else:
        found.flags.pop(flag.name, None)


def parameter(found: Arguments, value: str) -> None:
    """Read one ``--param NAME=VALUE``.

    A parameter given twice is refused rather than taken last-wins:
    two places both think they own that parameter, and one of them
    is wrong.  An ``--accept`` given twice is simply the one name.

    Args:
        found: What has been read so far, which this adds to.
        value: What followed the flag.

    Raises:
        Usage: The value is not ``NAME=VALUE``, names nothing,
            or repeats a name.

    """
    name, equals, text = value.partition("=")
    if not equals or not name:
        raise Usage(f"--param takes NAME=VALUE, and got {value!r}")
    if name in found.params:
        raise Usage(f"--param {name} is given twice")
    found.params[name] = text


COMMANDS = {
    "build": build,
    "inspect": inspect_printout,
    "render": render,
    "validate": validate,
    "version": version,
}
