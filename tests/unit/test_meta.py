"""What the package says about itself."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution, version

import pytest

from sr import cli, meta
from tests.changelog import NUMBER, headings, latest, numbered

DISTRIBUTION = "s-reports"


def following(release: str) -> list[str]:
    """Return the versions that can come next: a patch, a minor, a major."""
    major, minor, patch = numbered(release)
    return [
        f"{major}.{minor}.{patch + 1}",
        f"{major}.{minor + 1}.0",
        f"{major + 1}.0.0",
    ]


def test_the_version_is_a_release_number() -> None:
    assert NUMBER.fullmatch(meta.VERSION)


def test_the_version_matches_the_changelog() -> None:
    """The version is the latest release, or the one that follows it.

    Work on a version starts by raising sr.meta.VERSION
    one step past the latest release, and the changelog dates it
    only once it is released.  A larger step skips a version,
    which is more likely a typing slip than a decision.

    """
    release = latest()
    allowed = [release, *following(release)]
    choices = ", ".join(allowed)
    assert meta.VERSION in allowed, (
        f"sr.meta.VERSION is {meta.VERSION}, and the latest release "
        f"CHANGELOG.md dates is {release}, so it should be one of {choices}"
    )


def test_the_date_matches_the_changelog() -> None:
    """The changelog dates this version as sr.meta.DATE does.

    The release workflow refuses a tag whose two dates differ.
    Checked here too, so that a mismatch fails the pull request
    rather than the tag, which would then have to be cut again.

    A version in development has no date in the changelog yet,
    and sr.meta.DATE still names the latest release.  A release
    sets the two dates together, so a new date with no date
    in the changelog is a release that left its section out.

    """
    dated = headings().get(meta.VERSION)
    if dated:
        assert dated == meta.DATE.isoformat(), (
            f"CHANGELOG.md dates {meta.VERSION} {dated}, "
            f"and sr.meta.DATE says {meta.DATE}"
        )
        return
    release = latest()
    assert headings()[release] == meta.DATE.isoformat(), (
        f"sr.meta.DATE is {meta.DATE}, and CHANGELOG.md dates no "
        f"{meta.VERSION}: until it does, the date stays that of "
        f"{release}, {headings()[release]}"
    )


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
