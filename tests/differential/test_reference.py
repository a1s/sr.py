"""Tests for the oracle and the plumbing around it.

Until M6 there is no second engine to compare against, so these run
the reference against itself.  That is not a tautology: it exercises
the build, the argument construction, the output paths and the byte
comparison -- every part of the harness except the engine that does not
exist yet -- and it checks the reproducibility doc/cli.md promises,
which is what byte-identity is measured against in the first place.

"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from tests.differential.cases import Case, example_cases
from tests.differential.diff import compare_printouts
from tests.differential.engines import BuildFailed, Engine, run
from tests.differential.reference import ReferenceBinary


@pytest.mark.differential
def test_the_reference_reports_a_version(reference: ReferenceBinary) -> None:
    assert reference.version.startswith("sr "), reference.version
    assert reference.path.is_file()


@pytest.mark.differential
def test_the_reference_repeats_itself(oracle: Engine, build_directory: Path) -> None:
    """One template, one dataset, two runs, the same bytes.

    doc/cli.md makes this promise for a fixed ``--build-time`` under
    ``--strict-fonts``, which is what every case in the corpus sets.
    """
    case = example_cases()[0]
    again = replace(oracle, name="reference-again")
    first = run(oracle, case, build_directory)
    second = run(again, case, build_directory)
    comparison = compare_printouts(
        first.printout,
        second.printout,
        left_name=oracle.name,
        right_name=again.name,
        title=case.ident,
    )
    assert comparison, comparison.report


@pytest.mark.differential
def test_the_summary_line_goes_to_stderr(oracle: Engine, build_directory: Path) -> None:
    """What a command says about the run does not land in the document."""
    build = run(oracle, example_cases()[0], build_directory)
    assert re.search(r": \d+ pages?, \d+ fonts?", build.stderr), build.stderr
    assert build.stdout == ""


@pytest.mark.differential
def test_a_refusal_carries_the_engine_diagnostic(
    oracle: Engine, build_directory: Path, tmp_path: Path
) -> None:
    """A build that fails is a failure, not a silent empty printout."""
    absent = Case("probe/absent", tmp_path / "no-such-template.kdl")
    with pytest.raises(BuildFailed) as refusal:
        run(oracle, absent, build_directory)
    assert "probe/absent" in str(refusal.value)
