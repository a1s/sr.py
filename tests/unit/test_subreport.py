"""Subreports: nested builders, their names and values, and their pages.

The differential suite holds the layout to the reference over
the `subreport/` probes.  What is here is what a comparison cannot
reach: the errors, which stop a build, the nesting bound, the tables
the templates share, and the rules where this engine and the reference
part company, each tested for this engine's answer.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sr.api import Options, build
from sr.data import coerce
from sr.errors import BadValue, BuildError, NodePath
from sr.expr import Decimal, FrozenList, Record, Time
from sr.expr.builtins import parse_time
from sr.fonts.resolve import Resolver
from sr.layout.blobs import DataTable
from sr.layout.fonttable import FontTable
from sr.layout.frame import Frame
from sr.printout.model import Page, Printout, Text
from sr.settle import is_of_type
from sr.template.model import Font, Member

ROOT = Path(__file__).resolve().parents[2]
REGULAR = (ROOT / "example" / "fonts" / "Go-Regular.ttf").as_posix()
BOLD = (ROOT / "example" / "fonts" / "Go-Bold.ttf").as_posix()

# A page 200 by 60 with 5pt margins, a frame 50pt tall.  Size 5 sets
# a line 6pt tall, and every band here is a line, so the arithmetic is whole.
HEAD = """
report name="Subreports" {
  font "body" file="FACE" size=5
MEMBERS
  layout width=200 height=60 leftmargin=5 rightmargin=5 topmargin=5 bottommargin=5 {
    style font="body" color="black"
BANDS
  }
}
"""


def built(
    tmp_path: Path,
    bands: str,
    rows: list[dict[str, Any]] | None = None,
    *,
    members: str = "",
    files: dict[str, str] | None = None,
) -> Printout:
    """Build a report and return its printout.

    Args:
        tmp_path: Where to write the templates and the data.
        bands: What goes under `layout`, after its style.
        rows: The records, or ``None`` for one record nothing reads.
        members: What goes between the font and the layout.
        files: Other templates to write beside it, by file name.

    """
    for name, text in (files or {}).items():
        (tmp_path / name).write_bytes(text.replace("FACE", REGULAR).encode("utf-8"))
    template = tmp_path / "report.kdl"
    text = HEAD.replace("FACE", REGULAR).replace("MEMBERS", members)
    template.write_bytes(text.replace("BANDS", bands).encode("utf-8"))
    data = tmp_path / "rows.jsonl"
    lines = [json.dumps(row) for row in ([{"n": 1}] if rows is None else rows)]
    data.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    asked = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(template, data, asked).printout


def member(name: str, kind: str) -> Member:
    """Return a declared member that is not nullable."""
    return Member(name, kind, False, None, NodePath())


def texts(page: Page) -> list[str]:
    """Return the first line of every text mark on a page, in paint order."""
    return [mark.lines[0] for mark in page.marks if isinstance(mark, Text)]


# An embedded layout of one detail band showing `n`, with the records it needs.
NOTES = """
    embedded "notes" {
      records {
        member "n" type="int"
      }
      detail height=6 {
        field expr="'note %d' % n" left=0 top=0 height=6
      }
    }
"""


def host(subreport: str, extra: str = NOTES) -> str:
    """Return the bands of a report whose detail runs a subreport.

    Args:
        subreport: The `subreport` node.
        extra: What to declare beside the detail band.

    """
    return (
        extra
        + """
    detail height=6 {
      field text="host" left=0 top=0 height=6
      SUBREPORT
    }
""".replace("SUBREPORT", subreport)
    )


# -- what a subreport runs over -----------------------------------------


@pytest.mark.parametrize(
    ("data", "said"),
    [
        ("5", "want a sequence of records, got int"),
        ("'abc'", "want a sequence of records, got string"),
        ("BUILD_TIME", "want a sequence of records, got time.time"),
        ("[1, 2]", "element 0 is int, and a record is an object"),
        ("[{'n': 1}, None]", "element 1 is NoneType, and a record is an object"),
        ("{'n': 1}", "element 0 is string, and a record is an object"),
        ("[{'n': 'x'}]", "element 0: member 'n': not an integer"),
    ],
)
def test_data_that_is_not_a_sequence_of_records_is_an_error(
    tmp_path: Path, data: str, said: str
) -> None:
    """doc/layout.md#names-and-values: it must yield a sequence of records."""
    bands = host(f'subreport embedded="notes" seq=1 data="{data}" inline=#true')
    with pytest.raises(BuildError, match=said) as raised:
        built(tmp_path, bands)
    assert "subreport data=" in str(raised.value)


