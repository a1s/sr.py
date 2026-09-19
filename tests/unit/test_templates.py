"""The corpus in tests/templates, and the two examples.

Each template in ``broken/`` isolates one rule of
doc/template.md#validation and states, in its own header comment,
the diagnostics it expects:

    // expect: <text a diagnostic has to contain>
    // warn:   <text a warning has to contain>

The expectation lives in the template rather than here so that
the question and the answer are one file, and so that adding
a case is writing a template rather than editing a table.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from sr.template import load
from sr.template.load import Loaded

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "templates"
EXAMPLES = (
    ROOT / "example" / "sakila" / "sakila.kdl",
    ROOT / "example" / "invoices" / "invoices.kdl",
    ROOT / "example" / "invoices" / "region_sheet.kdl",
)


def cases(directory: str) -> list[Path]:
    """Return the templates of one corpus directory, `.inc.kdl` aside.

    Args:
        directory: ``broken`` or ``valid``.

    """
    found = sorted(CORPUS.joinpath(directory).glob("*.kdl"))
    return [path for path in found if not path.name.endswith(".inc.kdl")]


def expectations(path: Path, marker: str) -> list[str]:
    """Return what a template's header says it expects.

    Args:
        path: The template.
        marker: ``expect`` or ``warn``.

    """
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("//"):
            break
        head, sep, tail = line[2:].strip().partition(":")
        if sep and head.strip() == marker:
            found.append(tail.strip())
    return found


def reported(loaded: Loaded) -> str:
    """Return everything a load said, as one block of text.

    Args:
        loaded: What the load produced.

    """
    return "\n".join(str(one) for one in (*loaded.errors, *loaded.warnings))


def identify(path: Path) -> str:
    """Return the test id for one corpus template.

    Args:
        path: The template.

    """
    return f"{path.parent.name}/{path.stem}"


@pytest.mark.parametrize("path", cases("broken"), ids=identify)
def test_a_broken_template_is_refused(path: Path) -> None:
    loaded = load(path)
    assert loaded.errors, f"{path.name} was accepted"
    assert not loaded.ok


@pytest.mark.parametrize("path", cases("broken"), ids=identify)
def test_a_broken_template_says_what_is_wrong(path: Path) -> None:
    loaded = load(path)
    said = reported(loaded)
    wanted = expectations(path, "expect")
    assert wanted, f"{path.name} states no expectation"
    for one in wanted:
        assert one in said, f"{path.name} did not report {one!r}:\n{said}"


@pytest.mark.parametrize("path", cases("broken"), ids=identify)
def test_a_broken_template_names_its_file(path: Path) -> None:
    for diagnostic in load(path).errors:
        assert diagnostic.location.file is not None


@pytest.mark.parametrize("path", cases("valid"), ids=identify)
def test_a_valid_template_loads(path: Path) -> None:
    loaded = load(path)
    assert loaded.ok, reported(loaded)


@pytest.mark.parametrize("path", cases("valid"), ids=identify)
def test_a_valid_template_warns_as_it_says(path: Path) -> None:
    loaded = load(path)
    said = reported(loaded)
    wanted = expectations(path, "warn")
    for one in wanted:
        assert one in said, f"{path.name} did not warn {one!r}:\n{said}"
    # One `warn:` line per warning, so a template that grows one nobody
    # asked for is caught as well as one that loses a warning it states.
    assert len(loaded.warnings) == len(wanted), (
        f"{path.name} states {len(wanted)} warnings and made "
        f"{len(loaded.warnings)}:\n{said}"
    )


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_an_example_loads_clean(path: Path) -> None:
    loaded = load(path)
    assert loaded.ok, reported(loaded)
    assert not loaded.warnings, reported(loaded)
