"""The examples build clean, run the way README.md tells a reader to run them.

Clean means what doc/cli.md#sr-build gives a build to say about itself:
the summary line and nothing else on standard error, so no warning and
no note about the template; a printout that keeps its own invariants,
which ``sr.py inspect`` says without a word; and a PDF a reader can open,
as the tests' own reader and ``qpdf --check`` see it.

The command lines are read out of README.md's "Running the examples"
rather than written again here, so that the README cannot drift from what
is tested.  Each runs as a reader would run it, through ``sr.py``, from the
repository root, with its output moved into a directory of the test's own.
A printout is also rendered, since that is the other route to a PDF.

"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.pdfcheck import check

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ENTRY_POINT = ROOT / "sr.py"

# Generous, as for the differential builds: a cold cache is slow.
TIMEOUT = 300.0


def readme_commands() -> list[tuple[str, ...]]:
    """Return the example command lines README.md gives, without the program.

    Raises:
        AssertionError: The section has no command line in it.

    """
    text = README.read_text(encoding="utf-8")
    section = text.split("\n## Running the examples\n", 1)[1].split("\n## ", 1)[0]
    found = [
        tuple(shlex.split(line)[2:])
        for line in section.splitlines()
        if line.startswith("python sr.py ")
    ]
    assert found, "README.md runs no example"
    return found


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run ``sr.py`` from the repository root.

    Args:
        *argv: The arguments after the program name.

    """
    return subprocess.run(  # the argv is ours, not a shell string
        [sys.executable, str(ENTRY_POINT), *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT,
        check=False,
    )


def moved(argv: tuple[str, ...], directory: Path) -> tuple[list[str], Path]:
    """Return a command line whose output lands in ``directory``.

    Args:
        argv: The command line, as README.md gives it.
        directory: Where its output goes instead.

    """
    arguments = list(argv)
    at = arguments.index("-o") + 1
    out = directory / arguments[at]
    arguments[at] = str(out)
    return arguments, out


def clean(done: subprocess.CompletedProcess[str], out: Path) -> None:
    """Assert that a build or a render said nothing but its summary line.

    Args:
        done: What the run produced.
        out: The output it was asked for.

    """
    assert done.returncode == 0, done.stderr
    lines = done.stderr.splitlines()
    assert len(lines) == 1, done.stderr
    assert lines[0].startswith(f"{out}: "), lines[0]
    assert "warning" not in lines[0], lines[0]


@pytest.mark.parametrize(
    "argv", readme_commands(), ids=lambda argv: argv[argv.index("-o") + 1]
)
def test_an_example_builds_clean(argv: tuple[str, ...], tmp_path: Path) -> None:
    arguments, out = moved(argv, tmp_path)
    clean(run(*arguments), out)
    if out.suffix == ".pdf":
        check(out.read_bytes(), tmp_path / "checked.pdf")
        return
    inspected = run("inspect", str(out))
    assert inspected.returncode == 0, inspected.stderr
    assert inspected.stderr == ""
    pdf = tmp_path / "rendered.pdf"
    clean(run("render", str(out), "-o", str(pdf)), pdf)
    check(pdf.read_bytes(), tmp_path / "checked.pdf")


def test_the_readme_runs_every_example() -> None:
    """The three reports, and sakila narrowed by its parameters."""
    templates = [argv[argv.index("-t") + 1] for argv in readme_commands()]
    assert sorted(set(templates)) == [
        "example/invoices/invoices.kdl",
        "example/minimal/minimal.kdl",
        "example/sakila/sakila.kdl",
    ]
    assert any("--param" in argv for argv in readme_commands())
