"""The command lines both engines are held to each other over.

doc/cli.md specifies the shell: how a flag is spelled, what a mistake
in one costs, which stream the output goes to, and what an exit code
means.  Each line here asks one of those questions, and both engines
answer it.  What they are compared on is the exit code, what they wrote
to standard output, what they wrote to the files, and, after a build
or a render that succeeded, the summary line that ends standard error.

A line goes by an identifier, ``cli/`` and then the question,
which the divergence register can name as it names a probe.

The arguments are written with placeholders, filled in per run:

| | |
|---|---|
| ``{template}``, ``{data}`` | the minimal example and its records |
| ``{sakila}``, ``{payments}`` | the sakila example and its records |
| ``{printout}`` | a printout of the minimal example, built once |
| ``{pages}`` | a printout of the invoices example, of seven pages |
| ``{out}`` | a directory of the engine's own, for what it writes |

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.differential.cases import BUILD_TIME, ROOT

__all__ = ["CommandLine", "command_lines"]

# Where the command lines' inputs are, relative to the root,
# which is where both engines run them from.
INPUTS = {
    "template": "example/minimal/minimal.kdl",
    "data": "example/minimal/films.jsonl",
    "sakila": "example/sakila/sakila.kdl",
    "payments": "example/sakila/payments.jsonl",
}

# What makes a build's output the same on both sides.
FIXED = ("--build-time", BUILD_TIME)


@dataclass(frozen=True)
class CommandLine:
    """One command line, to be run by both engines.

    Attributes:
        ident: A stable name for it, ``cli/`` and then the question.
        argv: The arguments after the program name, with placeholders.
        stdin: The file, relative to the root, whose bytes
            standard input holds, where the command reads it.
        files: Whether the files it writes are compared byte for byte.
            A PDF is not, for the reason test_render.py gives, and
            nor is a build whose `BUILD_TIME` is the time it ran.
        output: Whether what it writes to standard output is compared.
            ``help`` is not: doc/cli.md says what it does
            and not what it says.

    """

    ident: str
    argv: tuple[str, ...]
    stdin: str | None = None
    files: bool = True
    output: bool = True

    def arguments(self, out: Path, printouts: dict[str, Path]) -> list[str]:
        """Return the arguments with the placeholders filled in.

        Args:
            out: The engine's own directory, for what it writes.
            printouts: The prebuilt printouts, by placeholder.

        """
        names = dict(INPUTS)
        names.update({key: str(path) for key, path in printouts.items()})
        names["out"] = str(out)
        return [one.format(**names) for one in self.argv]

    def standard_input(self) -> bytes | None:
        """Return what standard input holds, where the line gives it."""
        return None if self.stdin is None else (ROOT / self.stdin).read_bytes()


def line(
    ident: str,
    *argv: str,
    stdin: str | None = None,
    files: bool = True,
    output: bool = True,
) -> CommandLine:
    """Return one command line, named under ``cli/``.

    Args:
        ident: The question, as the part of the name after ``cli/``.
        *argv: The arguments after the program name.
        stdin: As :class:`CommandLine` has it.
        files: As :class:`CommandLine` has it.
        output: As :class:`CommandLine` has it.

    """
    return CommandLine("cli/" + ident, argv, stdin, files, output)


def build(*more: str) -> tuple[str, ...]:
    """Return a build of the minimal example to a printout, and more.

    Args:
        *more: What follows the template and the output.

    """
    return ("build", "-t", "{template}", "-o", "{out}/a.srp.jsonl", *FIXED, *more)


def command_lines() -> list[CommandLine]:
    """Return every command line the two engines are compared over."""
    return [
        # -- how a flag is spelled: doc/cli.md#flags
        line("flags/short", *build("-d", "{data}")),
        line(
            "flags/long",
            "build",
            "--template",
            "{template}",
            "--data",
            "{data}",
            "--out",
            "{out}/a.srp.jsonl",
            *FIXED,
        ),
        line(
            "flags/attached",
            "build",
            "--template={template}",
            "--data={data}",
            "--out={out}/a.srp.jsonl",
            f"--build-time={BUILD_TIME}",
        ),
        line(
            "flags/one-dash-long",
            "build",
            "-template",
            "{template}",
            "-out",
            "{out}/a.srp.jsonl",
            *FIXED,
        ),
        line("flags/one-dash-switch", "validate", "{template}", "-quiet"),
        line(
            "flags/short-attached",
            "build",
            "-t={template}",
            "-o={out}/a.srp.jsonl",
            *FIXED,
        ),
        line(
            "flags/two-dash-short",
            "build",
            "--t",
            "{template}",
            "--o",
            "{out}/a.srp.jsonl",
            *FIXED,
        ),
        line(
            "flags/three-dashes",
            "build",
            "---t",
            "{template}",
            "-o",
            "{out}/a.srp.jsonl",
        ),
        line("flags/unknown", *build("--wobble")),
        line("flags/another-commands-flag", *build("--pages", "1")),
        line("flags/no-value", "build", "-t", "{template}", "-o"),
        line("flags/last-wins", *build("-o", "{out}/b.srp.jsonl")),
        line("flags/empty-is-not-given", *build("-d", "")),
        line("flags/empty-required", "build", "-t", "", "-o", "{out}/a.srp.jsonl"),
        line("flags/after-a-positional", "validate", "{template}", "-q"),
        line("flags/end", "validate", "--", "{template}"),
        line("flags/end-after-flags", "validate", "-q", "--", "{template}"),
        line("flags/end-makes-positional", "validate", "--", "-q"),
        line("flags/end-then-more", *build("--", "extra")),
        line("flags/positional", *build("extra")),
        # -- switches
        line("switches/repeated", *build("--strict-fonts", "--strict-fonts")),
        line("switches/true", *build("--strict-fonts=true")),
        line("switches/false", *build("--strict-fonts=false")),
        line("switches/last-wins", *build("--strict-fonts=false", "--strict-fonts")),
        line("switches/separate-value", *build("--strict-fonts", "true")),
        line("switches/empty-value", *build("--strict-fonts=")),
        line("switches/other-word", *build("--strict-fonts=yes")),
        line("switches/digit-one", *build("--strict-fonts=1")),
        line("switches/digit-zero", *build("--strict-fonts=0")),
        line("switches/letter", *build("--strict-fonts=T")),
        line("switches/upper-case", *build("--strict-fonts=TRUE")),
        # -- help and version: doc/cli.md#subcommands
        line("help/all", "help", output=False),
        line("help/one", "help", "build", output=False),
        line("help/short", "build", "-h", output=False),
        line("help/long", "build", "--help", output=False),
        line("help/one-dash", "build", "-help", output=False),
        line("help/two-dash-short", "build", "--h", output=False),
        line("help/flag-short", "-h", output=False),
        line("help/flag-long", "--help", output=False),
        line("help/flag-one-dash", "-help"),
        line("help/flag-two-dash-short", "--h"),
        line("help/among-arguments", "validate", "--help", "nosuch", output=False),
        line("help/unknown", "help", "frobnicate"),
        line("help/extra", "help", "build", "extra", output=False),
        line("version/command", "version"),
        line("version/flag", "--version"),
        line("version/one-dash", "-version"),
        line("version/argument", "version", "extra"),
        line("commands/none"),
        line("commands/unknown", "frobnicate"),
        line("commands/flag-first", "-t", "{template}"),
        # -- parameters: doc/cli.md#flags
        line(
            "param/declared",
            "validate",
            "{sakila}",
            "--param",
            "period_start=2005-06-01",
            "-q",
        ),
        line(
            "param/attached",
            "validate",
            "{sakila}",
            "--param=period_start=2005-06-01",
            "-q",
        ),
        line(
            "param/undeclared",
            "build",
            "-t",
            "{sakila}",
            "-o",
            "{out}/a.srp.jsonl",
            "--param",
            "nosuch=1",
        ),
        line("param/undeclared-check", "validate", "{sakila}", "--param", "nosuch=1"),
        line("param/undeclared-none", *build("--param", "x=1")),
        line(
            "param/twice",
            "validate",
            "{sakila}",
            "--param",
            "period_start=2005-01-01",
            "--param",
            "period_start=2006-01-01",
        ),
        line(
            "param/twice-before-the-template",
            "build",
            "-t",
            "nosuch.kdl",
            "-o",
            "{out}/a.srp.jsonl",
            "--param",
            "x=1",
            "--param",
            "x=2",
        ),
        line("param/no-equals", "validate", "{sakila}", "--param", "period_start"),
        line("param/no-name", *build("--param", "=1")),
        line("param/empty", *build("--param", "")),
        line(
            "param/bad-value",
            "validate",
            "{sakila}",
            "--param",
            "period_start=yesterday",
        ),
        line(
            "param/bad-value-build",
            "build",
            "-t",
            "{sakila}",
            "-d",
            "{payments}",
            "-o",
            "{out}/a.srp.jsonl",
            "--param",
            "period_start=yesterday",
        ),
        # -- the build time: doc/cli.md#sr-build
        line("build-time/not-a-time", *build("--build-time", "yesterday")),
        line("build-time/date-only", *build("--build-time", "2026-08-04")),
        line(
            "build-time/lower-case",
            *build("--build-time", "2026-08-04t09:12:44z"),
        ),
        line(
            "build-time/before-the-template",
            "build",
            "-t",
            "nosuch.kdl",
            "-o",
            "{out}/a.srp.jsonl",
            "--build-time",
            "yesterday",
        ),
        line(
            "build-time/offset",
            *build("--build-time", "2026-08-04T09:12:44+03:00"),
        ),
        line(
            "build-time/fraction",
            *build("--build-time", "2026-08-04T09:12:44.5Z"),
        ),
        line("build-time/empty", *build("--build-time", ""), files=False),
        # -- the output format: doc/cli.md#sr-build
        line(
            "format/unknown-extension", "build", "-t", "{template}", "-o", "{out}/a.txt"
        ),
        line("format/no-extension", "build", "-t", "{template}", "-o", "{out}/a"),
        line("format/json", "build", "-t", "{template}", "-o", "{out}/a.json"),
        line(
            "format/upper-case-extension",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.JSONL",
            *FIXED,
        ),
        line(
            "format/ndjson-extension",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.ndjson",
            *FIXED,
        ),
        line(
            "format/pdf",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.PDF",
            *FIXED,
            files=False,
        ),
        line(
            "format/over-an-unknown-extension",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.txt",
            "--format",
            "jsonl",
            *FIXED,
        ),
        line(
            "format/over-a-known-extension",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.jsonl",
            "--format",
            "cbor",
            *FIXED,
        ),
        line(
            "format/agreeing",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.ndjson",
            "--format",
            "jsonl",
            *FIXED,
        ),
        line(
            "format/ndjson",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.x",
            "--format",
            "ndjson",
            *FIXED,
        ),
        line(
            "format/upper-case",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.x",
            "--format",
            "JSONL",
            *FIXED,
        ),
        line(
            "format/unknown",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.x",
            "--format",
            "txt",
        ),
        line(
            "format/empty",
            "build",
            "-t",
            "{template}",
            "-o",
            "{out}/a.x",
            "--format",
            "",
        ),
        # -- streams: doc/cli.md#-for-a-stream
        line("streams/out-needs-a-format", "build", "-t", "{template}", "-o", "-"),
        line(
            "streams/out",
            "build",
            "-t",
            "{template}",
            "-d",
            "{data}",
            "-o",
            "-",
            "--format",
            "jsonl",
            *FIXED,
        ),
        line(
            "streams/data",
            *build("-d", "-"),
            stdin=INPUTS["data"],
        ),
        line("streams/render-from-stdin", "render", "-", "-o", "{out}/a.pdf"),
        line("streams/inspect-from-stdin", "inspect", "-"),
        # -- render: doc/cli.md#sr-render
        line(
            "render/printout", "render", "{printout}", "-o", "{out}/a.pdf", files=False
        ),
        line(
            "render/flags-first",
            "render",
            "-o",
            "{out}/a.pdf",
            "{printout}",
            "--uncompressed",
            files=False,
        ),
        line("render/no-out", "render", "{printout}"),
        line("render/empty-out", "render", "{printout}", "-o", ""),
        line("render/two", "render", "{printout}", "{printout}", "-o", "{out}/a.pdf"),
        line(
            "render/param",
            "render",
            "{printout}",
            "-o",
            "{out}/a.pdf",
            "--param",
            "x=1",
        ),
        line(
            "render/format",
            "render",
            "{printout}",
            "-o",
            "{out}/a.pdf",
            "--format",
            "pdf",
        ),
        line("render/missing", "render", "nosuch.srp.jsonl", "-o", "{out}/a.pdf"),
        # -- inspect: doc/cli.md#sr-inspect
        line("inspect/printout", "inspect", "{pages}"),
        line("inspect/summary", "inspect", "{pages}", "--summary"),
        line("inspect/summary-first", "inspect", "--summary", "{pages}"),
        line("inspect/summary-true", "inspect", "{pages}", "--summary=true"),
        line("inspect/page", "inspect", "{pages}", "--pages", "2"),
        line("inspect/pages", "inspect", "{pages}", "--pages=1,3-4,6-"),
        line("inspect/every-page", "inspect", "{pages}", "--pages", "-"),
        line("inspect/up-to", "inspect", "{pages}", "--pages=-3"),
        line("inspect/past-the-end", "inspect", "{pages}", "--pages", "9"),
        line("inspect/spaces", "inspect", "{pages}", "--pages", " 2 , 4 - 5 "),
        line("inspect/empty-pages", "inspect", "{pages}", "--pages", ""),
        line("inspect/word", "inspect", "{pages}", "--pages", "first"),
        line("inspect/zero", "inspect", "{pages}", "--pages", "0"),
        line("inspect/backwards", "inspect", "{pages}", "--pages", "3-1"),
        line("inspect/gap", "inspect", "{pages}", "--pages", "1,,2"),
        line("inspect/three-ends", "inspect", "{pages}", "--pages", "1-2-3"),
        line("inspect/plus-sign", "inspect", "{pages}", "--pages", "+1"),
        line(
            "inspect/pages-before-the-file",
            "inspect",
            "nosuch.srp.jsonl",
            "--pages",
            "first",
        ),
        line("inspect/out", "inspect", "{pages}", "-o", "x"),
        line("inspect/param", "inspect", "{pages}", "--param", "x=1"),
        line("inspect/two", "inspect", "{printout}", "{printout}"),
        line("inspect/missing", "inspect", "nosuch.srp.jsonl"),
        # -- validate: doc/cli.md#sr-validate
        line("validate/flag", "validate", "-t", "{template}"),
        line("validate/flag-and-positional", "validate", "-t", "{template}", "{data}"),
        line("validate/two", "validate", "{template}", "{template}"),
        line("validate/none", "validate"),
        line("validate/quiet", "validate", "{template}", "--quiet"),
        line("validate/out", "validate", "{template}", "-o", "x"),
        line("validate/build-time", "validate", "{template}", *FIXED),
        line("validate/allow-overflow", "validate", "{template}", "--allow-overflow"),
        line("validate/missing", "validate", "nosuch.kdl"),
        line("validate/accept", "validate", "{template}", "--accept", "wobble"),
        line("validate/strict-names", "validate", "{template}", "--strict-names"),
    ]
