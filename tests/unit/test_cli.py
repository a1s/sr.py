"""The command line: its flags, its exit codes, and what validate says."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from sr import meta
from sr.cli import COMMANDS, PARTIAL, PLANNED, as_written, main

ROOT = Path(__file__).resolve().parents[2]
SAKILA = ROOT / "example" / "sakila" / "sakila.kdl"
INVOICES = ROOT / "example" / "invoices" / "invoices.kdl"
BROKEN = ROOT / "tests" / "templates" / "broken" / "no-layout.kdl"
UNKNOWN = ROOT / "tests" / "templates" / "valid" / "unknown-names.kdl"
FONTS = ROOT / "example" / "fonts"


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
    assert set(COMMANDS) == {"build", "inspect", "validate", "version"}


def test_a_command_that_works_but_not_to_the_end_says_which_part() -> None:
    for name, (milestone, missing) in PARTIAL.items():
        code, said = run("help", name)
        assert code == 0
        assert milestone in said
        assert missing.split(";")[0] in said


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


def test_validate_reports_what_doc_cli_says_it_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The block doc/cli.md#sr-validate prints, word for word.
    # Run from the repository root and given the template relatively,
    # because the font paths in that block are relative and they are
    # relative to the directory the command was run from.
    monkeypatch.chdir(ROOT)
    expected = [
        "template example/sakila/sakila.kdl",
        '  report "DVD rental payments" version 2 by als',
        "  Payments by customer, from the Sakila sample database",
        "  page 595.276 x 841.89 pt, margins "
        "left 70.866 right 42.52 top 42.52 bottom 42.52",
        "  2 columns, 1 group, 6 members, 5 variables, 4 fonts, 1 data blob",
        "parameters",
        '  period_start  date      default "2005-01-01"  prompt',
        '  period_end    date      default "2006-01-01"  prompt',
        "  as_of         datetime  defaultexpr",
        "fonts",
        '  body       8pt   explicit  example/fonts/Go-Regular.ttf  "Go"',
        '  bold       8pt   explicit  example/fonts/Go-Bold.ttf  "Go"',
        '  pagetitle  12pt  explicit  example/fonts/Go-Regular.ttf  "Go"',
        '  title      14pt  explicit  example/fonts/Go-Bold.ttf  "Go"',
        "ok",
    ]
    code, said = run("validate", "example/sakila/sakila.kdl")
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


def test_a_template_with_subreports_lists_them_by_path() -> None:
    # doc/cli.md#sr-validate asks for the path and nothing after it.
    # The path already ends in the band and the node, and what the node
    # names -- a file, an embedded layout -- is not part of the section.
    _, said = run("validate", str(INVOICES))
    listed = section_of(said, "subreports")
    assert listed == [
        'report > layout > group "region" > summary > subreport',
        'report > layout > group "region" > detail > subreport',
    ]


def test_the_counts_line_counts_subreport_nodes_not_layouts() -> None:
    # invoices.kdl declares one `embedded` layout and writes
    # two `subreport` nodes, one of which names that layout.
    # The count is of the nodes: what runs, not what is available to run.
    _, said = run("validate", str(INVOICES))
    assert "2 subreports" in counts_of(said)


def test_a_layout_with_no_columns_node_still_counts_one_column(
    tmp_path: Path,
) -> None:
    # doc/template.md#columns: one column is what the absence means.
    # Zero-suppression drops the other terms, and dropping this one
    # would say the template has no columns rather than one.
    probe = tmp_path / "probe.kdl"
    probe.write_text(
        f"""
