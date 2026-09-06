"""Tests for the divergence register.

The register is only as good as the rules that keep it honest,
so those are what is tested: the shipped file loads, an example
is never excused by it, and a malformed entry is refused rather
than dropped.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.cases import example_cases
from tests.differential.register import load_register

WELL_FORMED = """
[[divergence]]
id = "example"
summary = "a summary"
reason = "a reason"
ticket = "a ticket"
since = "2026-09-06"
affects = ["probe/one", "probe/two-*"]
"""


def write(directory: Path, text: str) -> Path:
    """Write a register file and return its path."""
    path = directory / "divergences.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_register_loads() -> None:
    register = load_register()
    assert register.entries, "the register is empty"


def test_every_entry_names_what_retires_it() -> None:
    for entry in load_register().entries:
        assert entry.ticket.strip(), f"{entry.ident} names no ticket"
        assert entry.affects, f"{entry.ident} covers no case"


def test_the_examples_are_never_excused() -> None:
    """No entry may cover an example.

    The examples are the broadest comparison the corpus has.
    A register entry over one of them would excuse the whole report,
    which is never what an entry is for.

    """
    register = load_register()
    for case in example_cases():
        entry = register.entry_for(case.ident)
        assert entry is None, f"{case.ident} is covered by divergence {entry.ident}"


def test_a_pattern_matches(tmp_path: Path) -> None:
    register = load_register(write(tmp_path, WELL_FORMED))
    matched = register.entry_for("probe/two-and-a-half")
    assert matched is not None
    assert matched.ident == "example"
    assert register.entry_for("probe/three") is None


def test_the_description_carries_the_reason(tmp_path: Path) -> None:
    entry = load_register(write(tmp_path, WELL_FORMED)).entries[0]
    described = entry.describe()
    assert "a reason" in described
    assert "a ticket" in described


@pytest.mark.parametrize(
    ("flaw", "text"),
    [
        ("a missing ticket", WELL_FORMED.replace('ticket = "a ticket"\n', "")),
        ("an empty reason", WELL_FORMED.replace('"a reason"', '"   "')),
        ("no affected case", WELL_FORMED.replace('["probe/one", "probe/two-*"]', "[]")),
        ("an unknown key", WELL_FORMED + 'note = "surplus"\n'),
        ("a duplicate id", WELL_FORMED + WELL_FORMED),
    ],
)
def test_a_malformed_entry_is_refused(flaw: str, text: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_register(write(tmp_path, text))
    assert flaw  # named in the parameter id, for the failure report
