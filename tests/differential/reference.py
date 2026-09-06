"""Locating and building the Go reference binary.

The Go implementation is the oracle: its *outputs* settle questions
the specification leaves open.  Its source is never read, so the only
thing wanted from the tree next door is a binary.

Where the toolchain or the tree is missing, every function here raises
:class:`ReferenceUnavailable` rather than failing, and the fixtures in
``conftest.py`` turn that into a skip.

"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "REFERENCE_BUILD_TIMEOUT",
    "ReferenceBinary",
    "ReferenceUnavailable",
    "build_reference",
    "find_go_tree",
]

# The repository root: tests/differential/reference.py -> up three.
ROOT = Path(__file__).resolve().parents[2]

# Where the built oracle lands.  ``tmp/`` is already ignored by git.
BUILD_DIR = ROOT / "tmp" / "reference"

# A cold build of the Go tree pulls its module cache; a warm one is instant.
REFERENCE_BUILD_TIMEOUT = 600.0

# Fallbacks tried when the environment does not name a tree.
# Anything is only accepted after :func:`looks_like_go_tree` agrees.
FALLBACK_TREES = (Path.home() / "src" / "sr",)


class ReferenceUnavailable(Exception):
    """The oracle cannot be produced on this machine.

    Carries the reason as its message, which is what the skip says.

    """


@dataclass(frozen=True)
class ReferenceBinary:
    """A built reference engine, and what it says about itself."""

    path: Path
    version: str
    tree: Path

    def __str__(self) -> str:
        """Name the binary and the version it reports."""
        return f"{self.version} ({self.path})"


def looks_like_go_tree(candidate: Path) -> bool:
    """Report whether ``candidate`` is the Go implementation's source tree.

    Two markers, both from the directory listing alone: the module file
    and the command package the ``GNUmakefile`` builds.

    """
    return (candidate / "go.mod").is_file() and (
        candidate / "cmd" / "sr" / "main.go"
    ).is_file()


def find_go_tree() -> Path:
    """Return the Go implementation's source tree.

    ``SR_GO_REPO`` wins when it is set; otherwise the ancestors of this
    repository are searched for a sibling named ``sr``, which finds the
    tree from a plain checkout and from a git worktree alike.

    Raises:
        ReferenceUnavailable: when no tree is found.

    """
    named = os.environ.get("SR_GO_REPO")
    if named:
        candidate = Path(named).expanduser()
        if not looks_like_go_tree(candidate):
            raise ReferenceUnavailable(
                f"SR_GO_REPO names {candidate}, which is not the Go source tree"
            )
        return candidate

    for ancestor in (ROOT, *ROOT.parents):
        sibling = ancestor / "sr"
        if looks_like_go_tree(sibling):
            return sibling

    raise ReferenceUnavailable(
        "the Go source tree was not found next to this repository; "
        "set SR_GO_REPO to point at it"
    )


def find_go_toolchain() -> str:
    """Return the path to the ``go`` command.

    Raises:
        ReferenceUnavailable: when Go is not installed.
    """
    found = shutil.which(os.environ.get("SR_GO", "go"))
    if found is None:
        raise ReferenceUnavailable("the go toolchain is not on PATH")
    return found


def build_reference() -> ReferenceBinary:
    """Build the reference binary once and return it.

    ``SR_REFERENCE_BINARY`` short-circuits the build, for a machine that
    has the binary but not the source.  Otherwise the Go tree is located
    and ``go build`` runs into ``tmp/reference/``; Go's own caching makes
    a repeat build a no-op, so this is cheap to call once per test session.

    Raises:
        ReferenceUnavailable: the toolchain, the tree, or the build is absent.

    """
    prebuilt = os.environ.get("SR_REFERENCE_BINARY")
    if prebuilt:
        binary = Path(prebuilt).expanduser()
        if not binary.is_file():
            raise ReferenceUnavailable(
                f"SR_REFERENCE_BINARY names {binary}, which does not exist"
            )
        return ReferenceBinary(binary, reference_version(binary), binary.parent)

    tree = find_go_tree()
    toolchain = find_go_toolchain()
    binary = BUILD_DIR / ("sr.exe" if sys.platform == "win32" else "sr")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # -trimpath for the same reason the GNUmakefile uses it: build paths
    # do not belong in the binary, and two machines should produce one file.
    command = [
        toolchain,
        "build",
        "-trimpath",
        "-o",
        str(binary),
        "./cmd/sr",
    ]
    try:
        completed = subprocess.run(  # the argv is ours, not a shell string
            command,
            cwd=tree,
            capture_output=True,
            text=True,
            timeout=REFERENCE_BUILD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ReferenceUnavailable(
            f"building the reference timed out after {REFERENCE_BUILD_TIMEOUT:.0f}s"
        ) from None
    if completed.returncode != 0:
        raise ReferenceUnavailable(
            "the reference did not build:\n"
            + indent(completed.stderr or completed.stdout)
        )
    return ReferenceBinary(binary, reference_version(binary), tree)


def reference_version(binary: Path) -> str:
    """Return what the binary says its version is.

    Raises:
        ReferenceUnavailable: when the binary will not run.

    """
    try:
        completed = subprocess.run(  # the argv is ours, not a shell string
            [str(binary), "version"],
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
    except OSError as failure:
        raise ReferenceUnavailable(f"{binary} would not run: {failure}") from None
    if completed.returncode != 0:
        raise ReferenceUnavailable(
            f"{binary} version exited {completed.returncode}:\n"
            + indent(completed.stderr)
        )
    return completed.stdout.strip()


def indent(text: str, prefix: str = "    ") -> str:
    """Indent every line of ``text``, for quoting a tool's output."""
    return "\n".join(prefix + line for line in text.splitlines())