report name="probe" {{
  font "body" file="{FACE}" size=10
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=20 {{ field text="x" left=0 top=0 width=50 height=12 }}
  }}
}}
""",
        encoding="utf-8",
    )
    code, said = run("validate", str(probe))
    assert code == 0
    assert counts_of(said) == "1 column, 1 font"


def counts_of(said: str) -> str:
    """Return the line of a validate report that counts what is in it.

    It is the one header line that opens with a number,
    the rest being the name, the description and the page geometry.

    Args:
        said: What the command printed.

    """
    found = [
        one.strip()
        for one in said.splitlines()
        if one.startswith("  ") and one.strip()[:1].isdigit()
    ]
    assert len(found) == 1
    return found[0]


def section_of(said: str, name: str) -> list[str]:
    """Return the lines under one heading of a validate report, unindented.

    Args:
        said: What the command printed.
        name: The heading.

    """
    lines = said.splitlines()
    start = lines.index(name) + 1
    found = []
    for line in lines[start:]:
        if not line.startswith("  "):
            break
        found.append(line.strip())
    return found


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


# -- the fonts a template declares ------------------------------------
#
# doc/cli.md#sr-validate resolves them, because that is the one part
# of the check that depends on the machine rather than on the document.


def test_a_font_named_by_file_resolves_without_touching_the_host(
    tmp_path: Path,
) -> None:
    template = tmp_path / "explicit.kdl"
    template.write_text(
        'report name="explicit" {\n'
        f'  font "body" file="{(FONTS / "Go-Regular.ttf").as_posix()}" size=9\n'
        '  layout pagesize="A4" {\n'
        '    style font="body" color="black"\n'
        '    detail height=20 { field text="x" left=0 top=0 width=10 height=10 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    code, said = run("validate", str(template), "--verbose")
    assert code == 0
    assert "explicit" in said
    assert '"Go"' in said
    assert "diagnostics" not in said


def test_strict_fonts_refuses_a_typeface_and_says_why(tmp_path: Path) -> None:
    template = tmp_path / "strict.kdl"
    template.write_text(
        'report name="strict" {\n'
        '  font "body" typeface="Helvetica" size=9\n'
        '  layout pagesize="A4" {\n'
        '    style font="body" color="black"\n'
        '    detail height=20 { field text="x" left=0 top=0 width=10 height=10 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    code, said = run("validate", str(template), "--strict-fonts")
    assert code == 1
    assert "failures" in said
    assert 'font "body": strict mode admits only' in said
    assert 'typeface "Helvetica"' in said
    assert said.splitlines()[-1] != "ok"


def test_a_font_that_did_not_resolve_is_not_in_the_font_table(
    tmp_path: Path,
) -> None:
    template = tmp_path / "missing.kdl"
    template.write_text(
        'report name="missing" {\n'
        '  font "gone" file="nowhere.ttf" size=9\n'
        f'  font "body" file="{(FONTS / "Go-Regular.ttf").as_posix()}" size=9\n'
        '  layout pagesize="A4" {\n'
        '    style font="body" color="black"\n'
        '    detail height=20 { field text="x" left=0 top=0 width=10 height=10 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    code, said = run("validate", str(template))
    assert code == 1
    lines = said.splitlines()
    assert any(one.strip().startswith("body") for one in lines)
    assert not any(one.strip().startswith("gone  ") for one in lines)
    assert 'font "gone": cannot read' in said


def test_a_declared_style_the_face_has_not_got_is_a_warning(tmp_path: Path) -> None:
    template = tmp_path / "declared.kdl"
    template.write_text(
        'report name="declared" {\n'
        f'  font "body" file="{(FONTS / "Go-Regular.ttf").as_posix()}" '
        "size=9 bold=#true\n"
        '  layout pagesize="A4" {\n'
        '    style font="body" color="black"\n'
        '    detail height=20 { field text="x" left=0 top=0 width=10 height=10 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    code, said = run("validate", str(template))
    assert code == 0
    assert "warnings" in said
    assert 'font "body" declares bold' in said


def test_a_font_taking_its_face_from_a_blob_names_the_blob(tmp_path: Path) -> None:
    face = base64.b64encode((FONTS / "Go-Regular.ttf").read_bytes()).decode()
    wrapped = "\n".join(face[at : at + 72] for at in range(0, len(face), 72))
    template = tmp_path / "blob.kdl"
    template.write_text(
        'report name="blob" {\n'
        '  data "face" encoding="base64" {\n'
        f'    content """\n{wrapped}\n"""\n'
        "  }\n"
        '  font "body" data="face" size=9\n'
        '  layout pagesize="A4" {\n'
        '    style font="body" color="black"\n'
        '    detail height=20 { field text="x" left=0 top=0 width=10 height=10 }\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    code, said = run("validate", str(template))
    assert code == 0
    assert "explicit  data face" in said


# -- the fonts of a subreport -----------------------------------------
#
# doc/cli.md#sr-validate has a `template=` subreport loaded
# and checked with its host, and a font is part of what is checked.
# The fonts it declares are not listed, though: the table is what
# this template declares, and the subreport's belongs to its own file.
# Only what went wrong crosses over.

FACE = (FONTS / "Go-Regular.ttf").as_posix()


def nested(tmp_path: Path, sub: str, template: str = "sub.kdl") -> Path:
    """Write a host template and the subreport it invokes, and return it.

    Args:
        tmp_path: Where to write the pair.
        sub: The `font` node the subreport declares.
        template: What the host calls the subreport,
            which is also where it is written.

    """
    file = tmp_path / template
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        f"""
report name="sub" {{
  {sub}
  layout pagesize="A4" {{
    detail height=20 {{ field text="y" left=0 top=0 width=50 height=12 }}
  }}
}}
""",
        encoding="utf-8",
    )
    host = tmp_path / "host.kdl"
    host.write_text(
        f"""
