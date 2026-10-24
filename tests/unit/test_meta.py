"""What the package says about itself."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import pytest

from sr import cli, meta

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = "s-reports"

# `## [0.1.0] - 2026-10-18`, or `## [Unreleased]` with no date,
# as .github/scripts/changelog.py reads a release heading.
RELEASE = re.compile(
    r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", flags=re.MULTILINE
)

# A release number, which `## [Unreleased]` is not.
NUMBER = re.compile(r"\d+\.\d+\.\d+")


def released() -> dict[str, str]:
    """Return the date of each release CHANGELOG.md records, by version."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return dict(RELEASE.findall(changelog))


def numbered(version: str) -> tuple[int, ...]:
    """Return a release number as its parts, which order as releases do."""
    return tuple(int(part) for part in version.split("."))


def test_the_version_is_a_release_number() -> None:
    assert NUMBER.fullmatch(meta.VERSION)


def test_the_version_matches_the_changelog() -> None:
    """The version is one the changelog records, or one after all of them.

    Work on a version starts by raising sr.meta.VERSION
    past the last release, and the changelog records it
    only once it is released.  A version that is neither
    is one the changelog has never heard of.

    """
    releases = [one for one in released() if NUMBER.fullmatch(one)]
    assert releases, "CHANGELOG.md records no release"
    if meta.VERSION in releases:
        return
    latest = max(releases, key=numbered)
    assert numbered(meta.VERSION) > numbered(latest), (
        f"sr.meta.VERSION is {meta.VERSION}, which CHANGELOG.md does not "
        f"record, and which does not follow its latest release, {latest}"
    )


def test_the_date_matches_the_changelog() -> None:
    """The changelog dates this version as sr.meta.DATE does.

    The release workflow refuses a tag whose two dates differ.
    Checked here too, so that a mismatch fails the pull request
    rather than the tag, which would then have to be cut again.
    A version still in development has no date in the changelog,
    so there is nothing to hold it to until it is released.

    """
    releases = released()
    if meta.VERSION not in releases:
        pytest.skip(f"{meta.VERSION} is not released yet")
    assert releases[meta.VERSION] == meta.DATE.isoformat()


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