def test_none_is_an_empty_sequence(tmp_path: Path) -> None:
    """The child still runs, so its title and summary print."""
    notes = NOTES.replace(
        "      detail height=6",
        "      title height=6 { field expr=\"'title %d' % DATA_COUNT\" left=0"
        " top=0 height=6 }\n      detail height=6",
    )
    printout = built(
        tmp_path,
        host('subreport embedded="notes" seq=1 data="None" inline=#true', notes),
    )
    assert texts(printout.pages[0]) == ["host", "title 0"]


def test_the_elements_are_coerced_by_the_childs_records(tmp_path: Path) -> None:
    """A string from JSON and a decimal from an expression are both ints there."""
    printout = built(
        tmp_path,
        host(
            'subreport embedded="notes" seq=1 inline=#true'
            " data=\"[{'n': '2'}, {'n': decimal('3')}, THIS]\""
        ),
    )
    assert texts(printout.pages[0]) == ["host", "note 2", "note 3", "note 1"]


def test_a_typed_value_is_written_as_text_and_read_by_type() -> None:
    """The reading a JSON scalar gets, for a value an expression produced."""
    moment = parse_time("2026-08-04T09:12:44Z")
    assert coerce(Decimal("2"), member("i", "int")) == 2
    assert coerce(Decimal("1.10"), member("s", "string")) == "1.10"
    assert coerce(moment, member("s", "string")) == "2026-08-04T09:12:44Z"
    assert coerce(2, member("d", "decimal")) == Decimal("2")
    with pytest.raises(BadValue):
        coerce(Decimal("2.5"), member("i", "int"))


def test_a_time_a_record_and_a_list_are_kept() -> None:
    """A `date` takes a time at the start of its day."""
    moment = parse_time("2026-08-04T09:12:44Z")
    assert coerce(moment, member("t", "datetime")) is moment
    day = coerce(moment, member("d", "date"))
    assert isinstance(day, Time)
    assert day.format("2006-01-02T15:04:05Z07:00") == "2026-08-04T00:00:00Z"
    record = Record({"a": 1})
    assert coerce(record, member("o", "object")) is record
    assert coerce({"a": 1}, member("o", "object")) == record
    assert coerce((1, 2), member("l", "list")) == FrozenList([1, 2])
    with pytest.raises(BadValue, match="want an array, got string"):
        coerce("ab", member("l", "list"))


# -- its parameters -----------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "named"),
    [
        ("int", "decimal('2')", "decimal"),
        ("int", "True", "bool"),
        ("decimal", "2", "int"),
        ("float", "2", "int"),
        ("string", "None", "NoneType"),
        ("object", "{'a': 1}", "dict"),
        ("list", "(1, 2)", "tuple"),
        ("datetime", "'2026-01-01T00:00:00Z'", "string"),
    ],
)
def test_an_arg_must_already_be_of_the_parameters_type(
    tmp_path: Path, kind: str, value: str, named: str
) -> None:
    """doc/template.md#arg: checked against the type, not parsed for it."""
    child = f"""
    embedded "child" {{
      parameter "p" type="{kind}"
      detail height=6 {{ field text="child" left=0 top=0 height=6 }}
    }}
"""
    bands = host(
        f'subreport embedded="child" seq=1 data="[{{}}]" inline=#true'
        f' {{ arg "p" value="{value}" }}',
        child,
    )
    with pytest.raises(BuildError, match=f"the value is {named}, and 'p'") as raised:
        built(tmp_path, bands)
    assert 'arg "p" value=' in str(raised.value)


def test_a_value_of_the_type_is_taken() -> None:
    """A `date` takes any time, and a `list` a frozen list as well as a list."""
    moment = parse_time("2026-08-04T09:12:44Z")
    assert is_of_type("date", moment)
    assert is_of_type("list", FrozenList([1]))
    assert is_of_type("list", [1])
    assert is_of_type("object", Record({}))
    assert not is_of_type("int", True)
    assert not is_of_type("bool", 1)


