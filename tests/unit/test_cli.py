"""The command line: its flags, its exit codes, and what validate says."""

from __future__ import annotations

import base64
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from sr import meta
from sr.cli import COMMANDS, as_written, main
from sr.printout.inspect import read_printout

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


def test_the_commands_that_work_are_the_ones_listed() -> None:
    assert set(COMMANDS) == {
        "build",
        "inspect",
        "render",
        "validate",
        "version",
    }


def test_help_names_no_milestone_any_more() -> None:
    """Every command works, so none of them says which milestone it awaits."""
    for name in ("", "build", "render"):
        code, said = run("help", *([name] if name else []))
        assert code == 0
        assert "not implemented" not in said
        assert "M14" not in said


# -- the flag parser --------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("validate", "-t", str(SAKILA)),
        ("validate", "--template", str(SAKILA)),
        ("validate", "--template=" + str(SAKILA)),
        ("validate", "-t=" + str(SAKILA)),
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


def test_a_parameter_the_template_does_not_declare_is_a_usage_error() -> None:
    # doc/cli.md#flags: the mistake is in the command line,
    # although the template has to be read to find it.
    code, _ = run("validate", str(SAKILA), "--param", "nosuch=1", "-q")
    assert code == 2


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


def test_a_font_warning_is_spelled_the_way_the_dump_spells_one(
    tmp_path: Path,
) -> None:
    # A warning knows its kind and the node it happened at, and the line
    # used to carry the message alone: a substituted typeface said what
    # had happened without saying which `font` node it happened to,
    # which in a subreport is the only thing that identifies it.
    # doc/cli.md#sr-inspect spells one, and the check spells it alike.
    host = nested(tmp_path, 'font "inner" typeface="NoSuchFamilyAnywhere" size=9')
    code, said = run("validate", str(host))
    assert code == 0
    lines = [one.strip() for one in said.splitlines() if "NoSuchFamily" in one]
    assert len(lines) == 1
    assert lines[0].startswith('font  report > font "inner"  typeface ')


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


def test_build_writes_a_pdf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out.pdf"
    code = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(out),
        *REPRODUCIBLE,
    )[0]
    assert code == 0
    assert out.read_bytes().startswith(b"%PDF-1.7\n")
    assert capsys.readouterr().err.endswith(f"{out}: 1 page, 1 font\n")


def test_build_writes_cbor_holding_the_records_ndjson_does(tmp_path: Path) -> None:
    """doc/printout.md#cbor: the same objects, in the same order."""
    _, ndjson = build_minimal(tmp_path)
    cbor = tmp_path / "out.srp.cbor"
    code = run(
        "build",
        "-t",
        str(MINIMAL),
        "-d",
        str(FILMS),
        "-o",
        str(cbor),
        *REPRODUCIBLE,
    )[0]
    assert code == 0
    assert read_printout(cbor) == read_printout(ndjson)
    assert cbor.read_bytes()[:3] == bytes.fromhex("aa6273"), "a map of 10, then `sr`"


def test_build_writes_cbor_to_standard_output_as_bytes(tmp_path: Path) -> None:
    written = tmp_path / "out.srp.cbor"
    run(
        "build", "-t", str(MINIMAL), "-d", str(FILMS), "-o", str(written), *REPRODUCIBLE
    )
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8")
    code = main(
        [
            "build",
            "-t",
            str(MINIMAL),
            "-d",
            str(FILMS),
            "-o",
            "-",
            "--format",
            "cbor",
            *REPRODUCIBLE,
        ],
        out,
    )
    out.flush()
    assert code == 0
    standard = read_cbor_bytes(raw.getvalue(), tmp_path)
    assert standard[1:] == read_printout(written)[1:]


def read_cbor_bytes(data: bytes, tmp_path: Path) -> list[object]:
    """Return the records some CBOR bytes hold, by way of a file."""
    path = tmp_path / "piped.srp.cbor"
    path.write_bytes(data)
    return read_printout(path)


