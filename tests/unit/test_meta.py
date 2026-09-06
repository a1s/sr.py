"""What the package says about itself."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

from sr import meta

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = "sr-py"


def test_the_version_is_a_release_number() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", meta.VERSION)


def test_the_version_matches_the_changelog() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## \[([^\]]+)\]", changelog, flags=re.MULTILINE)
    assert released, "CHANGELOG.md records no release"
    assert meta.VERSION in released


def test_the_distribution_takes_its_version_from_here() -> None:
    """pyproject.toml reads sr.meta.VERSION rather than stating its own.

    Checking it is checking the wiring: a `dynamic` version that
    silently stopped resolving would leave the two free to drift,
    which is the thing the arrangement exists to prevent.

    """
    try:
        installed = version(DISTRIBUTION)
    except PackageNotFoundError:
        pytest.skip(f"{DISTRIBUTION} is not installed; run `make install`")
    assert installed == meta.VERSION


def test_the_engine_identifier_is_what_a_printout_carries() -> None:
    assert meta.engine() == f"sr {meta.VERSION}"
