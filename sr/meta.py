"""What the package says about itself.

This is the single source.  ``pyproject.toml`` reads ``VERSION``
from here rather than stating one of its own, so the distribution
metadata and the string a printout carries cannot drift apart.

"""

from __future__ import annotations

from datetime import date

__all__ = ["DATE", "NAME", "VERSION", "engine"]

# The engine's name, as doc/printout.md's `engine` field spells it.
# It is the specification's name for the engine, not this implementation's,
# so the Go engine writes the same one.
NAME = "sr"

# Kept in step with CHANGELOG.md.
VERSION = "0.1.0"
DATE = date(2026, 9, 5)


def engine() -> str:
    """Return the identifier a printout header carries.

    doc/cli.md prints the same string for ``sr.py version``, so an artifact
    and the code that made it can be matched up.

    """
    return f"{NAME} {VERSION}"
