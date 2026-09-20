"""The example at the end of doc/printout.md is a document, not a picture.

doc/printout.md ends with a printout, and this builds the template that
example names and compares the bytes.  Everything the specification
fixes is in those bytes -- the number format, the key order, the path
rule, the line ending -- so one comparison holds all of them at once,
and a change to any of them fails here with the specification's own
text as the expected value.

The example is shown wrapped for readability: a record begins at
the left margin and every continuation of it is indented or closes
the array.  :func:`unwrap` undoes exactly that, which is why the test
can be on the bytes.

The build happens in a copy of the tree rather than in the repository:
the printout's `resolvedFile` is written relative to where it lands,
so the example's ``../fonts/Go-Regular.ttf`` is a statement about
the directory the file is in and is only true if the file is there.

"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sr.api import Options, build
from sr.printout.write import write_jsonl

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "doc" / "printout.md"
EXAMPLE = ROOT / "example" / "minimal"
FONTS = ROOT / "example" / "fonts"

# The example is built with these two flags, and without them it
# is not reproducible: the first fixes `built` and the second
# keeps the font resolution off the machine.
BUILD_TIME = "2026-08-04T09:12:44Z"


def unwrap(block: str) -> str:
    """Return the example's wrapped page line as the one line it is.

    Args:
        block: The fenced JSON block, as the specification shows it.

    """
    out: list[str] = []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("{") or not out:
            out.append(line.strip())
        else:
            out[-1] += line.strip()
    return "".join(one + "\n" for one in out)


def shown() -> str:
    """Return the printout doc/printout.md's example section shows.

    Raises:
        AssertionError: The section is not where it is expected.

    """
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## Example") :]
    opened = section.index("```json") + len("```json\n")
    closed = section.index("```", opened)
    return unwrap(section[opened:closed])


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Return a copy of the example beside a copy of the fonts."""
    shutil.copytree(FONTS, tmp_path / "fonts")
    shutil.copytree(EXAMPLE, tmp_path / "minimal")
    return tmp_path / "minimal"


def test_the_example_is_what_the_template_produces(tree: Path) -> None:
    result = build(
        tree / "minimal.kdl",
        tree / "films.jsonl",
        Options(build_time=BUILD_TIME, strict_fonts=True),
    )
    out = tree / "minimal.srp.jsonl"
    with out.open("w", encoding="utf-8", newline="") as handle:
        write_jsonl(result.printout, handle, out.parent)
    assert out.read_text(encoding="utf-8") == shown()


def test_the_example_is_written_with_one_newline_per_record(tree: Path) -> None:
    result = build(
        tree / "minimal.kdl",
        tree / "films.jsonl",
        Options(build_time=BUILD_TIME, strict_fonts=True),
    )
    out = tree / "minimal.srp.jsonl"
    with out.open("w", encoding="utf-8", newline="") as handle:
        write_jsonl(result.printout, handle, out.parent)
    raw = out.read_bytes()
    assert raw.count(b"\n") == 2
    assert b"\r" not in raw


def test_the_build_is_reproducible(tree: Path) -> None:
    """Two runs of one template give one document.

    doc/cli.md#reproducibility promises this for a fixed
    ``--build-time`` and ``--strict-fonts``, and it is
    the promise the printout format exists to keep.

    """
    asked = Options(build_time=BUILD_TIME, strict_fonts=True)
    written = []
    for name in ("first.srp.jsonl", "second.srp.jsonl"):
        result = build(tree / "minimal.kdl", tree / "films.jsonl", asked)
        out = tree / name
        with out.open("w", encoding="utf-8", newline="") as handle:
            write_jsonl(result.printout, handle, out.parent)
        written.append(out.read_bytes())
    assert written[0] == written[1]
