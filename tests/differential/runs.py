"""Running one command line through either engine, for what it printed.

The printout comparison runs both engines as programs, because what
it compares is a file.  The reports and the command line are about
the streams and the exit code, and run often: a check and a dump
for every case in the corpus, and a table of command lines on top.
So this engine runs in the test's own process, through
:func:`sr.cli.main`, which is everything ``sr.py`` does but start
an interpreter, and the reference runs as the program it is.

``main`` writes to standard output through the stream it is given,
and to standard error through :data:`sys.stderr`.  Both are byte
buffers here, behind text streams set up as an interpreter sets up
a pipe, newline translation included, so that what this engine
would write on the machine is what is compared.

"""

from __future__ import annotations

import io
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from sr.cli import main
from tests.differential.cases import ROOT
from tests.differential.engines import BUILD_TIMEOUT, Engine

__all__ = ["Outcome", "run_local", "run_reference"]


@dataclass(frozen=True)
class Outcome:
    """What one run of a command line produced.

    Attributes:
        code: The exit code.
        stdout: What it wrote to standard output, as bytes.
        stderr: What it wrote to standard error, decoded.

    """

    code: int
    stdout: bytes
    stderr: str


def run_reference(
    oracle: Engine, argv: Sequence[str], stdin: bytes | None = None
) -> Outcome:
    """Run a command line through the reference, from the repository root.

    Args:
        oracle: The reference engine.
        argv: The arguments after the program name.
        stdin: What standard input holds, where the command reads it.

    """
    done = subprocess.run(  # the argv is ours, not a shell string
        [*oracle.command, *argv],
        cwd=ROOT,
        input=b"" if stdin is None else stdin,
        capture_output=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )
    return Outcome(
        done.returncode,
        done.stdout,
        done.stderr.decode("utf-8", "replace"),
    )


def run_local(argv: Sequence[str], stdin: bytes | None = None) -> Outcome:
    """Run a command line through this engine, from the repository root.

    Args:
        argv: The arguments after the program name.
        stdin: What standard input holds, where the command reads it.

    """
    out = io.BytesIO()
    err = io.BytesIO()
    # The encoding an interpreter would pick for a pipe on Windows,
    # so that a stream the command forgot to set up shows as one.
    stdout = io.TextIOWrapper(out, encoding="cp1252")
    stderr = io.TextIOWrapper(err, encoding="cp1252", errors="backslashreplace")
    source = io.TextIOWrapper(io.BytesIO(stdin or b""), encoding="cp1252")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        patch.setattr(sys, "stderr", stderr)
        patch.setattr(sys, "stdin", source)
        code = main(list(argv), stdout)
        stdout.flush()
        stderr.flush()
    return Outcome(code, out.getvalue(), err.getvalue().decode("utf-8", "replace"))
