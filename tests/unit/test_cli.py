"""The command line: its flags, its exit codes, and what validate says."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from sr import meta
from sr.cli import COMMANDS, PLANNED, main

ROOT = Path(__file__).resolve().parents[2]
SAKILA = ROOT / "example" / "sakila" / "sakila.kdl"
INVOICES = ROOT / "example" / "invoices" / "invoices.kdl"
BROKEN = ROOT / "tests" / "templates" / "broken" / "no-layout.kdl"
UNKNOWN = ROOT / "tests" / "templates" / "valid" / "unknown-names.kdl"


def run(*argv: str) -> tuple[int, str]:
    """Run one command and return its exit code and what it printed.

    Args:
        *argv: The arguments after the program name.

    """
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


# -- commands ---------------------------------------------------------


def test_version_prints_what_a_printout_header_carries() -> None:
    assert run("version") == (0, meta.engine() + "\n")


def test_version_takes_no_arguments() -> None:
    code, _ = run("version", "extra")
    assert code == 2


def test_no_command_is_a_usage_error() -> None:
    code, _ = run()
    assert code == 2


def test_an_unknown_command_is_a_usage_error() -> None:
    code, _ = run("frobnicate")
    assert code == 2


def test_help_lists_every_command() -> None:
    code, said = run("help")
    assert code == 0
    for name in ("build", "validate", "render", "inspect", "version"):
        assert name in said


@pytest.mark.parametrize("spelling", ["-h", "--help"])
def test_a_commands_own_help_is_the_same_as_asking_for_it(spelling: str) -> None:
    asked = run("help", "validate")
    assert run("validate", spelling) == asked
    assert asked[0] == 0


@pytest.mark.parametrize("name", sorted(PLANNED))
def test_a_command_that_is_not_written_yet_says_so(name: str) -> None:
    code, said = run(name, "-t", str(SAKILA))
    assert code == 1
    assert said == ""


def test_the_commands_that_work_are_the_ones_listed() -> None:
    assert set(COMMANDS) == {"validate", "version"}


# -- the flag parser --------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("validate", "-t", str(SAKILA)),
        ("validate", "--template", str(SAKILA)),
        ("validate", "--template=" + str(SAKILA)),
        ("validate", "-template", str(SAKILA)),
        ("validate", str(SAKILA)),
        ("validate", str(SAKILA), "--quiet"),
    ],
)
def test_every_spelling_of_a_flag_reaches_the_same_template(
    argv: tuple[str, ...],
) -> None:
    code, _ = run(*argv)
    assert code == 0


def test_a_flag_may_follow_a_positional() -> None:
    code, said = run("validate", str(SAKILA), "-q")
    assert (code, said) == (0, "")


def test_an_unknown_flag_is_a_usage_error() -> None:
    code, _ = run("validate", str(SAKILA), "--wobble")
    assert code == 2


def test_a_flag_with_no_value_is_a_usage_error() -> None:
    code, _ = run("validate", "--template")
    assert code == 2


def test_two_templates_is_a_usage_error() -> None:
    code, _ = run("validate", "-t", str(SAKILA), str(INVOICES))
    assert code == 2


def test_no_template_at_all_is_a_usage_error() -> None:
    code, _ = run("validate")
    assert code == 2


# -- parameters -------------------------------------------------------


def test_a_declared_parameter_is_accepted() -> None:
    code, _ = run("validate", str(SAKILA), "--param", "period_start=2005-06-01", "-q")
    assert code == 0


def test_an_attached_parameter_value_is_the_same_flag() -> None:
    code, _ = run("validate", str(SAKILA), "--param=period_start=2005-06-01", "-q")
    assert code == 0


def test_a_parameter_the_template_does_not_declare_is_refused() -> None:
    code, _ = run("validate", str(SAKILA), "--param", "nosuch=1", "-q")
    assert code == 1


def test_a_parameter_given_twice_is_refused_rather_than_taken_last() -> None:
    code, _ = run(
        "validate",
        str(SAKILA),
        "--param",
        "period_start=2005-01-01",
        "--param",
        "period_start=2006-01-01",
        "-q",
    )
    assert code == 2


def test_a_parameter_value_that_does_not_parse_is_refused() -> None:
    code, _ = run("validate", str(SAKILA), "--param", "period_start=yesterday", "-q")
    assert code == 1


def test_a_parameter_without_an_equals_sign_is_a_usage_error() -> None:
    code, _ = run("validate", str(SAKILA), "--param", "period_start")
    assert code == 2


# -- the report -------------------------------------------------------


def test_validate_reports_what_doc_cli_says_it_does() -> None:
    # The block doc/cli.md#sr-validate prints, word for word, less
    # the `fonts` section, which needs resolution and arrives in M5.
    expected = [
        f"template {SAKILA}",
        '  report "DVD rental payments" version 2 by als',
        "  Payments by customer, from the Sakila sample database",
        "  page 595.276 x 841.89 pt, margins "
        "left 70.866 right 42.52 top 42.52 bottom 42.52",
        "  2 columns, 1 group, 6 members, 5 variables, 4 fonts, 1 data blob",
        "parameters",
        '  period_start  date      default "2005-01-01"  prompt',
        '  period_end    date      default "2006-01-01"  prompt',
        "  as_of         datetime  defaultexpr",
        "ok",
    ]
    code, said = run("validate", str(SAKILA))
    assert code == 0
    assert said.splitlines() == expected


def test_ok_is_the_last_line_so_that_tail_reads_the_verdict() -> None:
    _, said = run("validate", str(INVOICES))
    assert said.splitlines()[-1] == "ok"


def test_a_template_that_does_not_load_prints_no_ok() -> None:
    code, said = run("validate", str(BROKEN))
    assert code == 1
    assert said == ""


def test_quiet_prints_nothing_at_all() -> None:
    code, said = run("validate", str(SAKILA), "--quiet")
    assert (code, said) == (0, "")


def test_a_template_with_subreports_lists_them() -> None:
    _, said = run("validate", str(INVOICES))
    assert "subreports" in said
    assert "region_sheet.kdl" in said
    assert "embedded 'lines' inline" in said


# -- names the format does not define ---------------------------------
#
# doc/template.md#unknown-names, from the command line: the two flags,
# and where a warning about one shows up.


def test_an_unknown_name_is_a_warning_and_not_a_failure() -> None:
    code, said = run("validate", str(UNKNOWN))
    assert code == 0
    assert "warnings" in said
    assert "unknown property `wobble`" in said


def test_strict_names_refuses_what_is_otherwise_a_warning() -> None:
    code, said = run("validate", str(UNKNOWN), "--strict-names")
    assert code == 1
    assert said == ""


def test_accept_silences_one_name() -> None:
    code, said = run("validate", str(UNKNOWN), "--accept", "wobble")
    assert code == 0
    assert "unknown property `wobble`" not in said
    assert "unknown node `sprocket`" in said


def test_accept_is_repeatable() -> None:
    code, said = run(
        "validate", str(UNKNOWN), "--accept", "wobble", "--accept=sprocket"
    )
    assert code == 0
    assert "warnings" not in said


def test_accept_and_strict_names_together_leave_the_named_ones_alone() -> None:
    code, _ = run(
        "validate",
        str(UNKNOWN),
        "--strict-names",
        "--accept",
        "wobble",
        "--accept",
        "sprocket",
    )
    assert code == 0


def test_accept_given_the_same_name_twice_is_that_one_name() -> None:
    code, _ = run(
        "validate",
        str(UNKNOWN),
        "--strict-names",
        "--accept",
        "wobble",
        "--accept",
        "wobble",
        "--accept",
        "sprocket",
    )
    assert code == 0


def test_accept_with_no_value_is_a_usage_error() -> None:
    code, _ = run("validate", str(UNKNOWN), "--accept")
    assert code == 2


def test_the_help_names_both_flags() -> None:
    _, said = run("help", "validate")
    assert "--strict-names" in said
    assert "--accept" in said
