"""What CHANGELOG.md says has been released.

sr.meta's version and date are held to it, and so is the example
doc/printout.md ends with, which names the latest release.

"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["NUMBER", "headings", "latest", "numbered"]

ROOT = Path(__file__).resolve().parents[1]

# `## [0.1.0] - 2026-10-18`, or `## [Unreleased]` with no date,
# as .github/scripts/changelog.py reads a release heading.
RELEASE = re.compile(
    r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", flags=re.MULTILINE
)

# A release number, which `Unreleased` is not.
NUMBER = re.compile(r"\d+\.\d+\.\d+")


def headings() -> dict[str, str]:
    """Return the date of each heading CHANGELOG.md has, by version.

    A heading with no date maps to an empty string:
    it names a version that is not released yet.

    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return dict(RELEASE.findall(changelog))


def numbered(version: str) -> tuple[int, ...]:
    """Return a release number as its parts, which order as releases do."""
    return tuple(int(part) for part in version.split("."))


def latest() -> str:
    """Return the latest release, the highest number CHANGELOG.md dates.

    Raises:
        AssertionError: The changelog dates no release.

    """
    dated = [
        version
        for version, day in headings().items()
        if day and NUMBER.fullmatch(version)
    ]
    assert dated, "CHANGELOG.md dates no release"
    return max(dated, key=numbered)