report name="host" {{
  font "body" file="{FACE}" size=9
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=20 {{
      field text="x" left=0 top=0 width=50 height=12
      subreport template="{template}" seq=1 data="[]"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    return host


def test_a_font_a_subreport_cannot_resolve_fails_the_host(tmp_path: Path) -> None:
    host = nested(tmp_path, 'font "inner" file="no-such-face.ttf" size=9')
    code, said = run("validate", str(host))
    assert code == 1
    assert "failures" in said
    assert 'font "inner"' in said
    assert "ok" not in said.splitlines()


def test_a_subreport_font_warning_reaches_the_host(tmp_path: Path) -> None:
    host = nested(tmp_path, 'font "inner" typeface="NoSuchFamilyAnywhere" size=9')
    code, said = run("validate", str(host))
    assert code == 0
    assert "warnings" in said
    assert 'typeface "NoSuchFamilyAnywhere" was not found' in said


def test_a_font_warning_is_spelled_the_way_a_load_warning_is(tmp_path: Path) -> None:
    # A warning knows its kind and the node it happened at, and the line
    # used to carry the message alone: a substituted typeface said what
    # had happened without saying which `font` node it happened to,
    # which in a subreport is the only thing that identifies it.
    host = nested(tmp_path, 'font "inner" typeface="NoSuchFamilyAnywhere" size=9')
    code, said = run("validate", str(host))
    assert code == 0
    lines = [one.strip() for one in said.splitlines() if "NoSuchFamily" in one]
    assert len(lines) == 1
    assert lines[0].startswith("font: ")
    assert lines[0].endswith('(report > font "inner")')


def test_a_subreport_font_is_not_listed_among_the_host_s(tmp_path: Path) -> None:
    bold = (FONTS / "Go-Bold.ttf").as_posix()
    host = nested(tmp_path, f'font "inner" file="{bold}" size=9')
    code, said = run("validate", str(host))
    assert code == 0
    listed = said.split("fonts", 1)[1]
    assert "body" in listed
    assert "inner" not in listed


def test_a_subreport_resolves_a_file_against_its_own_directory(
    tmp_path: Path,
) -> None:
    # The face sits beside the subreport, which names it without
    # a directory.  Resolved against the host's basedir this would
    # be looked for one directory up, and there is nothing there.
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "Face.ttf").write_bytes((FONTS / "Go-Regular.ttf").read_bytes())
    host = nested(tmp_path, 'font "inner" file="Face.ttf" size=9', "inner/sub.kdl")
    code, said = run("validate", str(host))
    assert code == 0
    assert "failures" not in said


def test_one_template_invoked_twice_is_reported_once(tmp_path: Path) -> None:
    host = nested(tmp_path, 'font "inner" file="no-such-face.ttf" size=9')
    text = host.read_text(encoding="utf-8")
    twice = text.replace(
        'subreport template="sub.kdl" seq=1 data="[]"',
        'subreport template="sub.kdl" seq=1 data="[]"\n'
        '      subreport template="sub.kdl" seq=2 data="[]"',
    )
    host.write_text(twice, encoding="utf-8")
    code, said = run("validate", str(host))
    assert code == 1
    assert said.count('font "inner"') == 1
    assert "1 font" in said or said.count("failures") == 1


def test_the_help_names_the_font_flags() -> None:
    _, said = run("help", "validate")
    assert "--strict-fonts" in said
    assert "--verbose" in said


# -- build ------------------------------------------------------------

MINIMAL = ROOT / "example" / "minimal" / "minimal.kdl"
FILMS = ROOT / "example" / "minimal" / "films.jsonl"
REPRODUCIBLE = ("--build-time", "2026-08-04T09:12:44Z", "--strict-fonts")


def build_minimal(tmp_path: Path, *extra: str) -> tuple[int, Path]:
    """Build the minimal example into ``tmp_path`` and return the code."""
    out = tmp_path / "out.srp.jsonl"
    code, _ = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(out),
        *REPRODUCIBLE,
        *extra,
    )
    return code, out


def test_build_writes_a_printout(tmp_path: Path) -> None:
    code, out = build_minimal(tmp_path)
    assert code == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"kind":"header"' in lines[0]
    assert '"kind":"page"' in lines[1]


def test_build_writes_to_standard_output_when_asked(tmp_path: Path) -> None:
    code, said = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        "-",
        "--format",
        "jsonl",
        *REPRODUCIBLE,
    )
    assert code == 0
    assert said.splitlines()[0].startswith('{"sr":1')


def test_build_to_a_stream_without_a_format_is_a_usage_error() -> None:
    code, _ = run("build", "-t", str(MINIMAL), "-d", str(FILMS), "-o", "-")
    assert code == 2


def test_an_extension_that_names_no_format_is_a_usage_error(tmp_path: Path) -> None:
    code, _ = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(tmp_path / "out.txt"),
        *REPRODUCIBLE,
    )
    assert code == 2


