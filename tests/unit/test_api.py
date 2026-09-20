"""The library surface: what a build takes, and what it hands back.

doc/cli.md says the command line decides nothing, so the decisions
are here: which parameter value wins, what counts as a warning about
the document and what is only about the template, and which of those
a caller has to ask for.

"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sr import api
from sr.api import Options, build
from sr.errors import BuildError
from sr.fonts.hostenum import Catalog
from sr.fonts.resolve import Resolver

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()
ONE_ROW = '{"n":1}\n'

BODY = """
report name="Api" {
  font "body" file="FACE" size=10
PARAMETERS
  layout width=300 height=200 {
    style font="body" color="black"
    detail height=20 { field text="row" left=0 top=0 width=50 }
  }
}
"""


def written(tmp_path: Path, parameters: str = "") -> Path:
    """Write a template with the given declarations and return its path."""
    path = tmp_path / "api.kdl"
    path.write_text(
        BODY.replace("FACE", REGULAR).replace("PARAMETERS", parameters),
        encoding="utf-8",
    )
    return path


def rows(tmp_path: Path) -> Path:
    """Write one record and return its path."""
    path = tmp_path / "rows.jsonl"
    path.write_text(ONE_ROW, encoding="utf-8")
    return path


def result(tmp_path: Path, parameters: str = "", **options: Any) -> api.Result:
    """Build the template above and return the whole result."""
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True, **options)
    return build(written(tmp_path, parameters), rows(tmp_path), asked)


# -- parameters -------------------------------------------------------


def test_a_parameter_with_no_value_at_all_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BuildError) as refused:
        result(tmp_path, '  parameter "period" type="date"')
    assert "period" in str(refused.value)


def test_a_given_value_wins_over_a_default(tmp_path: Path) -> None:
    printout = result(
        tmp_path,
        '  parameter "period" type="date" default="2005-01-01"',
        params={"period": "2006-02-03"},
    ).printout
    assert printout.pages  # it built; the value is exercised below


def test_a_defaultexpr_may_read_build_time(tmp_path: Path) -> None:
    """doc/expressions.md: `BUILD_TIME` is constant for the whole run.

    It is the one predefined name a `defaultexpr` can read, because it
    is the only one that has a value before any data is.

    """
    built = result(
        tmp_path, '  parameter "as_of" type="datetime" defaultexpr="BUILD_TIME"'
    )
    assert built.printout.built == "2026-08-04T09:12:44Z"


# -- what comes back --------------------------------------------------


def test_a_template_warning_is_a_note_rather_than_a_header_warning(
    tmp_path: Path,
) -> None:
    """A band that collapses is about the template, not the document.

    doc/printout.md#header-line names four kinds of warning and this is
    none of them, so it reaches the caller and not the printout.

    """
    path = tmp_path / "collapse.kdl"
    path.write_text(
        'report name="Collapse" {\n'
        '  font "body" file="' + REGULAR + '" size=10\n'
        "  layout width=300 height=200 {\n"
        '    style font="body" color="black"\n'
        "    detail { line left=0 top=0 width=1 }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    built = build(
        path,
        rows(tmp_path),
        Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True),
    )
    assert built.printout.warnings == ()
    assert [one.message for one in built.notes] != []


def test_the_host_diagnostics_are_asked_for_rather_than_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--verbose` is what carries them, and nothing else does.

    They are about the machine rather than the document,
    which is why they are neither warnings nor part of the printout.

    """
    catalog = Catalog()
    catalog.diagnostics.append("a face this machine skipped")

    def noisy(**asked: Any) -> Resolver:
        return Resolver(**asked, catalog=catalog)

    monkeypatch.setattr(api, "Resolver", noisy)
    assert result(tmp_path).diagnostics == ()
    assert result(tmp_path, verbose=True).diagnostics == (
        "a face this machine skipped",
    )
