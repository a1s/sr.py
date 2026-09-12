"""The corpus the two engines are compared over.

A case is one template, its data, its parameters, and the flags that make
a build reproducible.  The same case produces the argument list for both
engines, so nothing about the comparison can drift between them.

Two sources feed the corpus: the example reports in ``example/``,
and the probes in ``probes/`` -- one template per specification question,
added by respective tests as each question is answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BUILD_TIME",
    "Case",
    "corpus",
    "example_cases",
    "probe_cases",
    "within_root",
]

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = Path(__file__).resolve().parent / "probes"

# Pinned so the printout header's ``built`` field is the same on both sides.
# The value is the one doc/printout.md uses in its example.
BUILD_TIME = "2026-08-04T09:12:44Z"

# Every case builds strictly: only fonts the template names by path resolve,
# so the output does not depend on what is installed on the machine.
COMMON_FLAGS: tuple[str, ...] = ("--build-time", BUILD_TIME, "--strict-fonts")

# The extension for each printout encoding, per doc/cli.md.
SUFFIXES = {"jsonl": ".srp.jsonl", "cbor": ".srp.cbor"}

TEMPLATE_SUFFIX = ".kdl"

# A probe template that exists to be pulled in by another one,
# rather than to be built on its own.
#
# Both this and the sidecar lookup work on the name with `.kdl` removed
# rather than on `Path.with_suffix`, which replaces the last extension
# instead of appending: for `strings.v2.kdl` it would look for
# `strings.jsonl`, quietly binding whatever unrelated file was
# sitting there.  A probe that builds the wrong data is worse
# than one that is not picked up at all.
INCLUDE_MARKER = ".inc"

# The records a probe gets when it does not bring its own.
#
# Nearly every probe wants the same thing: one record, whose contents
# it never reads, so that the detail band runs once.  Committing that
# file eighteen times over says the eighteen might differ, and a reader
# has to diff them to find out they do not.  One file says it once.
#
# A probe that wants no records at all ships an empty ``NAME.jsonl``,
# which is the same thing to the engine as passing no data.
SHARED_RECORDS = "records.jsonl"


@dataclass(frozen=True)
class Case:
    """One reproducible build, to be run by both engines.

    An identifier appears in the test id, in a failure report, and in the
    divergence register's patterns, so renaming one is a deliberate act.

    When data is None, the template runs with empty record sequence,
    producing title, header, footer, summary, and no detail bands.

    Attributes:
        ident: A stable name for this case.
        template: The template, relative to the repository root.
        data: The records, relative to the root, or None.
        params: ``--param`` values, in the order the command line takes them.
        flags: Further engine flags, appended after the common ones.
        encoding: The printout encoding to compare; a key of SUFFIXES.

    """

    ident: str
    template: Path
    data: Path | None = None
    params: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    encoding: str = "jsonl"

    def __post_init__(self) -> None:
        """Reject an encoding no extension names."""
        if self.encoding not in SUFFIXES:
            raise ValueError(
                f"case {self.ident}: unknown encoding {self.encoding!r}; "
                f"expected one of {', '.join(sorted(SUFFIXES))}"
            )

    @property
    def suffix(self) -> str:
        """The extension the output file needs for its encoding."""
        return SUFFIXES[self.encoding]

    def argv(self, out: Path) -> list[str]:
        """Return the ``build`` arguments that write this case to ``out``.

        The engine's own name is not included: the caller puts the
        reference binary or the Python entry point in front of it.

        """
        arguments = ["build", "--template", str(ROOT / self.template)]
        if self.data is not None:
            arguments += ["--data", str(ROOT / self.data)]
        arguments += ["--out", str(out)]
        for value in self.params:
            arguments += ["--param", value]
        return arguments + list(COMMON_FLAGS) + list(self.flags)

    def missing_inputs(self) -> list[Path]:
        """Return the case's input files that are not on disk."""
        wanted = [self.template] + ([self.data] if self.data else [])
        return [path for path in wanted if not (ROOT / path).is_file()]


def within_root(path: Path) -> Path:
    """Return ``path`` relative to the repository root where it lies inside it.

    Paths are kept relative so a case identifier and a failure message read
    the same on every machine.  A probe directory pointed somewhere else --
    which only the harness' own tests do -- stays absolute, and joining it
    onto the root still yields itself.

    """
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def example_cases() -> list[Case]:
    """Return the cases built from the reports in ``example/``.

    Between them the two templates use every node in the format
    and all twelve ``calc`` modes, so they are the broadest single
    comparison the corpus has.

    """
    sakila = Path("example/sakila/sakila.kdl")
    invoices = Path("example/invoices/invoices.kdl")
    return [
        Case(
            ident="example/sakila",
            template=sakila,
            data=Path("example/sakila/payments.jsonl"),
        ),
        Case(
            # The same report narrowed by its date parameters, which is
            # what exercises `--param` and a `printwhen` that goes both ways.
            ident="example/sakila-june",
            template=sakila,
            data=Path("example/sakila/payments.jsonl"),
            params=("period_start=2005-06-01", "period_end=2005-07-01"),
        ),
        Case(
            ident="example/invoices",
            template=invoices,
            data=Path("example/invoices/invoices.jsonl"),
        ),
    ]


@dataclass(frozen=True)
class Sidecar:
    """What a probe's ``.args`` file says, split into the two kinds."""

    params: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


def read_sidecar(path: Path) -> Sidecar:
    """Read a probe's ``.args`` file.

    One argument per line.  A line starting with ``-`` is passed through
    as written; any other line is a ``NAME=VALUE`` parameter.
    Blank lines and lines starting with ``#`` are ignored.

    """
    params: list[str] = []
    flags: list[str] = []
    if not path.is_file():
        return Sidecar()
    for line in path.read_text(encoding="utf-8").splitlines():
        argument = line.strip()
        if not argument or argument.startswith("#"):
            continue
        (flags if argument.startswith("-") else params).append(argument)
    return Sidecar(tuple(params), tuple(flags))


def probe_cases(directory: Path = PROBE_DIR) -> list[Case]:
    """Return one case per probe template under ``directory``.

    A probe is ``NAME.kdl``, optionally beside ``NAME.jsonl`` for its records
    and ``NAME.args`` for its parameters and flags.  ``NAME.inc.kdl``
    is a template another probe pulls in, and is not a case of its own.

    A probe without a ``NAME.jsonl`` of its own gets ``records.jsonl``
    from the top of the probe directory, which is one record that reads
    the same to every probe that does not care what its record holds.
    An empty ``NAME.jsonl`` is how a probe says it wants no records.

    Each open specification question is to be answered with a probe;
    dropping the files in is all it takes to have them compared from then on.

    """
    if not directory.is_dir():
        return []
    shared = directory / SHARED_RECORDS
    cases: list[Case] = []
    for template in sorted(directory.rglob("*.kdl")):
        base = template.name.removesuffix(TEMPLATE_SUFFIX)
        if base.endswith(INCLUDE_MARKER):
            continue
        data = template.with_name(base + ".jsonl")
        if not data.is_file():
            data = shared
        sidecar = read_sidecar(template.with_name(base + ".args"))
        relative = template.relative_to(directory).with_name(base)
        cases.append(
            Case(
                ident="probe/" + relative.as_posix(),
                template=within_root(template),
                data=within_root(data) if data.is_file() else None,
                params=sidecar.params,
                flags=sidecar.flags,
            )
        )
    return cases


def corpus() -> list[Case]:
    """Return every case, examples first and probes after."""
    return example_cases() + probe_cases()
