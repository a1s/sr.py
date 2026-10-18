"""What the package says about itself."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import pytest

from sr import cli, meta

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = "s-reports"


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


def test_the_installed_command_is_the_one_doc_cli_md_names() -> None:
    """An installed package puts ``s-reports`` on the path, running main.

    Read from the installed metadata rather than from pyproject.toml,
    so that it is the command pip actually makes that is checked.

    """
    try:
        installed = distribution(DISTRIBUTION)
    except PackageNotFoundError:
        pytest.skip(f"{DISTRIBUTION} is not installed; run `make install`")
    scripts = list(installed.entry_points.select(group="console_scripts"))
    assert [point.name for point in scripts] == ["s-reports"]
    assert scripts[0].load() is cli.main


def test_the_engine_identifier_is_what_a_printout_carries() -> None:
    assert meta.engine() == f"sr {meta.VERSION}"
