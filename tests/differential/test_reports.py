"""The two reports, held to the reference's: the check and the dump.

doc/cli.md specifies what ``validate`` and ``inspect`` print,
so both are compared byte for byte, as a printout is: the exit
code and standard output.  Standard error is not, because what
it carries is the engine's own diagnostics, worded as each engine
words them; doc/cli.md fixes which stream they go to and no more.

The check runs over every case's template, with the case's
parameters and its ``--strict-fonts``, from the repository root.
The dump is of the printout **this** engine built, inspected
by both: a printout the two engines disagree on is the printout
comparison's to report, and dumping one document with both
dumpers keeps a difference here a difference between the dumpers.

A report goes by its own identifier, ``validate/`` or ``inspect/``
and then the case's, so the register can name a report that differs
while the printout agrees.  A case this engine does not build has
no dump to compare, and is skipped, since its printout test says why.

"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest

from tests.differential.cases import Case, inspected, report_cases, validated
from tests.differential.engines import Engine
from tests.differential.register import Register
from tests.differential.runs import Outcome, run_local, run_reference
from tests.differential.test_corpus import expected, unbuilt

# The format the reference writes on an image mark's line of the dump,
# right after the box, which it reads from the mark's `type`: a field
# doc/printout.md#image has no longer, and a printout of this engine's
# does not carry, so the reference writes it empty.
IMAGE_TYPE = re.compile(r"^( *image  box \S+ \S+)  [a-z]*(?=  )", re.MULTILINE)


def without_image_types(dump: str) -> str:
    """Return a dump with the reference's image format column dropped.

    The one column the comparison does not hold the reference to,
    for the reason :func:`tests.differential.test_corpus.without_image_types`
    drops the field from its printout: `image-type-removed` registers it,
    and comparing it would leave every case with an image differing.

    """
    return IMAGE_TYPE.sub(r"\1", dump)


@pytest.mark.differential
@pytest.mark.parametrize("case", report_cases(), ids=validated)
def test_checks_agree(
    case: Case,
    oracle: Engine,
    register: Register,
    pending: Register,
) -> None:
    argv = case.validate_argv()
    theirs = run_reference(oracle, argv)
    ours = run_local(argv)
    hold(validated(case), theirs, ours, register, pending)


@pytest.mark.differential
@pytest.mark.parametrize("case", report_cases(), ids=inspected)
def test_dumps_agree(
    case: Case,
    oracle: Engine,
    build_directory: Path,
    register: Register,
    pending: Register,
) -> None:
    printout = build_directory / ("local" + case.suffix)
    if run_local(case.argv(printout)).code != 0:
        pytest.skip(f"this engine does not build {case.ident}")
    argv = ["inspect", str(printout)]
    theirs = run_reference(oracle, argv)
    ours = run_local(argv)
    hold(inspected(case), theirs, ours, register, pending)


def hold(
    ident: str,
    theirs: Outcome,
    ours: Outcome,
    register: Register,
    pending: Register,
) -> None:
    """Hold one report to the reference's, or to the register's word for it.

    Args:
        ident: The report's identifier.
        theirs: What the reference printed.
        ours: What this engine printed.
        register: The divergences.
        pending: What waits for the reference.

    """
    judged(ident, differences(theirs, ours), register, pending)


def judged(
    ident: str,
    found: list[str],
    register: Register,
    pending: Register,
) -> None:
    """Pass, fail, or mark expected one comparison, by what the lists say.

    A comparison neither list names has to agree.  One that a list
    names has to differ, and is reported as the entry describes it;
    one that has stopped differing fails, asking for its entry
    to be removed, which is how both lists shrink by themselves.

    Args:
        ident: What was compared.
        found: How the two engines differed, one line each.
        register: The divergences.
        pending: What waits for the reference.

    """
    entry = register.entry_for(ident)
    waiting = None if entry is not None else pending.entry_for(ident)
    detail = f"{ident}:\n" + "\n".join(found)
    if entry is None and waiting is None:
        assert not found, detail
        return
    listed = entry if entry is not None else waiting
    assert listed is not None
    if not found:
        file = "divergences.toml" if entry is not None else "pending.toml"
        word = "divergence" if entry is not None else "pending entry"
        pytest.fail(
            f"{ident} now agrees, so the {word} {listed.ident!r} "
            f"has done its work: remove it from {file}",
            pytrace=False,
        )
    if entry is not None:
        expected(entry, detail)
    unbuilt(listed, detail)


def differences(theirs: Outcome, ours: Outcome) -> list[str]:
    """Return how two runs of one report differ: exit code, then output.

    Args:
        theirs: What the reference printed.
        ours: What this engine printed.

    """
    found = []
    if theirs.code != ours.code:
        found.append(f"  exit {theirs.code} in the reference, {ours.code} here")
        found.extend(f"  reference: {line}" for line in theirs.stderr.splitlines())
        found.extend(f"  here: {line}" for line in ours.stderr.splitlines())
    found.extend(output_differences(theirs.stdout, ours.stdout))
    return found


def output_differences(theirs: bytes, ours: bytes) -> list[str]:
    """Return how two engines' standard output differs, as a diff.

    A line holding a carriage return is shown as its ``repr``,
    since a diff of two lines that differ only in one shows nothing.

    Args:
        theirs: What the reference wrote.
        ours: What this engine wrote.

    """
    left = without_image_types(theirs.decode("utf-8", "replace"))
    right = ours.decode("utf-8", "replace")
    if left == right:
        return []
    diff = difflib.unified_diff(
        left.split("\n"),
        right.split("\n"),
        "reference",
        "local",
        lineterm="",
        n=1,
    )
    return [f"  {line!r}" if "\r" in line else f"  {line}" for line in diff]
