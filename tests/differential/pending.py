"""The register of cases this engine cannot build yet.

A divergence is a difference that is *meant* to be there until one of
the two engines catches up.  This is the other list: cases the Python
engine does not build yet because the milestone that builds them has
not arrived.  They would otherwise sit in the suite as failures, and
a real regression would hide among them -- which is the same argument
the divergence register makes, so the two files have the same shape
and the same two rules:

- a pending case that still fails or differs is reported
  and does not fail the suite;
- a pending case that **agrees** fails, asking for the entry
  to be removed.

The second rule is what makes the list shrink by itself.
A milestone that lands and quietly leaves its entries behind
would leave the suite measuring nothing for those cases, forever.

Unlike a divergence, a pending entry **may** cover a case in
``example/``.  The examples use every node in the format, so until
the last of those nodes is built they cannot pass, and pretending
otherwise would mean carrying a red suite for ten milestones.
What a pending entry must not do is cover a case the divergence
register already covers: two reasons for one expected failure
is one reason too many, and
:func:`tests.differential.test_pending.test_no_case_is_in_both`
holds that.

"""

from __future__ import annotations

from pathlib import Path

from tests.differential.register import Register, load_register

__all__ = ["PENDING_PATH", "load_pending"]

PENDING_PATH = Path(__file__).resolve().parent / "pending.toml"


def load_pending(path: Path = PENDING_PATH) -> Register:
    """Read and validate the pending register.

    Args:
        path: The file to read.

    Raises:
        ValueError: On a malformed entry.

    """
    return load_register(path, table="pending")
