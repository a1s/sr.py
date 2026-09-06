"""Running a case through an engine.

Both engines take the same arguments, so a case is turned into one argument
list and only the command in front of it differs.  That is deliberate:
a difference in how the two are invoked would show up as a difference
in their output, and be attributed to the engine.

"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.differential.cases import ROOT, Case
from tests.differential.reference import ReferenceBinary, ReferenceUnavailable, indent

__all__ = [
    "BUILD_TIMEOUT",
    "UNAVAILABLE",
    "Build",
    "BuildFailed",
    "Engine",
    "EngineNotImplemented",
    "EngineUnavailable",
    "local_engine",
    "reference_engine",
    "run",
]

# Generous: the examples build in well under a second, but a first run on
# a cold filesystem cache is slower, and a hung engine should still report.
BUILD_TIMEOUT = 300.0

# The entry point doc/cli.md describes.
ENTRY_POINT = ROOT / "sr.py"


class EngineUnavailable(Exception):
    """An engine cannot be run here, so its comparisons are skipped.

    This is about the machine, not about the code: a toolchain that
    is not installed, a binary that will not start.  Under
    ``--differential-required`` it is a failure, because on a machine
    that is supposed to have both engines it means one of them is broken.

    """


class EngineNotImplemented(EngineUnavailable):
    """An engine does not exist yet: a milestone away rather than a fault.

    Kept apart from :class:`EngineUnavailable` so that
    ``--differential-required`` can insist on the oracle without insisting
    on an engine the plan has not reached.  This is what the Python side
    raises until M6, and nothing raises it after: once ``sr.py`` is committed,
    a checkout without it is a broken checkout.

    """


# What the fixtures catch to turn into a skip.
UNAVAILABLE = (EngineUnavailable, ReferenceUnavailable)


class BuildFailed(Exception):
    """An engine ran and refused the case.

    Unlike :class:`EngineUnavailable` this is a real failure: the
    engine exists, it was asked to build something, and it would not.

    """


@dataclass(frozen=True)
class Engine:
    """One engine, ready to run.

    Attributes:
        name: ``reference`` or ``local``; also the output file's stem.
        command: What goes in front of a case's arguments.
        version: What the engine says its version is.

    """

    name: str
    command: tuple[str, ...]
    version: str

    def __str__(self) -> str:
        """Name the engine and the version it reports."""
        return f"{self.name} ({self.version})"


@dataclass(frozen=True)
class Build:
    """What one engine produced for one case."""

    engine: Engine
    case: Case
    path: Path
    printout: bytes
    stdout: str
    stderr: str


def reference_engine(binary: ReferenceBinary) -> Engine:
    """Return the engine that runs the Go reference binary."""
    return Engine("reference", (str(binary.path),), binary.version)


def local_engine() -> Engine:
    """Return the engine that runs this repository's entry point.

    Raises:
        EngineNotImplemented: while there is no entry point at all.
        EngineUnavailable: when the entry point exists but will not run.

    """
    if not ENTRY_POINT.is_file():
        raise EngineNotImplemented(
            f"{ENTRY_POINT.name} does not exist yet; the Python engine "
            "produces its first printout in M6"
        )
    command = (sys.executable, str(ENTRY_POINT))
    try:
        completed = subprocess.run(  # the argv is ours, not a shell string
            [*command, "version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
            check=False,
        )
    except OSError as failure:
        raise EngineUnavailable(f"{ENTRY_POINT} would not run: {failure}") from None
    if completed.returncode != 0:
        raise EngineUnavailable(
            f"{ENTRY_POINT.name} version exited {completed.returncode}:\n"
            + indent(completed.stderr or completed.stdout)
        )
    return Engine("local", command, completed.stdout.strip())


def run(engine: Engine, case: Case, directory: Path) -> Build:
    """Build ``case`` with ``engine``, into ``directory``.

    Both engines write into one directory, because a printout's paths are
    written relative to where it lands: two output files at different depths
    would differ in every font and image path for that reason alone.

    Raises:
        BuildFailed: when the engine exits non-zero, or writes nothing.

    """
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / (engine.name + case.suffix)
    arguments = [*engine.command, *case.argv(out)]
    try:
        completed = subprocess.run(  # the argv is ours, not a shell string
            arguments,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise BuildFailed(
            f"{engine.name} did not finish {case.ident} in {BUILD_TIMEOUT:.0f}s"
        ) from None
    if completed.returncode != 0:
        raise BuildFailed(
            f"{engine.name} exited {completed.returncode} on {case.ident}\n"
            f"  {' '.join(arguments)}\n" + indent(completed.stderr or completed.stdout)
        )
    if not out.is_file():
        raise BuildFailed(
            f"{engine.name} reported success on {case.ident} but wrote no {out.name}"
        )
    return Build(
        engine=engine,
        case=case,
        path=out,
        printout=out.read_bytes(),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
