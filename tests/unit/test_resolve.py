"""The four-step chain, driven against a fixture machine and a real one.

Most tests here hand the resolver a :class:`Catalog` built from a fixture
directory, so that what the machine running the suite happens to have
installed changes nothing.  That is what lets the chain be checked step
by step: a family the host has, an alias whose candidate it has, and
a typeface nothing has, all arranged rather than hoped for.

The section at the end resolves against the real host instead, and
asserts only what holds of any machine with fonts.  It is not decoration.
The alias table's later candidates are unreachable on a machine that has
the first one, so a Windows run never leaves Arial, and a run somewhere
with neither Arial nor Helvetica installed is what reaches the rest of
each list.  ``make test-linux`` is one such machine: a Debian container
carrying Liberation and DejaVu.

"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import pytest
from fontTools.ttLib import TTCollection, TTFont

from sr.errors import FontError, NodePath
from sr.fonts.face import Face, Origin, build
from sr.fonts.hostenum import Catalog, Source, enumerate_faces
from sr.fonts.resolve import ALIASES, STEPS, Resolver, aliases_for, regular, tidy
from sr.template.model import Font

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "example" / "fonts"


def font(
    name: str = "body",
    *,
    typeface: str | None = None,
    file: str | None = None,
    data: str | None = None,
    size: int = 10,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> Font:
    """Return a `font` node as the template model holds one.

    Args:
        name: The name ``style font=`` reaches it by.
        typeface: The family to resolve, where resolution is by family.
        file: A path relative to the report's ``basedir``.
        data: The name of a ``data`` node holding a face.
        size: Points.
        bold: The declared weight.
        italic: The declared slant.
        underline: Drawn by the renderer; it does not affect metrics.

    """
    return Font(
        name=name,
        typeface=typeface,
        file=file,
        data=data,
        size=size,
        bold=bold,
        italic=italic,
        underline=underline,
        path=NodePath().child("report").child("font", name),
    )


@pytest.fixture
def machine(font_directory: Path) -> Catalog:
    """Return a host table holding the two Go faces and a collection."""
    return enumerate_faces((Source(directory=font_directory),))


# -- step 1, an explicit file or blob ---------------------------------


def test_a_file_resolves_to_the_face_in_it() -> None:
    found = Resolver(basedir=FONTS).resolve(font(file="Go-Regular.ttf"))
    assert found.step == "explicit"
    assert found.face.family == "Go"
    assert found.requested is None
    assert found.warnings == ()


def test_a_blob_resolves_to_the_face_in_its_bytes() -> None:
    blobs = {"face": (FONTS / "Go-Bold.ttf").read_bytes()}
    found = Resolver(blobs=blobs).resolve(font(data="face", bold=True))
    assert found.step == "explicit"
    assert found.origin.data == "face"
    assert found.origin.path is None


def test_a_file_that_is_not_there_ends_the_chain(machine: Catalog) -> None:
    resolver = Resolver(basedir=FONTS, catalog=machine)
    with pytest.raises(FontError, match="cannot read"):
        resolver.resolve(font(file="absent.ttf"))


def test_a_blob_with_no_bytes_ends_the_chain() -> None:
    with pytest.raises(FontError, match="no content"):
        Resolver().resolve(font(data="face"))


def test_a_declared_style_the_face_has_not_got_is_a_warning() -> None:
    found = Resolver(basedir=FONTS).resolve(font(file="Go-Regular.ttf", bold=True))
    assert len(found.warnings) == 1
    assert found.warnings[0].kind == "font"
    assert 'font "body" declares bold' in found.warnings[0].message


def test_a_style_the_face_has_and_the_node_does_not_is_not_reported() -> None:
    assert Resolver(basedir=FONTS).resolve(font(file="Go-Bold.ttf")).warnings == ()


# -- a collection, chosen by style ------------------------------------


def test_a_collection_gives_the_face_whose_bits_match(collection: Path) -> None:
    resolver = Resolver(basedir=collection.parent)
    assert resolver.resolve(font(file=collection.name)).origin.index == 0
    assert resolver.resolve(font(file=collection.name, bold=True)).origin.index == 1


def test_a_collection_with_no_matching_face_gives_its_first(collection: Path) -> None:
    resolver = Resolver(basedir=collection.parent)
    found = resolver.resolve(font(file=collection.name, italic=True))
    assert found.origin.index == 0
    assert "declares italic" in found.warnings[0].message


def damage(data: bytes, index: int) -> bytes:
    """Return a collection with one of its faces made unreadable.

    A collection header is the tag, the version, the face count,
    and then one four-byte offset per face.  Pointing one of them
    past the end of the file leaves the others exactly as they were,
    which is what makes this a damaged face rather than a damaged file.

    Args:
        data: The collection.
        index: Which face to damage.

    """
    broken = bytearray(data)
    struct.pack_into(">I", broken, 12 + index * 4, len(broken) + 1)
    return bytes(broken)


def test_a_damaged_face_in_a_collection_does_not_fail_the_font(
    tmp_path: Path, collection_bytes: bytes
) -> None:
    # Face 1 is the bold one, so a node declaring bold is a node that
    # reads it.  doc/template.md#font makes face 0 the answer when no
    # face matches, and one unreadable face is not a reason to refuse
    # a file whose other faces are fine.
    path = tmp_path / "Damaged.ttc"
    path.write_bytes(damage(collection_bytes, 1))
    found = Resolver(basedir=tmp_path).resolve(font(file=path.name, bold=True))
    assert found.origin.index == 0
    assert "declares bold" in found.warnings[0].message


def test_the_unstyled_face_of_a_collection_passes_over_a_damaged_one(
    tmp_path: Path,
) -> None:
    # The substitute face is the one whose style bits say neither bold
    # nor slanted, and here that face is second because the first will
    # not parse.  Bold first is not the order the shipped collection
    # has, which is why this one is built rather than borrowed.
    holder = TTCollection()
    holder.fonts = [TTFont(FONTS / "Go-Bold.ttf"), TTFont(FONTS / "Go-Regular.ttf")]
    buffer = io.BytesIO()
    holder.save(buffer)
    path = tmp_path / "BoldFirst.ttc"
    path.write_bytes(damage(buffer.getvalue(), 0))
    assert regular(path.read_bytes(), Origin(path=path)) == 1


def test_every_face_read_here_is_named_by_its_file(
    tmp_path: Path, collection_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refusals raised in here are passed over rather than printed,
    # so an origin with no file in it goes unnoticed until something
    # does print one -- and then it reads `data None`.
    # What the faces are read under is therefore checked directly.
    path = tmp_path / "Pair.ttc"
    path.write_bytes(collection_bytes)
    seen: list[Origin] = []

    def watched(data: bytes, origin: Origin) -> Face:
        seen.append(origin)
        return build(data, origin)

    monkeypatch.setattr("sr.fonts.resolve.build", watched)
    regular(collection_bytes, Origin(path=path))
    assert seen
    assert [one.path for one in seen] == [path] * len(seen)


# -- step 2, the host -------------------------------------------------


def test_a_family_the_host_has_resolves_to_it(machine: Catalog) -> None:
    found = Resolver(catalog=machine).resolve(font(typeface="Go"))
    assert found.step == "host"
    assert found.face.family == "Go"
    assert found.requested == "Go"


def test_the_host_is_matched_without_regard_to_case(machine: Catalog) -> None:
    assert Resolver(catalog=machine).resolve(font(typeface="gO")).step == "host"


def test_the_style_selects_among_the_hosts_faces(machine: Catalog) -> None:
    found = Resolver(catalog=machine).resolve(font(typeface="Go", bold=True))
    assert found.face.bold


def test_the_host_is_not_enumerated_for_a_font_named_by_file() -> None:
    resolver = Resolver(basedir=FONTS)
    resolver.resolve(font(file="Go-Regular.ttf"))
    assert resolver.catalog is None
    assert resolver.diagnostics == ()


def test_the_host_is_enumerated_once_a_typeface_needs_it(
    font_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sr.fonts.resolve.host_sources",
        lambda platform: (Source(directory=font_directory),),
    )
    resolver = Resolver()
    assert resolver.resolve(font(typeface="Go")).step == "host"
    assert resolver.catalog is not None


# -- step 3, the aliases ----------------------------------------------


def test_an_alias_is_tried_after_the_host_and_not_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `Go` is a family this fixture machine has, and for the length
    # of this test it is also an alias pointing at a family it has not got.
    # The host must win, which is why doc/template.md puts the table
    # after the machine: `Helvetica` is a real family on macOS.
    monkeypatch.setitem(ALIASES, "go", ("Nothing At All",))
    catalog = enumerate_faces((Source(directory=FONTS),))
    found = Resolver(catalog=catalog).resolve(font(typeface="Go"))
    assert found.step == "host"
    assert found.face.family == "Go"


def test_an_alias_resolves_when_the_host_has_the_aliased_family(
    machine: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ALIASES, "helvetica", ("Go",))
    found = Resolver(catalog=machine).resolve(font(typeface="Helvetica"))
    assert found.step == "alias"
    assert found.face.family == "Go"
    assert found.requested == "Helvetica"


def test_the_alias_table_is_consulted_without_regard_to_case() -> None:
    assert aliases_for("HELVETICA") == aliases_for("helvetica")
    assert aliases_for("Helvetica")[0] == "Arial"


def test_a_typeface_is_looked_up_whole_and_not_normalised() -> None:
    assert aliases_for("Helvetica ") == ()
    assert aliases_for(" Helvetica") == ()
    assert aliases_for("Helvetica Neue") != aliases_for("Helvetica")
    assert aliases_for("Avant Garde") == ()
    assert aliases_for("Avantgarde") == ("Century Gothic", "URW Gothic L")


def test_an_entry_is_an_ordered_list_of_candidates() -> None:
    assert aliases_for("Helvetica") == ("Arial", "Liberation Sans", "Nimbus Sans")


def test_the_candidates_are_tried_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A machine with the second candidate and not the first
    # takes the second, and one with both takes the first.
    def machine_with(*families: str) -> Catalog:
        directory = tmp_path / "-".join(families).replace(" ", "")
        directory.mkdir()
        face = (FONTS / "Go-Regular.ttf").read_bytes()
        for family in families:
            written = TTFont(io.BytesIO(face))
            for record in written["name"].names:
                if record.nameID in (1, 4, 16):
                    record.string = family
            written.save(directory / f"{family.replace(' ', '')}.ttf")
        return enumerate_faces((Source(directory=directory),))

    second = Resolver(catalog=machine_with("Liberation Sans"))
    assert second.resolve(font(typeface="Helvetica")).face.family == "Liberation Sans"
    both = Resolver(catalog=machine_with("Arial", "Liberation Sans"))
    assert both.resolve(font(typeface="Helvetica")).face.family == "Arial"


def test_the_core_three_name_each_other_in_both_directions() -> None:
    # A template is written on one machine and built on another,
    # so the name its author had is as likely to be the absent one.
    for here, there in (
        ("Arial", "Helvetica"),
        ("Times New Roman", "Times"),
        ("Courier New", "Courier"),
    ):
        assert aliases_for(here)[0] == there
        assert aliases_for(there)[0] == here


def test_a_family_with_no_alias_offers_none() -> None:
    assert aliases_for("Gill Sans") == ()
    assert aliases_for("Arial Narrow") == ()


@pytest.mark.parametrize("wanted", sorted(ALIASES))
def test_every_alias_is_keyed_in_the_case_it_is_looked_up_in(wanted: str) -> None:
    assert wanted == wanted.casefold()


# -- step 4, the substitute -------------------------------------------


def test_a_family_nothing_has_reaches_the_substitute(
    machine: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sr.fonts.resolve.substitute_candidates", lambda platform: ("Go-Regular.ttf",)
    )
    found = Resolver(catalog=machine).resolve(font(typeface="Nothing At All"))
    assert found.step == "substitute"
    assert found.face.family == "Go"


def test_the_substitute_is_warned_about_by_name(
    machine: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sr.fonts.resolve.substitute_candidates", lambda platform: ("Go-Regular.ttf",)
    )
    found = Resolver(catalog=machine).resolve(font(typeface="Nothing At All"))
    said = [one.message for one in found.warnings]
    assert any('typeface "Nothing At All" was not found' in one for one in said)
    assert any('substitute face "Go"' in one for one in said)


def test_a_substitute_that_is_not_monospaced_is_warned_about_once(
    machine: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sr.fonts.resolve.substitute_candidates", lambda platform: ("Go-Regular.ttf",)
    )
    resolver = Resolver(catalog=machine)
    first = resolver.resolve(font("one", typeface="Nothing"))
    again = resolver.resolve(font("two", typeface="Nothing Else"))
    monospace = [one for one in first.warnings if "not monospaced" in one.message]
    assert len(monospace) == 1
    assert not [one for one in again.warnings if "not monospaced" in one.message]


def test_the_chain_running_out_is_an_error(
    machine: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sr.fonts.resolve.substitute_candidates", lambda platform: ("nowhere.ttf",)
    )
    with pytest.raises(FontError, match="no substitute face"):
        Resolver(catalog=machine).resolve(font(typeface="Nothing"))


def test_the_chain_running_out_says_why_each_candidate_was_refused(
    machine: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A candidate that is not installed and one that is installed and
    # will not open are different things to be told, and this message
    # is the only record of either: the substitute step leaves no
    # enumeration diagnostic behind it.
    junk = tmp_path / "notafont.ttf"
    junk.write_bytes(b"this is not a font at all")
    monkeypatch.setattr(
        "sr.fonts.resolve.substitute_candidates",
        lambda platform: (junk.as_posix(), "nowhere.ttf"),
    )
    with pytest.raises(FontError) as refused:
        Resolver(catalog=machine).resolve(font(typeface="Nothing"))
    assert "notafont.ttf" in str(refused.value)
    assert "not an sfnt font" in str(refused.value)
    assert "nowhere.ttf (not found)" in str(refused.value)


# -- strict mode ------------------------------------------------------


def test_strict_mode_stops_after_the_first_step(machine: Catalog) -> None:
    resolver = Resolver(catalog=machine, strict=True)
    with pytest.raises(FontError, match="strict mode"):
        resolver.resolve(font(typeface="Go"))


def test_strict_mode_still_takes_a_font_named_by_file() -> None:
    resolver = Resolver(basedir=FONTS, strict=True)
    assert resolver.resolve(font(file="Go-Regular.ttf")).step == "explicit"


def test_strict_mode_names_the_typeface_it_would_not_look_for(
    machine: Catalog,
) -> None:
    resolver = Resolver(catalog=machine, strict=True)
    with pytest.raises(FontError, match='typeface "Helvetica"'):
        resolver.resolve(font(typeface="Helvetica"))


# -- the shape of what comes out --------------------------------------


def test_every_step_a_resolution_reports_is_one_the_printout_names() -> None:
    catalog = enumerate_faces((Source(directory=FONTS),))
    resolver = Resolver(basedir=FONTS, catalog=catalog)
    for one in (font(file="Go-Regular.ttf"), font(typeface="Go")):
        assert resolver.resolve(one).step in STEPS


def test_a_joined_path_loses_its_dot_dot_segments() -> None:
    assert tidy(Path("example/sakila/../fonts/Go.ttf")) == Path("example/fonts/Go.ttf")


# -- the machine this actually runs on --------------------------------
#
# The chain against the real host rather than a fixture one.  What these
# assert holds of any machine with fonts on it, so that running the suite
# on another operating system exercises the platform code rather than
# skipping past it -- which is the whole reason the per-platform parts
# are worth a second machine.


@pytest.mark.slow
def test_a_family_this_machine_has_resolves_to_itself() -> None:
    resolver = Resolver()
    families = resolver.host().families()
    if not families:
        pytest.skip("no fonts on this machine")
    for family in families[:20]:
        entry = None
        for bold, italic in ((False, False), (True, False), (False, True)):
            entry = resolver.host().lookup(family, bold, italic)
            if entry is not None:
                break
        assert entry is not None
        found = resolver.resolve(
            font(typeface=family, bold=entry.bold, italic=entry.italic)
        )
        assert found.step == "host"
        assert found.face.family == family


@pytest.mark.slow
def test_this_machine_has_a_substitute_and_it_is_monospaced() -> None:
    resolver = Resolver()
    resolver.host()
    try:
        face = resolver.find_substitute()
    except FontError:
        pytest.skip("no substitute face on this machine")
    assert face.uneven_advances() == ()


@pytest.mark.slow
def test_a_family_nothing_has_reaches_the_substitute_on_this_machine() -> None:
    resolver = Resolver()
    try:
        resolver.host()
        resolver.find_substitute()
    except FontError:
        pytest.skip("no substitute face on this machine")
    found = resolver.resolve(font(typeface="No Such Family At All 12345"))
    assert found.step == "substitute"
    assert any("was not found" in one.message for one in found.warnings)


@pytest.mark.slow
def test_an_alias_whose_candidate_this_machine_has_resolves_through_it() -> None:
    # Whichever of the candidates this machine carries, the answer
    # must be one of them and the step must be `alias`.  On Windows that
    # is Arial, the first; under `make test-linux` it is Liberation Sans,
    # the second, because that container installs Debian's
    # `fonts-liberation2` and no Arial.  Reaching past the first
    # candidate is the half of the table no Windows run can exercise.
    resolver = Resolver()
    catalog = resolver.host()
    candidates = aliases_for("Helvetica")
    if catalog.lookup("Helvetica", False, False) is not None:
        pytest.skip("this machine has a real Helvetica, so step 2 answers")
    if not any(catalog.lookup(one, False, False) for one in candidates):
        pytest.skip("this machine has none of Helvetica's candidates")
    found = resolver.resolve(font(typeface="Helvetica"))
    assert found.step == "alias"
    assert found.face.family in candidates


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Courier New is a Windows guarantee",
)
@pytest.mark.slow
def test_a_typeface_every_windows_has_resolves_on_this_machine() -> None:
    found = Resolver().resolve(font(typeface="Courier New"))
    assert found.step == "host"
    assert found.face.family == "Courier New"
    assert not found.face.uneven_advances()


# -- the table against the specification ------------------------------
#
# doc/template.md#the-family-alias-table gives the fifteen entries
# in full.  They were read off the reference engine, and the method
# is worth recording because the obvious one does not work: an alias
# is only visible as `alias` when its target is installed, so asking
# a Windows machine what `helvetica` resolves to shows Arial and hides
# every other candidate behind it.
#
# What does work is a machine with no fonts on it.  The reference reads
# `WINDIR` and `LOCALAPPDATA` from the environment, so pointing both at
# scratch directories and planting synthesised faces -- one copy of
# Go-Regular per candidate family, its name records rewritten -- makes
# an arbitrary machine.  Then each key is asked repeatedly, with the
# family it just answered removed, until it stops aliasing.  That peels
# an entry's list apart in order.
#
# The residual limit is the candidate set: a target nobody thought to
# plant cannot appear, so the lists are complete only up to the ~130
# families the probe offered.  A sixteenth entry, or a longer list,
# would surface as a differential divergence rather than as a failure here,
# and is a change to this table and to doc/ rather than a bug in either.
#
# None of this can be a differential probe: the answer depends on what
# is installed, and every probe in tests/differential builds under
# `--strict-fonts` precisely so that it does not.  This is what keeps
# the table answered instead.


def alias_table() -> dict[str, tuple[str, ...]]:
    """Return the alias table as doc/template.md writes it."""
    text = (ROOT / "doc" / "template.md").read_text(encoding="utf-8")
    section = text.split("### The family alias table", 1)[1]
    section = section.split("### Host enumeration", 1)[0]
    found = {}
    for line in section.splitlines():
        parts = [one.strip() for one in line.strip().strip("|").split("|")]
        if len(parts) != 2 or not parts[0].startswith("`"):
            continue
        wanted = parts[0].strip("`")
        if wanted == "typeface":
            continue
        found[wanted] = tuple(one.strip() for one in parts[1].split(","))
    return found


def test_the_alias_table_is_the_one_the_specification_gives() -> None:
    assert ALIASES == alias_table()