def test_a_format_this_engine_cannot_write_yet_says_so(tmp_path: Path) -> None:
    for name in ("out.pdf", "out.cbor"):
        code, _ = run(
            "build",
            "-t",
            str(MINIMAL),
            "-d",
            str(FILMS),
            "-o",
            str(tmp_path / name),
            *REPRODUCIBLE,
        )
        assert code == 1


def test_build_needs_a_template_and_an_output(tmp_path: Path) -> None:
    assert run("build", "-o", str(tmp_path / "out.srp.jsonl"))[0] == 2
    assert run("build", "-t", str(MINIMAL))[0] == 2


def test_build_takes_no_positional_arguments(tmp_path: Path) -> None:
    code, _ = run(
        "build", str(MINIMAL), "-o", str(tmp_path / "out.srp.jsonl"), *REPRODUCIBLE
    )
    assert code == 2


def test_a_parameter_the_template_does_not_declare_fails_the_build(
    tmp_path: Path,
) -> None:
    code, _ = build_minimal(tmp_path, "--param", "nosuch=1")
    assert code == 1


def test_a_template_that_will_not_load_fails_the_build(tmp_path: Path) -> None:
    code, _ = run(
        "build",
        "-t",
        str(BROKEN),
        "-o",
        str(tmp_path / "out.srp.jsonl"),
        *REPRODUCIBLE,
    )
    assert code == 1


def test_a_build_time_that_is_not_a_time_fails_the_build(tmp_path: Path) -> None:
    out = tmp_path / "out.srp.jsonl"
    code, _ = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(out),
        "--build-time",
        "yesterday",
        "--strict-fonts",
    )
    assert code == 1


# -- inspect ----------------------------------------------------------


def test_inspect_dumps_the_header_and_the_marks(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    code, said = run("inspect", str(out))
    assert code == 0
    lines = said.splitlines()
    assert lines[0].startswith("printout ")
    assert 'engine "sr 0.1.0"' in lines[1]
    assert any(one.strip().startswith("text  box ") for one in lines)
    assert any(one.strip() == '"ACADEMY DINOSAUR"' for one in lines)


def test_inspect_summary_leaves_the_pages_out(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    code, said = run("inspect", str(out), "--summary")
    assert code == 0
    assert "page 1" not in said


def test_inspect_takes_a_range_of_pages(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    code, said = run("inspect", str(out), "--pages", "2-")
    assert code == 0
    assert "page 1  " not in said


def test_a_range_that_is_not_one_fails(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    code, _ = run("inspect", str(out), "--pages", "first")
    assert code == 1


def test_inspect_needs_one_printout() -> None:
    assert run("inspect")[0] == 2


def test_a_file_that_is_not_a_printout_fails(tmp_path: Path) -> None:
    path = tmp_path / "notes.srp.jsonl"
    path.write_text("this is not JSON\n", encoding="utf-8")
    code, _ = run("inspect", str(path))
    assert code == 1


def test_a_stream_is_reconfigured_for_the_formats_own_line_ending() -> None:
    """doc/printout.md#encoding is LF and UTF-8 wherever it is written.

    Standard output is a text stream the platform set up, and
    on Windows it arrives translating every LF and encoding in
    the console's codepage.  The stream below stands in for that one.
    Its newline is what a Windows console does; its encoding only has
    to be something other than UTF-8, so it is the narrowest one
    there is, and the character outside it is what makes that half
    of the test fail loudly rather than quietly.

    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", newline="\r\n")
    as_written(stream).write("one\ntwo\n\u4e2d\n")
    stream.flush()
    assert raw.getvalue() == "one\ntwo\n\u4e2d\n".encode()


def test_a_stream_that_cannot_be_reconfigured_is_left_alone() -> None:
    held = io.StringIO()
    assert as_written(held) is held


def test_an_output_directory_that_is_not_there_is_a_diagnostic(
    tmp_path: Path,
) -> None:
    code, _ = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(tmp_path / "nosuch" / "out.srp.jsonl"),
        *REPRODUCIBLE,
    )
    assert code == 1


def test_verbose_is_accepted_by_build(tmp_path: Path) -> None:
    code, _ = build_minimal(tmp_path, "-v")
    assert code == 0
