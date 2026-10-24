"""The command line, held to the reference's, one command line at a time.

Each line in :mod:`tests.differential.command_lines` is run by both
engines, each writing into a directory of its own, and the two runs
are compared on four things: the exit code; what went to standard
output; which files were written, and what is in them; and, after
a build or a render that succeeded, the summary line it ended with.

Standard error is otherwise not compared, because it carries the
engine's own diagnostics, worded as each engine words them.  The
summary line is the exception, since doc/cli.md#sr-build spells it.

What the reference writes is stamped with this engine's version
in place of its own before it is compared, for the reason
:func:`~tests.differential.diff.restamped` gives.

A line the divergence register names is expected to differ,
and fails once it does not, as a probe does.

"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.differential.cases import BUILD_TIME
from tests.differential.command_lines import CommandLine, command_lines
from tests.differential.diff import restamped
from tests.differential.engines import Engine
from tests.differential.register import Register
from tests.differential.runs import Outcome, run_local, run_reference
from tests.differential.test_reports import judged, output_differences

# The commands that end a run that succeeded with a summary line.
SUMMARIZED = {"build", "render"}


@pytest.fixture(scope="session")
def printouts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Return the printouts a command line reads, built once, by this engine.

    The minimal example, as ``{printout}``, and the invoices example,
    as ``{pages}``: seven pages, three of them landscape pages a subreport
    made, which is what ``--pages`` wants to choose among.  ``{gaps}``
    is the invoices printout with its pages numbered 1, 2, and then
    5 to 9, which is a printout whose numbering is broken, the kind
    an open range has to reach past the page count in.

    """
    directory = tmp_path_factory.mktemp("printouts")
    sources = {
        "printout": ("example/minimal/minimal.kdl", "example/minimal/films.jsonl"),
        "pages": ("example/invoices/invoices.kdl", "example/invoices/invoices.jsonl"),
    }
    built = {}
    for key, (template, data) in sources.items():
        path = directory / f"{key}.srp.jsonl"
        argv = ["build", "-t", template, "-d", data, "-o", str(path)]
        outcome = run_local([*argv, "--build-time", BUILD_TIME, "--strict-fonts"])
        assert outcome.code == 0, outcome.stderr
        built[key] = path
    built["gaps"] = renumbered(built["pages"], directory / "gaps.srp.jsonl")
    return built


def renumbered(source: Path, target: Path) -> Path:
    """Write a printout with its pages numbered 1, 2, and then on from 5.

    Args:
        source: The printout to copy.
        target: Where to write the copy.

    """
    numbers = iter([1, 2, *range(5, 1000)])
    lines = source.read_bytes().decode("utf-8").splitlines()
    pages = [json.loads(one) for one in lines[1:]]
    for page in pages:
        page["number"] = next(numbers)
    written = [lines[0], *(json.dumps(one, separators=(",", ":")) for one in pages)]
    target.write_bytes(("\n".join(written) + "\n").encode("utf-8"))
    return target


@pytest.mark.differential
@pytest.mark.parametrize("command", command_lines(), ids=lambda command: command.ident)
def test_command_lines_agree(
    command: CommandLine,
    oracle: Engine,
    local: Engine,
    printouts: dict[str, Path],
    tmp_path: Path,
    register: Register,
    pending: Register,
) -> None:
    stdin = command.standard_input()
    theirs_out = tmp_path / "reference"
    ours_out = tmp_path / "local"
    theirs_out.mkdir()
    ours_out.mkdir()
    theirs = run_reference(oracle, command.arguments(theirs_out, printouts), stdin)
    ours = run_local(command.arguments(ours_out, printouts), stdin)
    found = differences(
        command,
        (theirs, theirs_out),
        (ours, ours_out),
        (oracle.version, local.version),
    )
    judged(command.ident, found, register, pending)


def differences(
    command: CommandLine,
    theirs: tuple[Outcome, Path],
    ours: tuple[Outcome, Path],
    versions: tuple[str, str],
) -> list[str]:
    """Return how two runs of one command line differ, one line each.

    Args:
        command: The command line.
        theirs: What the reference did, and the directory it wrote into.
        ours: What this engine did, and the directory it wrote into.
        versions: The version each engine reports,
            the reference's and then this engine's.

    """
    (left, left_out), (right, right_out) = theirs, ours
    found = []
    if left.code != right.code:
        found.append(f"  exit {left.code} in the reference, {right.code} here")
        found.extend(f"  reference: {one}" for one in left.stderr.splitlines())
        found.extend(f"  here: {one}" for one in right.stderr.splitlines())
    if command.output:
        stdout = restamped(left.stdout, *versions)
        found.extend(output_differences(stdout, right.stdout))
    left_files = sorted(one.name for one in left_out.iterdir())
    right_files = sorted(one.name for one in right_out.iterdir())
    if left_files != right_files:
        found.append(f"  files written: {left_files} there, {right_files} here")
    elif command.files:
        for name in left_files:
            written = restamped((left_out / name).read_bytes(), *versions)
            if written != (right_out / name).read_bytes():
                found.append(f"  {name} differs")
    if left.code == right.code == 0 and command.argv[0] in SUMMARIZED:
        said = summary_line(left, left_out), summary_line(right, right_out)
        if said[0] != said[1]:
            found.append(f"  summary line: {said[0]!r} there, {said[1]!r} here")
    return found


def summary_line(outcome: Outcome, out: Path) -> str:
    """Return the line a run ended standard error with, its directory named.

    Each engine writes into a directory of its own, and the line
    names the output as the command line did, so the directory
    is replaced by the placeholder it was written as.

    Args:
        outcome: What the run printed.
        out: The directory it wrote into.

    """
    lines = outcome.stderr.splitlines()
    return lines[-1].replace(str(out), "{out}") if lines else ""
