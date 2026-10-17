"""A whole document: its structure, its outline, its links, its metadata.

The examples are built and rendered here, and each file is held to the
structure doc/render.md#file-structure gives it: by :mod:`tests.pdfcheck`,
by qpdf where it is installed, and by pdfium, which opens each one as a
reader would.  Rendering is a pure function of the printout, so the same
printout renders to the same bytes, and so does the same printout read
back from either encoding, which is doc/render.md#reproducibility.

The outline and the links are read back through pdfium as well,
since a mistake in a count or a destination is invisible on the page.

"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sr.api import Options, build
from sr.errors import RenderError
from sr.pdf import render
from sr.pdf.document import pdf_date
from sr.printout.model import Box, Outline, Page, Paper, Printout, Report, Xref
from sr.printout.read import read
from sr.printout.write import write_cbor, write_jsonl
from tests.pdfcheck import Parsed, check, entry, references
from tests.unit.documents import PAPER, document, rendered, text

pdfium = pytest.importorskip("pypdfium2")

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = {
    "minimal": ("example/minimal/minimal.kdl", "example/minimal/films.jsonl"),
    "sakila": ("example/sakila/sakila.kdl", "example/sakila/payments.jsonl"),
    "invoices": (
        "example/invoices/invoices.kdl",
        "example/invoices/invoices.jsonl",
    ),
}


def example(name: str) -> Printout:
    """Return one of the examples, built as the differential harness builds it.

    Args:
        name: Its key in :data:`EXAMPLES`.

    """
    template, data = EXAMPLES[name]
    options = Options(build_time="2026-08-04T09:12:44Z", strict_fonts=True)
    return build(ROOT / template, ROOT / data, options).printout


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_every_example_renders_to_a_well_formed_file(
    name: str,
    tmp_path: Path,
) -> None:
    printout = example(name)
    data = render(printout)
    check(data, tmp_path / f"{name}.pdf")
    opened = pdfium.PdfDocument(data)
    assert len(opened) == len(printout.pages)


def test_rendering_twice_gives_the_same_bytes() -> None:
    printout = example("sakila")
    assert render(printout) == render(printout)


@pytest.mark.parametrize("suffix", [".srp.jsonl", ".srp.cbor"])
def test_a_printout_read_back_renders_as_the_one_built(
    suffix: str,
    tmp_path: Path,
) -> None:
    """Invoices: subreports, pages of their own size, a file image."""
    printout = example("invoices")
    path = tmp_path / "archive" / f"invoices{suffix}"
    path.parent.mkdir()
    if suffix == ".srp.cbor":
        with path.open("wb") as handle:
            write_cbor(printout, handle, path.parent)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            write_jsonl(printout, handle, path.parent)
    assert render(read(path)) == render(printout)


def test_uncompressed_deflates_nothing_but_a_jpeg_keeps_its_filter() -> None:
    data = render(example("sakila"), compress=False)
    parsed = check(data)
    assert b"/FlateDecode" not in data
    jpegs = [one for one in parsed.objects.values() if b"/DCTDecode" in one]
    assert len(jpegs) == 1


def test_the_information_comes_from_the_header() -> None:
    report = Report(
        name="Šķūnis",
        description="Sub",
        version="2",
        author="als",
    )
    data, _ = rendered(document(report=report))
    assert pdfium.PdfDocument(data).get_metadata_dict() == {
        "Title": "Šķūnis",
        "Author": "als",
        "Subject": "Sub",
        "Keywords": "",
        "Creator": "sr 0.1.0",
        "Producer": "sr 0.1.0",
        "CreationDate": "D:20260804091244Z",
        "ModDate": "D:20260804091244Z",
    }


def test_fields_the_report_left_out_are_left_out() -> None:
    _, parsed = rendered(document(report=Report()))
    (info,) = references(entry(parsed.trailer, "Info"))
    body = parsed.objects[info]
    assert b"/Title" not in body and b"/Author" not in body
    assert entry(body, "Creator") == b"(sr 0.1.0)"


@pytest.mark.parametrize(
    ("built", "date"),
    [
        ("2026-08-04T09:12:44Z", "D:20260804091244Z"),
        ("2026-08-04t09:12:44z", "D:20260804091244Z"),
        ("2026-08-04T09:12:44.25Z", "D:20260804091244Z"),
        ("2026-08-04T12:12:44+03:00", "D:20260804121244+03'00'"),
        ("2026-08-04T04:42:44-04:30", "D:20260804044244-04'30'"),
    ],
)
def test_the_build_time_is_the_document_s_date(built: str, date: str) -> None:
    assert pdf_date(built) == date


def test_a_build_time_that_is_not_rfc_3339_is_refused() -> None:
    with pytest.raises(RenderError, match="not an RFC 3339 time"):
        render(document(built="yesterday"))


def test_a_page_runs_at_its_own_size_where_it_states_one() -> None:
    landscape = Paper(841.89, 595.276, 0, 0, 0, 0)
    printout = document(
        pages=(Page(1, ()), Page(2, (), landscape), Page(3, ())),
    )
    data, parsed = rendered(printout)
    opened = pdfium.PdfDocument(data)
    assert [opened[index].get_size() for index in range(3)] == [
        pytest.approx((595.276, 841.89), abs=0.001),
        pytest.approx((841.89, 595.276), abs=0.001),
        pytest.approx((595.276, 841.89), abs=0.001),
    ]
    pages = [parsed.objects[one] for one in parsed.kind("Page")]
    boxes = [entry(one, "MediaBox") for one in pages]
    assert boxes[1] == b"[0 0 841.89 595.276]"


def test_every_page_shares_one_resource_dictionary() -> None:
    printout = document(pages=(Page(1, (text("a"),)), Page(2, (text("b"),))))
    _, parsed = rendered(printout)
    pages = [parsed.objects[one] for one in parsed.kind("Page")]
    shared = {entry(one, "Resources") for one in pages}
    assert len(shared) == 1


def test_text_reads_back_as_the_printout_wrote_it() -> None:
    """Runs of spaces are left out: pdfium collapses those as it extracts."""
    lines = ("Šķūnis, 中 & 1/2", "(x) \\ y", "tab\there")
    marks = (text(line, y=20 * at) for at, line in enumerate(lines))
    printout = document(*marks)
    data, _ = rendered(printout)
    page = pdfium.PdfDocument(data)[0]
    said = page.get_textpage().get_text_range()
    assert said.replace("\r\n", "\n").split("\n") == list(lines)


# -- the outline ------------------------------------------------------


def outline(title: str, level: int, y: float = 0, **kind: object) -> Outline:
    """Return an outline entry at the left edge.

    Args:
        title: What it says.
        level: Its depth.
        y: How far down the page it is.
        **kind: Its ``name`` and ``closed``, where they are given.

    """
    return Outline(
        Box(10, y, 0, 0),
        title=title,
        level=level,
        name=kind.get("name"),  # type: ignore[arg-type]
        closed=bool(kind.get("closed", False)),
    )


def bookmarks(data: bytes) -> list[tuple[int, str, int, int, float, float]]:
    """Return the outline as pdfium reads it: depth, title, count, and where.

    Args:
        data: The file.

    """
    found = []
    for one in pdfium.PdfDocument(data).get_toc():
        destination = one.get_dest()
        _, (_, (left, top, _)) = (
            destination.get_index(),
            destination.get_view(),
        )
        found.append(
            (
                one.level,
                one.get_title(),
                one.get_count(),
                destination.get_index(),
                round(left, 3),
                round(top, 3),
            )
        )
    return found


def test_the_outline_is_a_tree_built_from_the_levels() -> None:
    """Counts are what an expanded entry shows, negated when it is closed."""
    first = (
        outline("One", 1, 100),
        outline("One.a", 2, 200, closed=True),
        outline("One.a.i", 3, 210),
        outline("One.b", 2, 300),
    )
    second = (
        outline("Two", 1, 50, closed=True),
        outline("Two.a", 2, 60),
        outline("Two.a.i", 3, 70),
    )
    printout = document(pages=(Page(1, first), Page(2, second)))
    data, parsed = rendered(printout)
    assert bookmarks(data) == [
        (0, "One", 2, 0, 10, 741.89),
        (1, "One.a", -1, 0, 10, 641.89),
        (2, "One.a.i", 0, 0, 10, 631.89),
        (1, "One.b", 0, 0, 10, 541.89),
        (0, "Two", -2, 1, 10, 791.89),
        (1, "Two.a", 1, 1, 10, 781.89),
        (2, "Two.a.i", 0, 1, 10, 771.89),
    ]
    (root,) = parsed.kind("Outlines")
    assert entry(parsed.objects[root], "Count") == b"4"
    (catalog,) = parsed.kind("Catalog")
    assert entry(parsed.objects[catalog], "PageMode") == b"/UseOutlines"


def test_a_level_that_jumps_is_one_deeper() -> None:
    printout = document(
        outline("One", 1),
        outline("Deep", 3),
        outline("Back", 2),
    )
    data, _ = rendered(printout)
    assert [one[:3] for one in bookmarks(data)] == [
        (0, "One", 2),
        (1, "Deep", 0),
        (1, "Back", 0),
    ]


def test_a_document_without_an_outline_asks_for_no_pane() -> None:
    _, parsed = rendered(document(text("a")))
    (catalog,) = parsed.kind("Catalog")
    assert b"/Outlines" not in parsed.objects[catalog]
    assert b"/PageMode" not in parsed.objects[catalog]


# -- links ------------------------------------------------------------


def link(target: str, kind: str = "url", caption: str | None = None) -> Xref:
    """Return an xref over a box, holding a text mark.

    Args:
        target: Where it goes.
        kind: ``url`` or ``outline``.
        caption: Its hover text.

    """
    return Xref(
        Box(100, 200, 50, 12),
        link=kind,
        target=target,
        caption=caption,
        marks=(text("go", y=200),),
    )


def links_of(parsed: Parsed) -> list[bytes]:
    """Return every link annotation, in object order.

    Args:
        parsed: The file's objects.

    """
    return [parsed.objects[one] for one in parsed.kind("Annot")]


def test_a_url_xref_is_a_link_with_no_border() -> None:
    xref = link("https://example.com/a b/é", caption="Ex")
    _, parsed = rendered(document(xref))
    (found,) = links_of(parsed)
    assert entry(found, "Subtype") == b"/Link"
    assert entry(found, "Rect") == b"[100 629.89 150 641.89]"
    assert entry(found, "Border") == b"[0 0 0]"
    assert b"/S /URI /URI (https://example.com/a%20b/%C3%A9)" in found
    assert entry(found, "Contents") == b"(Ex)"
    (page,) = parsed.kind("Page")
    listed = entry(parsed.objects[page], "Annots")
    assert references(listed) == parsed.kind("Annot")


def test_an_outline_xref_goes_where_the_first_entry_of_its_name_goes() -> None:
    printout = document(
        pages=(
            Page(1, (link("cust", "outline"),)),
            Page(2, (outline("A", 1, 100, name="cust"),)),
            Page(3, (outline("B", 1, 300, name="cust"),)),
        )
    )
    data, parsed = rendered(printout)
    (found,) = links_of(parsed)
    pages = parsed.kind("Page")
    goes = f"[{pages[1]} 0 R /XYZ 10 741.89 null]"
    assert entry(found, "Dest") == goes.encode()
    assert b"/Contents" not in found
    view = pdfium.PdfDocument(data)
    assert bookmarks(data)[0][3:] == (1, 10, 741.89)
    assert len(view) == 3


def test_an_xref_s_marks_are_drawn_where_the_xref_is() -> None:
    data, _ = rendered(document(link("https://example.com/")))
    page = pdfium.PdfDocument(data)[0]
    assert page.get_textpage().get_text_range() == "go"


def test_an_outline_xref_no_entry_claims_is_refused() -> None:
    printout = document(
        link("nobody", "outline"),
        outline("A", 1, name="other"),
    )
    with pytest.raises(RenderError, match=r"the xref targets .*'nobody'"):
        render(printout)


def test_a_nested_mark_is_named_after_its_xref() -> None:
    inner = Xref(
        Box(0, 0, 10, 10),
        link="url",
        target="x",
        caption=None,
        marks=(text("a", font="nowhere"),),
    )
    with pytest.raises(RenderError, match=re.escape("page 1, mark 1.1:")):
        render(document(inner))


def test_a_printout_with_no_pages_is_refused() -> None:
    with pytest.raises(RenderError, match="no pages"):
        render(document(pages=()))


def test_the_paper_is_the_header_s_where_the_page_says_nothing() -> None:
    data, _ = rendered(document(pages=(Page(1, ()),)))
    size = pdfium.PdfDocument(data)[0].get_size()
    assert size == pytest.approx((PAPER.width, PAPER.height), abs=0.001)