def test_build_needs_a_template_and_an_output(tmp_path: Path) -> None:
    assert run("build", "-o", str(tmp_path / "out.srp.jsonl"))[0] == 2
    assert run("build", "-t", str(MINIMAL))[0] == 2


def test_build_takes_no_positional_arguments(tmp_path: Path) -> None:
    code, _ = run(
        "build", str(MINIMAL), "-o", str(tmp_path / "out.srp.jsonl"), *REPRODUCIBLE
    )
    assert code == 2


def test_a_parameter_the_template_does_not_declare_is_a_build_usage_error(
    tmp_path: Path,
) -> None:
    code, _ = build_minimal(tmp_path, "--param", "nosuch=1")
    assert code == 2


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


@pytest.mark.parametrize("template", [MINIMAL, ROOT / "nosuch.kdl"])
def test_a_build_time_that_is_not_a_time_is_a_usage_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    template: Path,
) -> None:
    """doc/cli.md#sr-build: checked before the template is read."""
    out = tmp_path / "out.srp.jsonl"
    code, _ = run(
        "build",
        "-t",
        str(template),
        "-o",
        str(out),
        "--build-time",
        "yesterday",
    )
    assert code == 2
    assert "--build-time" in capsys.readouterr().err
    assert not out.exists()


# -- render -----------------------------------------------------------

SAKILA_DATA = ROOT / "example" / "sakila" / "payments.jsonl"


def built(tmp_path: Path, name: str, *source: str) -> Path:
    """Build a report to a file of the given name, and return the file.

    Args:
        tmp_path: Where to write it.
        name: Its name, whose extension chooses the format.
        *source: The template and data flags; the minimal example's
            when none are given.

    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / name
    given = source or ("-t", str(MINIMAL), "-d", str(FILMS))
    code, _ = run("build", *given, "-o", str(out), *REPRODUCIBLE)
    assert code == 0
    return out


@pytest.mark.parametrize("printout", ["out.srp.jsonl", "out.srp.cbor"])
def test_render_gives_the_pdf_build_gives(
    tmp_path: Path,
    printout: str,
) -> None:
    """doc/cli.md: the two routes to a PDF produce the same bytes.

    Sakila has images by data and by file, barcodes, an outline,
    and links of both kinds, so every path the printout carries
    has to resolve from where the printout was written.

    """
    sakila = ("-t", str(SAKILA), "-d", str(SAKILA_DATA))
    direct = built(tmp_path, "direct.pdf", *sakila)
    written = built(tmp_path / "elsewhere", printout, *sakila)
    rendered = tmp_path / "rendered.pdf"
    code, _ = run("render", str(written), "-o", str(rendered))
    assert code == 0
    assert rendered.read_bytes() == direct.read_bytes()


def test_render_writes_to_standard_output_as_bytes(tmp_path: Path) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    direct = built(tmp_path, "out.pdf")
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8")
    code = main(["render", str(printout), "-o", "-"], out)
    out.flush()
    assert code == 0
    assert raw.getvalue() == direct.read_bytes()


def test_build_writes_a_pdf_to_standard_output(tmp_path: Path) -> None:
    direct = built(tmp_path, "out.pdf")
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8")
    code = main(
        [
            "build",
            "-t",
            str(MINIMAL),
            "-d",
            str(FILMS),
            "-o",
            "-",
            "--format",
            "pdf",
            *REPRODUCIBLE,
        ],
        out,
    )
    out.flush()
    assert code == 0
    assert raw.getvalue() == direct.read_bytes()


def test_render_repeats_the_warnings_the_printout_carries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doc/cli.md#sr-render: this is where somebody looks at the document."""
    printout = built(tmp_path, "out.srp.jsonl", "-t", str(UNKNOWN))
    capsys.readouterr()
    out = tmp_path / "out.pdf"
    code, _ = run("render", str(printout), "-o", str(out))
    said = capsys.readouterr().err.splitlines()
    assert code == 0
    assert said[0] == (
        "warning: unknown  report > layout > detail wobble=  "
        "unknown property `wobble`, accepted and ignored"
    )
    assert said[1].startswith("warning: unknown  report > layout > detail > ")
    assert said[-1] == f"{out}: 1 page"