def test_a_defaultexpr_must_produce_the_declared_type(tmp_path: Path) -> None:
    """doc/template.md#parameter: the report's and a subreport's alike."""
    with pytest.raises(BuildError, match="defaultexpr produced string"):
        built(
            tmp_path,
            '    detail height=6 { field text="x" left=0 top=0 height=6 }\n',
            members='  parameter "p" type="int" defaultexpr="\'x\'"',
        )
    child = """
    embedded "child" {
      parameter "q" type="decimal" defaultexpr="1"
      detail height=6 { field text="child" left=0 top=0 height=6 }
    }
"""
    bands = host('subreport embedded="child" seq=1 data="[{}]" inline=#true', child)
    with pytest.raises(BuildError, match="defaultexpr produced int") as raised:
        built(tmp_path, bands)
    assert "subreport" in str(raised.value)


# -- how deep ---------------------------------------------------------------

# A layout that runs itself, one level deeper each time, while `level`
# is below `limit`.
TREE = """
    embedded "node" {
      parameter "level" type="int"
      parameter "limit" type="int"
      detail height=1 {
        field text="." left=0 top=0 height=1
        subreport embedded="node" seq=1 data="[{}]" when="level < limit" inline=#true {
          arg "level" value="level + 1"
          arg "limit" value="limit"
        }
      }
    }
"""


@pytest.mark.parametrize(("limit", "fails"), [(32, False), (33, True)])
def test_nesting_is_bounded_at_32(tmp_path: Path, limit: int, fails: bool) -> None:
    """doc/layout.md#subreports: the 33rd nested invocation is the error."""
    bands = host(
        'subreport embedded="node" seq=1 data="[{}]" inline=#true'
        f' {{ arg "level" value="1"; arg "limit" value="{limit}"; }}',
        TREE,
    )
    if not fails:
        built(tmp_path, bands)
        return
    with pytest.raises(BuildError, match="nested 32 deep"):
        built(tmp_path, bands)


# -- the pages --------------------------------------------------------------


def test_a_host_band_rolls_back_around_a_childs_eject(tmp_path: Path) -> None:
    """doc/layout.md#rollback: the band counts on the page it lands on.

    The negative-seq child fills the first page, so the host band follows
    it onto the second.  The reference counts the band on the first page,
    whose footer it is not above, and loses it to the second page's reset.

    """
    bands = host(
        'subreport embedded="notes" seq=-1 data="[{\'n\': 1}] * 9" inline=#true'
    ).replace(
        'field text="host"',
        "field expr=\"'host pn %d seen %d' % (PAGE_NUMBER, seen)\"",
    ) + (
        "    footer height=6 { field expr=\"'footer %d' % seen\""
        " left=0 top=0 height=6 }\n"
    )
    printout = built(
        tmp_path,
        bands,
        members='  variable "seen" expr="1" calc="count" reset="page"',
    )
    first, second = printout.pages
    assert texts(first)[-1] == "footer 0"
    assert "host pn 2 seen 1" in texts(second)
    assert texts(second)[-1] == "footer 1"


def test_a_child_with_pages_of_its_own_splices_them(tmp_path: Path) -> None:
    """The host's page ends, the child's follow, the host resumes after them.

    It resumes on a fresh page whether or not anything follows,
    so the last page here is empty.

    """
    bands = host('subreport embedded="notes" seq=1 data="[{\'n\': 1}] * 9"')
    printout = built(tmp_path, bands, [{}, {}])
    numbers = [page.number for page in printout.pages]
    assert numbers == [1, 2, 3, 4, 5, 6, 7]
    assert [texts(page)[:1] for page in printout.pages] == [
        ["host"],
        ["note 1"],
        ["note 1"],
        ["host"],
        ["note 1"],
        ["note 1"],
        [],
    ]


def test_a_template_sets_its_own_page_geometry(tmp_path: Path) -> None:
    """doc/printout.md#page-lines: the child's pages carry an override."""
    sheet = """
report name="Sheet" {
  font "body" file="FACE" size=5
  layout width=60 height=200 leftmargin=0 rightmargin=5 topmargin=5 bottommargin=5 {
    style font="body" color="black"
    detail height=6 { field text="sheet" left=0 top=0 height=6 }
  }
}
"""
    bands = '    detail height=6 { field text="host" left=0 top=0 height=6;'
    bands += ' subreport template="sheet.kdl" seq=1 data="[{}]" }\n'
    printout = built(tmp_path, bands, files={"sheet.kdl": sheet})
    host_page, sheet_page, resumed = printout.pages
    assert host_page.paper is None and resumed.paper is None
    assert sheet_page.paper is not None
    assert (sheet_page.paper.width, sheet_page.paper.left) == (60, 0)


