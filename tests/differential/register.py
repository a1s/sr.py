"""The register of known divergences.

Some differences between the two engines are there on purpose, because
a decision has been taken and only one side has been changed yet.
Those would otherwise sit in the suite as failures, and a real
regression would hide among them.

So each is written down, with the reason and the work that retires it.
Two rules keep the list honest, and both are enforced by the tests:

- a registered case that still differs is reported and does not fail;
- a registered case that *stops* differing fails, asking for the entry
  to be removed.

Every entry is a defect somewhere.  This is a list to shorten.

"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

__all__ = ["Divergence", "Register", "load_register"]

REGISTER_PATH = Path(__file__).resolve().parent / "divergences.toml"

# Every field is required, and every one of them non-empty:
# an entry that does not say why it exists, or what closes it,
# is the thing this file is meant to prevent.
REQUIRED_TEXT = ("id", "summary", "reason", "ticket", "since")


@dataclass(frozen=True)
class Divergence:
    """One difference that is expected until something is fixed.

    Attributes:
        ident: A stable name for the divergence.
        summary: One line, what differs.
        reason: Why, and which of the two engines is the wrong one.
        ticket: The work that retires this entry.
        since: When it was registered, as a date.
        affects: Glob patterns over case identifiers.

    """

    ident: str
    summary: str
    reason: str
    ticket: str
    since: str
    affects: tuple[str, ...]

    def matches(self, case_ident: str) -> bool:
        """Report whether this entry covers the named case."""
        return any(fnmatch(case_ident, pattern) for pattern in self.affects)

    def describe(self) -> str:
        """Render the entry for a test report."""
        return (
            f"known divergence {self.ident}: {self.summary}\n"
            f"  reason: {self.reason}\n"
            f"  retired by: {self.ticket} (registered {self.since})"
        )


@dataclass(frozen=True)
class Register:
    """Every registered divergence, in the order the file lists them."""

    entries: tuple[Divergence, ...]
    path: Path

    def entry_for(self, case_ident: str) -> Divergence | None:
        """Return the entry covering ``case_ident``, or None.

        The first match wins, so a narrow pattern belongs above a broad one.
        """
        for entry in self.entries:
            if entry.matches(case_ident):
                return entry
        return None


def load_register(path: Path = REGISTER_PATH, table: str = "divergence") -> Register:
    """Read and validate a register.

    Two files have this shape: the divergence register,
    and the [pending](pending.py) list of cases this engine
    does not build yet.  They hold different things and are read
    the same way, because the two rules that keep either honest
    are the same two.

    Args:
        path: The file to read.
        table: The name of the array of tables in it.

    Raises:
        ValueError: on a malformed entry.

            A register that cannot be read is a failure rather than
            an empty list, because an empty list silently turns
            every expected difference into a regression.

    """
    with path.open("rb") as handle:
        document = tomllib.load(handle)

    raw = document.get(table, [])
    if not isinstance(raw, list):
        raise ValueError(f"{path}: `{table}` must be an array of tables")

    entries: list[Divergence] = []
    seen: set[str] = set()
    for position, item in enumerate(raw):
        where = f"{path}: {table} {position + 1}"
        for key in REQUIRED_TEXT:
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{where}: `{key}` is required and must be text")
        affects = item.get("affects")
        if not isinstance(affects, list) or not affects:
            raise ValueError(f"{where}: `affects` must name at least one case pattern")
        if not all(isinstance(pattern, str) and pattern for pattern in affects):
            raise ValueError(f"{where}: every `affects` pattern must be text")
        unknown = set(item) - set(REQUIRED_TEXT) - {"affects"}
        if unknown:
            raise ValueError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
        if item["id"] in seen:
            raise ValueError(f"{where}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        entries.append(
            Divergence(
                ident=item["id"],
                summary=item["summary"],
                reason=item["reason"],
                ticket=item["ticket"],
                since=item["since"],
                affects=tuple(affects),
            )
        )
    return Register(tuple(entries), path)