def test_a_render_that_fails_leaves_the_previous_file_alone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole document is rendered before the output is opened."""
    shutil.copytree(FONTS, tmp_path / "fonts")
    shutil.copytree(MINIMAL.parent, tmp_path / "minimal")
    printout = built(
        tmp_path / "minimal",
        "out.srp.jsonl",
        "-t",
        str(tmp_path / "minimal" / "minimal.kdl"),
        "-d",
        str(FILMS),
    )
    (tmp_path / "fonts" / "Go-Regular.ttf").unlink()
    previous = tmp_path / "out.pdf"
    previous.write_bytes(b"previous")
    capsys.readouterr()
    code, _ = run("render", str(printout), "-o", str(previous))
    assert code == 1
    said = capsys.readouterr().err
    assert said.startswith("sr: font 'body': cannot read ")
    assert "Go-Regular.ttf" in said
    assert previous.read_bytes() == b"previous"


@pytest.mark.parametrize("name", ["out.pdf", "out.srp.jsonl"])
def test_a_build_that_cannot_write_still_says_what_it_warned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """A render that refuses a face is explained by a warning before it."""
    out = tmp_path / "missing" / name
    code, _ = run("build", "-t", str(UNKNOWN), "-o", str(out), *REPRODUCIBLE)
    said = capsys.readouterr().err.splitlines()
    assert code == 1
    assert [one.split(":")[0] for one in said] == ["warning", "warning", "sr"]


def test_a_pdf_to_a_stream_that_takes_no_bytes_is_named_a_pdf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    capsys.readouterr()
    code, _ = run("render", str(printout), "-o", "-")
    assert code == 1
    said = capsys.readouterr().err
    assert said == "sr: standard output takes no bytes, and a PDF is bytes\n"


def test_uncompressed_leaves_the_streams_readable(tmp_path: Path) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    out = tmp_path / "out.pdf"
    code, _ = run("render", "--uncompressed", str(printout), "-o", str(out))
    assert code == 0
    data = out.read_bytes()
    assert b"/FlateDecode" not in data
    assert b"\nBT\n" in data


def test_render_takes_one_printout_and_an_output(tmp_path: Path) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    pdf = str(tmp_path / "out.pdf")
    assert run("render", "-o", pdf)[0] == 2
    assert run("render", str(printout), str(printout), "-o", pdf)[0] == 2
    assert run("render", str(printout))[0] == 2
    assert run("render", str(printout), "-o", pdf, "--pages", "1")[0] == 2


def test_render_of_a_file_that_is_not_a_printout_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    junk = tmp_path / "junk.srp.jsonl"
    junk.write_text('{"sr":1,"kind":"header"}\n', encoding="utf-8")
    code, _ = run("render", str(junk), "-o", str(tmp_path / "out.pdf"))
    assert code == 1
    assert "not a printout" in capsys.readouterr().err
    assert not (tmp_path / "out.pdf").exists()


@pytest.mark.parametrize("command", ["render", "inspect"])
def test_a_printout_that_is_not_utf_8_is_not_a_printout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    junk = tmp_path / "junk.srp.jsonl"
    junk.write_bytes(b'{"sr":1}\n\xff\n')
    output = ("-o", str(tmp_path / "out.pdf")) if command == "render" else ()
    code, _ = run(command, str(junk), *output)
    assert code == 1
    assert "not a printout: byte 9 is not UTF-8" in capsys.readouterr().err


def test_the_help_names_the_render_flags() -> None:
    code, said = run("help", "render")
    assert code == 0
    assert "--out" in said
    assert "--uncompressed" in said


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


def test_a_range_that_is_not_one_is_a_usage_error(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    code, _ = run("inspect", str(out), "--pages", "first")
    assert code == 2


def test_inspect_needs_one_printout() -> None:
    assert run("inspect")[0] == 2


def test_inspect_reads_cbor_as_it_reads_ndjson(tmp_path: Path) -> None:
    _, ndjson = build_minimal(tmp_path)
    cbor = tmp_path / "out.srp.cbor"
    run("build", "-t", str(MINIMAL), "-d", str(FILMS), "-o", str(cbor), *REPRODUCIBLE)
    code, from_cbor = run("inspect", str(cbor))
    assert code == 0
    _, from_ndjson = run("inspect", str(ndjson))
    assert from_cbor.splitlines()[1:] == from_ndjson.splitlines()[1:]


def test_inspect_says_which_invariants_a_printout_breaks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """doc/cli.md#sr-inspect: after the dump, on standard error, and exit 1."""
    _, out = build_minimal(tmp_path)
    header, page = out.read_text(encoding="utf-8").splitlines()
    broken = tmp_path / "broken.srp.jsonl"
    broken.write_text(
        header.replace('"pages":1', '"pages":2')
        + "\n"
        + page.replace('"font":"body"', '"font":"nowhere"', 1)
        + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    code, said = run("inspect", str(broken))
    assert code == 1
    assert '"ACADEMY DINOSAUR"' in said, "the dump is still written"
    errors = capsys.readouterr().err.splitlines()
    assert errors == [
        "invariant: the header counts 2 pages, and 1 page lines follow",
        'invariant: page 1, mark 1: its font "nowhere" is not in'
        " the header's font table",
        f"sr: {broken}: 2 invariants broken",
    ]


def test_inspect_dumps_an_outline_entry_at_its_point(tmp_path: Path) -> None:
    """doc/cli.md#sr-inspect quotes what a template wrote as text."""
    template = tmp_path / "outline.kdl"
    face = (FONTS / "Go-Regular.ttf").as_posix()
    template.write_text(
        'report name="Outline" {\n'
        f'  font "body" file="{face}" size=5\n'
        "  layout width=100 height=60 leftmargin=5 rightmargin=5 topmargin=5"
        " bottommargin=5 {\n"
        '    style font="body" color="black"\n'
        "    title height=6 {\n"
        "      outline title=\"'Top'\" name=\"'top'\" closed=#true\n"
        "    }\n"
        "    detail height=10 {\n"
        '      xref type="outline" target="\'top\'" caption="\'Back up\'"'
        " left=0 top=0 width=20 height=6\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.jsonl"
    rows.write_text('{"n":1}\n', encoding="utf-8")
    out = tmp_path / "outline.srp.jsonl"
    code, _ = run(
        "build", "-t", str(template), "-d", str(rows), "-o", str(out), *REPRODUCIBLE
    )
    assert code == 0
    code, said = run("inspect", str(out))
    assert code == 0
    lines = [one.strip() for one in said.splitlines()]
    assert 'outline  at 5,5  level 1  "Top"  name "top"  closed' in lines
    assert 'xref  box 5,11 20x6  outline  "top"  caption "Back up"' in lines, "\n".join(
        lines
    )


def test_inspect_judges_the_whole_printout_whatever_it_dumps(tmp_path: Path) -> None:
    _, out = build_minimal(tmp_path)
    header, page = out.read_text(encoding="utf-8").splitlines()
    broken = tmp_path / "broken.srp.jsonl"
    broken.write_text(
        header + "\n" + page.replace('"lines":["Film title"]', '"lines":[]') + "\n",
        encoding="utf-8",
    )
    assert run("inspect", str(broken), "--summary")[0] == 1


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


# -- the streams' encoding --------------------------------------------
#
# doc/cli.md#streams: both streams are UTF-8, under any code page.
# On Windows a pipe or a file gets the ANSI code page, and text that
# page cannot hold stopped `inspect` at a traceback.  The stand-ins
# below encode as cp1252, the Western European page and the commonest.

# Text the stand-ins cannot hold.  `ķ` and `ū` are in neither cp1252
# nor cp1251: of the Windows ANSI code pages, only the Baltic cp1257
# has them.  An `é` would not do: cp1252 has one, and the crash would
# go untested.
LATVIAN = "Šķūnis"


def accented(tmp_path: Path) -> Path:
    """Write a template whose name and header hold text outside cp1252.

    Args:
        tmp_path: Where to write it.

    """
    template = tmp_path / "latvian.kdl"
    template.write_text(
        f"""
report name="{LATVIAN}" {{
  font "body" file="{FACE}" size=9
  layout pagesize="A4" {{
    style font="body" color="black"
    header {{ field text="{LATVIAN}" left=0 top=0 width=50 height=12 }}
    detail height=20 {{ field text="x" left=0 top=0 width=50 height=12 }}
  }}
}}
""",
        encoding="utf-8",
    )
    return template


def in_codepage(*argv: str) -> tuple[int, str]:
    """Run one command into a cp1252 stream and return what it wrote.

    The stream stands in for standard output on a Windows machine
    whose ANSI code page is cp1252.  What it wrote is decoded as UTF-8,
    so a stream left in its own encoding fails here rather than passing.

    Args:
        *argv: The arguments after the program name.

    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    code = main(list(argv), stream)
    stream.flush()
    return code, raw.getvalue().decode("utf-8")


def test_inspect_writes_utf8_where_the_code_page_cannot_hold_the_text(
    tmp_path: Path,
) -> None:
    out = tmp_path / "latvian.srp.jsonl"
    code, _ = run("build", "-t", str(accented(tmp_path)), "-o", str(out))
    assert code == 0
    code, said = in_codepage("inspect", str(out))
    assert code == 0
    assert f'"{LATVIAN}"' in [one.strip() for one in said.splitlines()]


def test_validate_writes_utf8_where_the_code_page_cannot_hold_the_text(
    tmp_path: Path,
) -> None:
    code, said = in_codepage("validate", str(accented(tmp_path)))
    assert code == 0
    assert f'  report "{LATVIAN}"' in said.splitlines()


def test_a_diagnostic_on_standard_error_is_utf8_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Python's own standard error escapes what its encoding cannot hold
    # rather than failing, so the stand-in does too: left alone,
    # it would write the `Š` as cp1252 and escape the `ķ` and the `ū`,
    # and the decode below is what catches that.
    raw = io.BytesIO()
    held = io.TextIOWrapper(raw, encoding="cp1252", errors="backslashreplace")
    monkeypatch.setattr(sys, "stderr", held)
    code, _ = run("validate", str(SAKILA), "--param", f"{LATVIAN}=1", "-q")
    held.flush()
    assert code == 2
    assert f"--param {LATVIAN}: " in raw.getvalue().decode("utf-8")


def test_a_path_that_is_not_text_is_escaped_alike_on_both_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A lone surrogate is what Python makes of a file name that is not
    # valid text, and UTF-8 cannot encode one.  A printout writes it as
    # JSON's escape for that code point, to a file and to standard output
    # alike, so the build succeeds and the path reads back as it was.
    odd = "fonts" + chr(0xDCFF)
    try:
        (tmp_path / odd).mkdir()
    except (OSError, UnicodeEncodeError):
        pytest.skip("this file system takes only names that are text")
    face = (FONTS / "Go-Regular.ttf").read_bytes()
    (tmp_path / odd / "Go.ttf").write_bytes(face)
    template = tmp_path / odd / "odd.kdl"
    template.write_text(
        """
report name="odd" {
  font "body" file="Go.ttf" size=9
  layout pagesize="A4" {
    style font="body" color="black"
    header { field text="h" left=0 top=0 width=50 height=12 }
    detail height=20 { field text="x" left=0 top=0 width=50 height=12 }
  }
}
""",
        encoding="utf-8",
    )
    # From the printout's own directory, so that the two routes
    # write the path relative to the same place.
    monkeypatch.chdir(tmp_path)
    argv = ("build", "-t", str(template), *REPRODUCIBLE)
    code, _ = run(*argv, "-o", "odd.srp.jsonl")
    assert code == 0
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    code = main([*argv, "-o", "-", "--format", "jsonl"], stream)
    stream.flush()
    assert code == 0
    written = (tmp_path / "odd.srp.jsonl").read_bytes()
    assert raw.getvalue() == written
    header = json.loads(written.decode("utf-8").splitlines()[0])
    assert header["fonts"][0]["resolvedFile"] == f"{odd}/Go.ttf"


# -- records on standard input ----------------------------------------
#
# doc/cli.md: records on standard input are UTF-8, as they are in a file.
# On Windows a pipe arrives decoded with the ANSI code page, and the
# stand-in below decodes the way a cp1252 machine would.


def built_from_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: bytes
) -> list[str]:
    """Build the minimal example from standard input and return its dump.

    Args:
        monkeypatch: To put the records on standard input.
        tmp_path: Where to write the printout.
        records: What standard input holds.

    """
    stdin = io.TextIOWrapper(io.BytesIO(records), encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", stdin)
    out = tmp_path / "out.srp.jsonl"
    argv = ("build", "-t", str(MINIMAL), "-d", "-", "-o", str(out))
    code, _ = run(*argv, *REPRODUCIBLE)
    assert code == 0
    code, said = run("inspect", str(out))
    assert code == 0
    return [one.strip() for one in said.splitlines()]


def test_records_on_standard_input_are_read_as_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    records = f'{{"title":"{LATVIAN}"}}\n'.encode()
    assert f'"{LATVIAN}"' in built_from_stdin(monkeypatch, tmp_path, records)


def test_byte_order_marks_on_standard_input_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # What Windows PowerShell 5.1 pipes in when the console's input
    # encoding and `$OutputEncoding` are both UTF-8 with a mark:
    # two marks, then the records, with its own line endings.
    record = f'{{"title":"{LATVIAN}"}}\r\n'.encode()
    records = b"\xef\xbb\xbf" * 2 + record
    assert f'"{LATVIAN}"' in built_from_stdin(monkeypatch, tmp_path, records)


def test_standard_input_already_read_from_is_left_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A caller running main in-process may have read part of standard
    # input, after which it cannot be reconfigured.  A command that
    # does not read it should not fail over it at its first line.
    stdin = io.TextIOWrapper(io.BytesIO(b"one\ntwo\n"), encoding="cp1252")
    stdin.readline()
    monkeypatch.setattr(sys, "stdin", stdin)
    assert run("version") == (0, meta.engine() + "\n")
    assert stdin.encoding == "cp1252"


def test_a_byte_on_standard_input_that_is_not_utf8_reads_as_in_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    records = b'{"title":"Caf\xe9"}\n'
    assert '"Caf\ufffd"' in built_from_stdin(monkeypatch, tmp_path, records)


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


# -- the shell, as doc/cli.md#conventions spells it -------------------
#
# tests/differential/command_lines.py holds the same questions to the
# reference.  These hold the answers this engine gives, so that one
# that changes is caught where there is no reference to compare with.


def test_the_version_flag_prints_what_the_command_does() -> None:
    assert run("--version") == run("version")


@pytest.mark.parametrize("spelling", ["-h", "--help"])
def test_every_spelling_of_help_is_the_command_s_help(spelling: str) -> None:
    assert run("build", spelling) == run("help", "build")


@pytest.mark.parametrize("spelling", ["-h", "--help"])
def test_help_in_place_of_a_command_is_the_help_command(spelling: str) -> None:
    assert run(spelling) == run("help")


@pytest.mark.parametrize(
    ("argv", "spelled"),
    [
        (("validate", "--t", str(SAKILA)), "-t"),
        (("validate", "-template", str(SAKILA)), "--template"),
        (("validate", "--t=" + str(SAKILA)), "-t="),
        (("validate", str(SAKILA), "--q"), "-q"),
        (("validate", str(SAKILA), "-quiet"), "--quiet"),
        (("validate", str(SAKILA), "-strict-fonts"), "--strict-fonts"),
        (("build", "--h"), "-h"),
        (("build", "-help"), "--help"),
        (("--h",), "-h"),
        (("-help",), "--help"),
        (("-version",), "--version"),
    ],
)
def test_a_letter_takes_one_dash_and_a_long_name_two(
    capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], spelled: str
) -> None:
    assert run(*argv) == (2, "")
    assert f"as {spelled}" in capsys.readouterr().err


def test_a_letter_does_not_run_into_its_value() -> None:
    assert run("validate", "-t" + str(SAKILA)) == (2, "")


@pytest.mark.parametrize(
    "argv",
    [
        ("help", "frobnicate"),
        ("help", "build", "extra"),
        ("-t", "x.kdl"),
    ],
)
def test_help_takes_one_command_it_knows(argv: tuple[str, ...]) -> None:
    code, said = run(*argv)
    assert (code, said) == (2, "")


def test_two_dashes_alone_end_the_flags() -> None:
    # After them `-q` is the template, which is not there.
    assert run("validate", "-q", "--", str(SAKILA))[0] == 0
    assert run("validate", "--", "-q")[0] == 1


@pytest.mark.parametrize("flag", ["---t", "----template"])
def test_three_dashes_are_not_a_flag(tmp_path: Path, flag: str) -> None:
    out = tmp_path / "out.srp.jsonl"
    code, _ = run("build", flag, str(MINIMAL), "-o", str(out))
    assert code == 2


@pytest.mark.parametrize(
    ("given", "strict"),
    [
        ((), False),
        (("--strict-fonts",), True),
        (("--strict-fonts=true",), True),
        (("--strict-fonts=false",), False),
        (("--strict-fonts=false", "--strict-fonts"), True),
        (("--strict-fonts", "--strict-fonts=false"), False),
    ],
)
def test_a_switch_takes_true_or_false(
    tmp_path: Path, given: tuple[str, ...], strict: bool
) -> None:
    out = tmp_path / "out.srp.jsonl"
    code, _ = run("build", "-t", str(MINIMAL), "-o", str(out), *given)
    assert code == 0
    header = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert header["strictFonts"] is strict


@pytest.mark.parametrize("value", ["1", "0", "T", "TRUE", "yes", ""])
def test_a_switch_takes_no_other_value(tmp_path: Path, value: str) -> None:
    out = tmp_path / "out.srp.jsonl"
    code, _ = run(
        "build", "-t", str(MINIMAL), "-o", str(out), f"--strict-fonts={value}"
    )
    assert code == 2


def test_an_empty_value_is_the_flag_not_given(tmp_path: Path) -> None:
    out = tmp_path / "out.srp.jsonl"
    assert run("build", "-t", str(MINIMAL), "-d", "", "-o", str(out))[0] == 0
    assert run("build", "-t", "", "-o", str(out))[0] == 2
    assert run("build", "-t", str(MINIMAL), "-o", "")[0] == 2


def test_any_other_flag_given_twice_takes_the_last_value(tmp_path: Path) -> None:
    first, last = tmp_path / "first.srp.jsonl", tmp_path / "last.srp.jsonl"
    code, _ = run("build", "-t", str(MINIMAL), "-o", str(first), "-o", str(last))
    assert code == 0
    assert last.is_file()
    assert not first.exists()


@pytest.mark.parametrize("value", ["=1", "", "period_start"])
def test_a_parameter_needs_a_name_and_an_equals_sign(value: str) -> None:
    code, _ = run("validate", str(SAKILA), "--param", value, "-q")
    assert code == 2


def test_accept_needs_a_name() -> None:
    assert run("validate", str(UNKNOWN), "--accept", "")[0] == 2


@pytest.mark.parametrize(
    "flag",
    [("--param", "x=1"), ("--accept", "x"), ("--strict-fonts",)],
)
def test_render_and_inspect_take_only_their_own_flags(
    tmp_path: Path, flag: tuple[str, ...]
) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    pdf = str(tmp_path / "out.pdf")
    assert run("render", str(printout), "-o", pdf, *flag)[0] == 2
    assert run("inspect", str(printout), *flag)[0] == 2


@pytest.mark.parametrize("command", ["render", "inspect"])
def test_a_printout_is_not_read_from_standard_input(
    tmp_path: Path, command: str
) -> None:
    output = ("-o", str(tmp_path / "out.pdf")) if command == "render" else ()
    assert run(command, "-", *output)[0] == 2


@pytest.mark.parametrize(
    ("name", "written"),
    [("ndjson", "jsonl"), ("JSONL", "jsonl"), ("Cbor", "cbor"), ("PDF", "pdf")],
)
def test_a_format_is_named_as_its_extension_is(
    tmp_path: Path, name: str, written: str
) -> None:
    out = tmp_path / "out.x"
    code, _ = run("build", "-t", str(MINIMAL), "-o", str(out), "--format", name)
    assert code == 0
    data = out.read_bytes()
    starts = {"jsonl": b'{"sr":1', "cbor": bytes.fromhex("aa6273"), "pdf": b"%PDF"}
    assert data.startswith(starts[written])


def test_the_summary_line_counts_pages_and_fonts_and_names_the_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doc/cli.md#sr-build: fonts always, warnings where there are any."""
    code, _ = run(
        "build",
        "-t",
        str(SAKILA),
        "-d",
        str(SAKILA_DATA),
        "-o",
        "-",
        "--format",
        "jsonl",
    )
    assert code == 0
    assert (
        capsys.readouterr().err.splitlines()[-1] == "standard output: 1 page, 4 fonts"
    )


def test_a_document_of_no_fonts_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A font declared and nothing printed in it, as in
    # tests/differential/probes/printout/no-fonts.kdl.
    template = tmp_path / "empty.kdl"
    template.write_text(
        f"""
report name="empty" {{
  font "body" file="{FACE}" size=9
  layout pagesize="A4" {{
    style font="body" color="black"
    detail height=10 {{
      field text="a" left=0 width=100 printwhen="False"
    }}
  }}
}}
""",
        encoding="utf-8",
    )
    out = tmp_path / "out.srp.jsonl"
    assert run("build", "-t", str(template), "-o", str(out))[0] == 0
    assert capsys.readouterr().err.splitlines()[-1] == f"{out}: 1 page, 0 fonts"


def test_a_render_counts_its_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    printout = built(tmp_path, "out.srp.jsonl")
    capsys.readouterr()
    pdf = tmp_path / "out.pdf"
    assert run("render", str(printout), "-o", str(pdf))[0] == 0
    assert capsys.readouterr().err.splitlines() == [f"{pdf}: 1 page"]


@pytest.mark.parametrize(
    "pages",
    ["first", "0", "3-1", "1,,2", "1,", "1-2-3", "+1", "1_0", chr(0x661)],
)
def test_pages_that_are_not_ranges_are_refused_before_the_file_is_read(
    pages: str,
) -> None:
    code, _ = run("inspect", str(ROOT / "nosuch.srp.jsonl"), "--pages", pages)
    assert code == 2


@pytest.mark.parametrize(
    ("pages", "shown"),
    [
        ("", [1]),
        ("-", [1]),
        (" 1 ", [1]),
        ("2-", []),
        ("-9", [1]),
        ("1,1", [1]),
    ],
)
def test_pages_that_are_ranges_choose_pages(
    tmp_path: Path, pages: str, shown: list[int]
) -> None:
    _, out = build_minimal(tmp_path)
    code, said = run("inspect", str(out), "--pages", pages)
    assert code == 0
    headings = [one for one in said.splitlines() if one.startswith("page ")]
    assert [int(one.split()[1]) for one in headings] == shown


def test_what_a_command_writes_ends_its_lines_with_a_line_feed() -> None:
    """doc/cli.md#streams: the bytes do not change with the machine.

    The stand-in translates a line feed as a Windows pipe does.

    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
    code = main(["validate", str(SAKILA)], stream)
    stream.flush()
    assert code == 0
    assert b"\r" not in raw.getvalue()
    assert raw.getvalue().endswith(b"\nok\n")
