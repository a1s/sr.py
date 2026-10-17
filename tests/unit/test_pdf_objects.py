"""The file's own syntax: numbers, names, strings, and the table.

doc/render.md#coordinates spells a length with three decimals
at most, no trailing zeros, and no negative zero, and the writer
spells every number in the file that way.  The rest is PDF's own
syntax, where the cases that go wrong are the characters a token
cannot hold as themselves.

"""

from __future__ import annotations

import zlib

import pytest

from sr.pdf.objects import (
    Hex,
    Name,
    Ref,
    Stream,
    Text,
    Writer,
    literal,
    name,
    number,
    serialize,
    text_string,
)
from tests.pdfcheck import check, stream_of


@pytest.mark.parametrize(
    ("value", "spelled"),
    [
        (0, "0"),
        (72, "72"),
        (72.0, "72"),
        (-0.0, "0"),
        (-0.0004, "0"),
        (0.0005, "0.001"),
        (-0.0005, "-0.001"),
        (595.276, "595.276"),
        (1.10, "1.1"),
        (2 / 3, "0.667"),
        (1e-7, "0"),
        (123456789.25, "123456789.25"),
    ],
)
def test_a_number_is_spelled_as_a_length_is(
    value: float,
    spelled: str,
) -> None:
    assert number(value) == spelled


def test_a_boolean_is_not_a_number() -> None:
    with pytest.raises(TypeError):
        number(True)


@pytest.mark.parametrize(
    ("text", "spelled"),
    [
        ("Type", b"/Type"),
        ("Identity-H", b"/Identity-H"),
        ("A B", b"/A#20B"),
        ("a/b", b"/a#2Fb"),
        ("50%", b"/50#25"),
        ("#1", b"/#231"),
        ("é", b"/#C3#A9"),
    ],
)
def test_a_name_escapes_what_it_cannot_hold(text: str, spelled: bytes) -> None:
    assert name(text) == spelled


def test_a_literal_string_escapes_its_delimiters_and_its_controls() -> None:
    assert literal(b"a(b)c\\d") == b"(a\\(b\\)c\\\\d)"
    assert literal(b"\r\n\x00\xff") == b"(\\015\\012\\000\\377)"


def test_a_text_string_is_ascii_where_it_can_be() -> None:
    assert text_string("Smith, John") == b"(Smith, John)"


def test_a_text_string_is_utf_16_where_it_cannot_be() -> None:
    assert text_string("Šķūnis") == b"<FEFF01600137016B006E00690073>"
    assert text_string("\U0001f600") == b"<FEFFD83DDE00>"
    assert text_string("a\tb") == b"<FEFF006100090062>"


def test_a_lone_surrogate_is_written_rather_than_refused() -> None:
    assert text_string("\udcff") == b"<FEFFDCFF>"


def test_values_are_spelled_as_pdf_spells_them() -> None:
    value = {
        "Type": Name("Page"),
        "Kids": [Ref(3), Ref(4)],
        "Box": [0, 0, 595.276, 841.89],
        "Title": Text("T"),
        "Raw": b"x",
        "ID": Hex(b"\x01\xab"),
        "On": True,
        "Off": False,
        "None": None,
    }
    assert serialize(value) == (
        b"<< /Type /Page /Kids [3 0 R 4 0 R] /Box [0 0 595.276 841.89] "
        b"/Title (T) /Raw (x) /ID <01AB> /On true /Off false /None null >>"
    )


def test_something_else_is_refused() -> None:
    with pytest.raises(TypeError):
        serialize({1, 2})


def test_objects_are_numbered_as_they_are_reserved() -> None:
    writer = Writer()
    first = writer.reserve()
    second = writer.add({"Type": Name("Second")})
    writer.put(first, {"Type": Name("First"), "Next": second})
    data = writer.finish(first, second)
    parsed = check(data)
    assert parsed.objects[1] == b"<< /Type /First /Next 2 0 R >>"
    assert parsed.objects[2] == b"<< /Type /Second >>"


def test_an_object_reserved_and_never_put_is_a_fault() -> None:
    writer = Writer()
    root = writer.add({})
    writer.reserve()
    with pytest.raises(ValueError, match="object 2"):
        writer.finish(root, root)


def test_a_stream_is_deflated_unless_asked_not_to_be() -> None:
    data = b"0 0 m 10 10 l S\n" * 20
    for compress in (True, False):
        writer = Writer(compress)
        ref = writer.add(Stream({}, data))
        parsed = check(writer.finish(ref, ref))
        body = parsed.objects[1]
        assert (b"/FlateDecode" in body) is compress
        assert stream_of(body) == data


def test_bytes_in_a_format_of_their_own_are_written_as_they_are() -> None:
    writer = Writer()
    jpeg = b"\xff\xd8 not really \xff\xd9"
    entries = {"Filter": Name("DCTDecode")}
    ref = writer.add(Stream(entries, jpeg, compress=False))
    body = check(writer.finish(ref, ref)).objects[1]
    assert b"/FlateDecode" not in body
    assert stream_of(body) == jpeg


def test_the_trailer_identifies_the_file_by_its_body() -> None:
    def made(title: str) -> bytes:
        writer = Writer()
        ref = writer.add({"Title": Text(title)})
        return writer.finish(ref, ref)

    one, again, other = made("one"), made("one"), made("other")
    assert one == again
    trailer = check(one).trailer
    assert b"/ID [<" in trailer
    assert check(other).trailer != trailer


def test_the_level_is_the_one_the_writer_names() -> None:
    """A change of library default does not change the file."""
    data = b"q 1 0 0 1 0 0 cm Q\n" * 50
    writer = Writer()
    ref = writer.add(Stream({}, data))
    body = check(writer.finish(ref, ref)).objects[1]
    assert body.split(b"\nstream\n", 1)[1].startswith(zlib.compress(data, 6))
