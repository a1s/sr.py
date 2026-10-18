"""Read a release out of CHANGELOG.md, for the release workflow.

usage: changelog.py latest [FILE]
       changelog.py notes VERSION [FILE]

``latest`` prints the version and the date of the first release heading.
``notes`` prints the section under one version's heading, without the
heading, the blank lines around it, or link definitions.

Both read headings with the one pattern below, so the version a tag
is checked against and the notes its release carries cannot come
from two readings of the same line.  A level-two heading the pattern
does not match is an error rather than something to read past.

"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

USAGE = "usage: changelog.py latest [FILE] | notes VERSION [FILE]"

# `## [0.1.0] - 2026-09-05`, or `## [Unreleased]` with no date.
VERSION = r"\[(?P<version>[^\]]+)\]"
DATE = r"(?P<date>\d{4}-\d{2}-\d{2})"
HEADING = re.compile(rf"## {VERSION}(?: - {DATE})?")

# `[0.1.0]: https://...`: Keep a Changelog ends the file with these,
# and they would otherwise end the last section's notes.
LINK_DEFINITION = re.compile(r"\[[^\]]+\]:\s")


class ChangelogError(Exception):
    """The changelog does not say what the release needs it to."""


@dataclass
class Release:
    """One release heading and the lines under it."""

    version: str
    date: str
    lines: list[str] = field(default_factory=list)


def read_releases(text: str) -> list[Release]:
    """Split a changelog into its releases, in the order they appear.

    Raises:
        ChangelogError: a level-two heading is not a release heading.

    """
    releases: list[Release] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            heading = HEADING.fullmatch(line.rstrip())
            if heading is None:
                shape = "`## [VERSION] - YYYY-MM-DD`"
                message = f"line {number} should read {shape}: {line!r}"
                raise ChangelogError(message)
            releases.append(Release(heading["version"], heading["date"] or ""))
        elif releases and not LINK_DEFINITION.match(line):
            releases[-1].lines.append(line)
    return releases


def latest(releases: list[Release]) -> str:
    """Return the first release's version and date, separated by a space.

    Raises:
        ChangelogError: the changelog has no release heading.

    """
    if not releases:
        raise ChangelogError("there is no release heading")
    return f"{releases[0].version} {releases[0].date}".rstrip()


def notes(releases: list[Release], version: str) -> str:
    """Return the section under one version's heading, trimmed.

    Raises:
        ChangelogError: the version has no section, or an empty one,
            so that a release cannot go out with no notes.

    """
    for release in releases:
        if release.version == version:
            body = "\n".join(release.lines).strip("\n")
            if not body.strip():
                raise ChangelogError(f"the section for {version} is empty")
            return body
    raise ChangelogError(f"there is no section for {version}")


def main(arguments: list[str]) -> int:
    """Run one command and return its exit code."""
    if arguments[:1] == ["latest"] and len(arguments) <= 2:
        path = Path(arguments[1] if len(arguments) == 2 else "CHANGELOG.md")
        command = "latest"
    elif arguments[:1] == ["notes"] and 2 <= len(arguments) <= 3:
        path = Path(arguments[2] if len(arguments) == 3 else "CHANGELOG.md")
        command = "notes"
    else:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        releases = read_releases(path.read_text(encoding="utf-8"))
        if command == "latest":
            print(latest(releases))
        else:
            print(notes(releases, arguments[1]))
    except (ChangelogError, OSError) as failure:
        print(f"changelog: {path}: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
