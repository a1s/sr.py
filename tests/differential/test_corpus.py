"""The comparison itself: one test per case, both engines, byte for byte.

Every case in the corpus is built by the reference and by this engine
into one directory, and the two printouts are compared.  A case the
divergence register covers is allowed to differ, and is required to.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.answers import answer_path, distil
from tests.differential.cases import ROOT, Case, corpus, probe_cases
from tests.differential.diff import compare_printouts
from tests.differential.engines import Build, BuildFailed, Engine, run
from tests.differential.register import Divergence, Register


def attempt(engine: Engine, case: Case, directory: Path) -> Build | BuildFailed:
    """Build the case, returning the failure instead of raising it.

    A registered divergence can show itself as a refusal rather than
    as a difference in bytes -- an expression using a builtin one side
    does not have yet is exactly that -- so the failure has to reach
    the register before it reaches the report.

    """
    try:
        return run(engine, case, directory)
    except BuildFailed as failure:
        return failure


def expected(entry: Divergence, detail: str) -> None:
    """Mark the running test as an expected difference."""
    pytest.xfail(f"{entry.describe()}\n{detail}")


@pytest.mark.differential
@pytest.mark.parametrize("case", corpus(), ids=lambda case: case.ident)
def test_printouts_agree(
    case: Case,
    oracle: Engine,
    local: Engine,
    build_directory: Path,
    register: Register,
) -> None:
    missing = case.missing_inputs()
    assert not missing, f"{case.ident}: missing input files: " + ", ".join(
        str(path) for path in missing
    )

    entry = register.entry_for(case.ident)
    outcomes = {
        engine.name: attempt(engine, case, build_directory)
        for engine in (oracle, local)
    }
    refused = {
        name: outcome
        for name, outcome in outcomes.items()
        if isinstance(outcome, BuildFailed)
    }
    if refused:
        detail = "\n".join(str(failure) for failure in refused.values())
        if entry is not None:
            expected(entry, detail)
        pytest.fail(detail, pytrace=False)

    reference_build = outcomes[oracle.name]
    local_build = outcomes[local.name]
    assert isinstance(reference_build, Build)
    assert isinstance(local_build, Build)

    comparison = compare_printouts(
        reference_build.printout,
        local_build.printout,
        left_name=oracle.name,
        right_name=local.name,
        title=case.ident,
    )
    if entry is None:
        assert comparison.identical, comparison.report
        return
    if comparison.identical:
        pytest.fail(
            f"{case.ident} no longer differs, so divergence {entry.ident!r} "
            "has been retired: remove it from divergences.toml",
            pytrace=False,
        )
    expected(entry, comparison.report)


@pytest.mark.differential
@pytest.mark.parametrize("case", probe_cases(), ids=lambda case: case.ident)
def test_the_reference_builds_every_probe(
    case: Case,
    oracle: Engine,
    build_directory: Path,
    register: Register,
) -> None:
    """Every probe still asks the oracle its question.

    A probe is a template whose answer is read out of the reference's
    printout, so one the reference will not build has stopped measuring
    anything -- and while this engine produces no printout of its own,
    the comparison above skips and would not notice.  This holds the
    corpus up on the oracle alone until M6, and keeps holding it after.

    A refusal the register covers is the answer rather than a fault:
    two of the dialect amendments are expressions the reference has
    no name for, and refusing them is exactly the divergence recorded.

    """
    outcome = attempt(oracle, case, build_directory)
    if isinstance(outcome, BuildFailed):
        entry = register.entry_for(case.ident)
        if entry is not None:
            expected(entry, str(outcome))
        pytest.fail(str(outcome), pytrace=False)


@pytest.mark.differential
@pytest.mark.parametrize("case", probe_cases(), ids=lambda case: case.ident)
def test_the_reference_still_answers_the_same_way(
    case: Case,
    oracle: Engine,
    build_directory: Path,
    register: Register,
) -> None:
    """The oracle says today what it said when the probe was written.

    Building is not answering.  Until this engine produces printouts
    the comparison above skips, so a reference that changed its mind
    about line breaking would pass a suite that only checked it still ran.
    The answer committed beside each probe closes that window, and it is
    the answer itself -- the `lines`, the boxes, the `data` keys --
    so a diff in one of those files is a diff in what doc/ was written from.

    Record them again with ``python -m tests.differential.record_answers``,
    and read what changes before committing it.

    """
    recorded = answer_path(ROOT / case.template)
    outcome = attempt(oracle, case, build_directory)
    if isinstance(outcome, BuildFailed):
        entry = register.entry_for(case.ident)
        assert entry is not None, str(outcome)
        assert not recorded.exists(), (
            f"{case.ident}: the reference refuses this probe, so "
            f"{recorded.name} should not exist"
        )
        expected(entry, str(outcome))
    assert recorded.is_file(), (
        f"{case.ident}: no recorded answer; run "
        "`python -m tests.differential.record_answers`"
    )
    assert isinstance(outcome, Build)
    got = distil(outcome.printout.decode("utf-8"))
    want = recorded.read_text(encoding="utf-8")
    assert got == want, (
        f"{case.ident}: the reference no longer answers as recorded in "
        f"{recorded.name}. That is either a change in the oracle, which doc/ "
        "was written from, or a change to the probe. Re-record with "
        "`python -m tests.differential.record_answers` once you know which."
    )


@pytest.mark.differential
def test_corpus_is_not_empty() -> None:
    """The suite measures something.

    An empty corpus passes every comparison it has, which is none.

    """
    assert corpus(), "the differential corpus is empty"
