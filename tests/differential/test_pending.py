"""Tests for the list of cases this engine does not build yet.

The same three rules the divergence register is held to,
and one more that only this list needs: an entry here must not
cover a case the divergence register already covers, because
one expected failure with two reasons is a reason nobody reads.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.cases import corpus, probe_cases
from tests.differential.pending import load_pending
from tests.differential.register import load_register

WELL_FORMED = """
[[pending]]
id = "example"
summary = "a summary"
reason = "a reason"
ticket = "a ticket"
since = "2026-09-20"
affects = ["probe/one", "probe/two-*"]
"""


def write(directory: Path, text: str) -> Path:
    """Write a pending file and return its path."""
    path = directory / "pending.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_list_loads() -> None:
    assert load_pending().entries, "the pending list is empty"


def test_every_entry_names_the_milestone_that_retires_it() -> None:
    for entry in load_pending().entries:
        assert entry.ticket.strip(), f"{entry.ident} names no milestone"
        assert entry.affects, f"{entry.ident} covers no case"


def test_every_entry_covers_a_case_that_exists() -> None:
    if not probe_cases():
        pytest.skip("no probes yet")
    identifiers = [case.ident for case in corpus()]
    for entry in load_pending().entries:
        assert any(entry.matches(ident) for ident in identifiers), (
            f"pending entry {entry.ident!r} matches no case; its patterns are "
            f"{list(entry.affects)}. Either the case was renamed, or the entry "
            "outlived it and should be removed."
        )


def test_no_case_is_in_both() -> None:
    """A case is either a decision or unbuilt work, not both.

    The corpus test looks at the divergence register first,
    so an overlap would hide the pending entry rather than
    report it -- and the entry would then never be cleaned up,
    because nothing would ever say it had done its work.

    """
    register = load_register()
    pending = load_pending()
    for case in corpus():
        divergence = register.entry_for(case.ident)
        waiting = pending.entry_for(case.ident)
        assert divergence is None or waiting is None, (
            f"{case.ident} is covered by divergence {divergence and divergence.ident} "
            f"and by pending entry {waiting and waiting.ident}; it may have one"
        )


def test_a_malformed_entry_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_pending(write(tmp_path, WELL_FORMED.replace('ticket = "a ticket"\n', "")))


def test_a_pattern_matches(tmp_path: Path) -> None:
    listed = load_pending(write(tmp_path, WELL_FORMED))
    matched = listed.entry_for("probe/two-and-a-half")
    assert matched is not None
    assert matched.ident == "example"