def test_a_templates_fonts_resolve_when_it_first_runs(tmp_path: Path) -> None:
    """One that never runs resolves nothing, and is not refused for it."""
    missing = (tmp_path / "missing.ttf").as_posix()
    sheet = f"""
report name="Sheet" {{
  font "body" file="{missing}" size=5
  layout width=200 height=60 leftmargin=5 rightmargin=5 topmargin=5 bottommargin=5 {{
    style font="body" color="black"
    detail height=6 {{ field text="sheet" left=0 top=0 height=6 }}
  }}
}}
"""
    bands = (
        '    detail height=6 { field text="host" left=0 top=0 height=6;'
        ' subreport template="sheet.kdl" seq=1 data="[{}]" when="ROW"'
        " inline=#true }\n"
    )
    printout = built(
        tmp_path, bands.replace("ROW", "False"), files={"sheet.kdl": sheet}
    )
    assert texts(printout.pages[0]) == ["host"]
    with pytest.raises(BuildError, match="font 'body'"):
        built(tmp_path, bands.replace("ROW", "True"), files={"sheet.kdl": sheet})


def test_groups_of_one_name_are_counted_together(tmp_path: Path) -> None:
    """doc/printout.md#header-line: a host's and a subreport's, every run."""
    child = """
    embedded "child" {
      records {
        member "k" type="string"
      }
      group "g" expr="k" {
        detail height=6 { field expr="k" left=0 top=0 height=6 }
      }
    }
"""
    bands = child + (
        '    group "g" expr="k" {\n'
        '      detail height=6 { field expr="k" left=0 top=0 height=6;'
        ' subreport embedded="child" seq=1 inline=#true data="rows" }\n'
        "    }\n"
    )
    rows = [
        {"k": "x", "rows": [{"k": "x"}, {"k": "y"}]},
        {"k": "b", "rows": [{"k": "x"}]},
    ]
    printout = built(
        tmp_path,
        bands,
        rows,
        members='  records {\n    member "k" type="string"\n  }',
    )
    assert printout.group_runs == {"g": 5}
    assert printout.group_keys == {"g": 3}


# -- the tables -------------------------------------------------------------


def resolution(name: str, face: str, size: int) -> Any:
    """Return what resolving a font of a file at a size produces."""
    font = Font(name, None, face, None, size, False, False, False, NodePath())
    return Resolver(strict=True).resolve(font)


def test_a_font_of_another_template_under_a_taken_name_is_suffixed() -> None:
    """doc/printout.md#fonts: the one used first keeps the name."""
    table = FontTable()
    host, child = object(), object()
    assert table.publish(child, resolution("x", BOLD, 6)) == "x"
    assert table.publish(host, resolution("x", REGULAR, 4)) == "x-2"
    assert table.publish(host, resolution("x", REGULAR, 4)) == "x-2"
    assert [entry.name for entry in table.entries.values()] == ["x", "x-2"]


def test_two_fonts_of_one_name_that_write_one_entry_are_one() -> None:
    """Sharing an entry renames nothing."""
    table = FontTable()
    host, child = object(), object()
    assert table.publish(host, resolution("body", REGULAR, 5)) == "body"
    assert table.publish(child, resolution("body", REGULAR, 5)) == "body"
    assert len(table.entries) == 1


def test_every_documents_declared_data_names_are_reserved() -> None:
    """doc/printout.md#data: a generated name steps around a child's too."""
    table = DataTable({})
    table.reserve(["pic.png"], {"pic.png": b"child"})
    assert table.generated("pic.png", b"host") == "pic.png-2"
    assert table.named("pic.png", b"child") == "pic.png"


def test_two_documents_declaring_one_name_go_to_the_first_built() -> None:
    """The other is suffixed, as a generated name would be."""
    table = DataTable({"shared": b"host"})
    table.reserve(["shared"], {"shared": b"child"})
    assert table.named("shared", b"child") == "shared"
    assert table.named("shared") == "shared-2"


# -- the frames -------------------------------------------------------------


def test_a_grafted_frame_is_walked_after_the_hosts_own() -> None:
    """doc/layout.md#pages: a frame can briefly hold two."""
    page = Frame(top=0.0, bottom=100.0)
    own = Frame(count=2, parent=page, bottom=90.0)
    inner = Frame(count=2, parent=own, bottom=80.0)
    graft = Frame(count=3, parent=page, bottom=95.0, grafted=True)
    page.graft(own)
    own.graft(inner)
    page.graft(graft)
    assert list(page.descendants()) == [own, inner, graft]
    assert page.end == 80.0
    page.prune(graft)
    assert list(page.descendants()) == [own, inner]
