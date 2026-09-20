"""Host enumeration: the table, its order, and what it says about a machine.

Most of this is tested against a fixture directory, because that is
the whole of the behaviour: the per-platform code contributes a list of
sources and nothing else, and :func:`enumerate_faces` walks whatever it
is handed.  Those tests run anywhere and do not care what is installed.

They are not enough on their own.  A platform whose source list came back
empty would pass every one of them, so the tests at the end enumerate the
real machine -- and what they assert is therefore whatever holds of any
machine with fonts on it rather than of one.  They are what makes running
the suite on a second operating system worth the trouble.

Two of doc/template.md's three rows are run.  **Windows** is
the development machine.  The **Linux** row is a Debian container --
``make test-linux``, and ``tests/linux/run.sh`` says what a real machine
exercises that a fixture cannot.  The distribution is named on purpose:
where a font lives and what it is called are a distribution's decisions,
so "Linux" names the row here and nothing else.  **macOS is neither**,
so its row is checked only for naming the four directories the specification
names, in the order it names them, and nothing has read a `.dfont` or
enumerated a `.ttc` holding `Helvetica`.

"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from sr.errors import FontError
from sr.fonts.face import ITALIC_BIT, MAC_ITALIC_BIT
from sr.fonts.hostenum import (
    HEAD_BYTES,
    MACOS_DIRECTORIES,
    SUBSTITUTES,
    Catalog,
    Entry,
    Source,
    enumerate_faces,
    fontconfig_directories,
    host_sources,
    peek,
    relaxations,
    style_words,
    substitute_candidates,
    walk,
)

ROOT = Path(__file__).resolve().parents[2]
REGULAR = ROOT / "example" / "fonts" / "Go-Regular.ttf"
BOLD = ROOT / "example" / "fonts" / "Go-Bold.ttf"

# `OS/2.fsSelection` bit 6, which a face sets to say it is neither bold
# nor italic.  fontTools refuses to save a face that sets it alongside
# either of the others, so the italics made here must clear it.
REGULAR_BIT = 1 << 6

# The version a collection header carries, in the four bytes
# after its tag.  The headers written below are damaged in the
# face count that follows it, never in this.
VERSION = 0x00010000


def table(directory: Path, recursive: bool = False) -> Catalog:
    """Return the table one directory builds.

    Args:
        directory: What to scan.
        recursive: Whether to descend into subdirectories.

    """
    return enumerate_faces((Source(directory=directory, recursive=recursive),))


# The four styles, as the tables of a face spell them
# and as the tests below name them.
STYLES = {
    "R": (False, False),
    "B": (True, False),
    "I": (False, True),
    "BI": (True, True),
}


def write_style(directory: Path, style: str) -> Path:
    """Write one face of the Go family in one style, and return its path.

    The example fonts are a regular and a bold, so the two italics
    are made here by setting the bits rather than shipped.  Which is
    the point being tested: style is what the style bits say, never
    the filename or the subfamily string.

    Args:
        directory: Where to write it.
        style: One of :data:`STYLES`' keys.

    """
    bold, italic = STYLES[style]
    font = TTFont(str(BOLD if bold else REGULAR))
    selection = int(font["OS/2"].fsSelection) & ~(ITALIC_BIT | REGULAR_BIT)
    mac = int(font["head"].macStyle) & ~MAC_ITALIC_BIT
    if italic:
        selection |= ITALIC_BIT
        mac |= MAC_ITALIC_BIT
    elif not bold:
        selection |= REGULAR_BIT
    font["OS/2"].fsSelection = selection
    font["head"].macStyle = mac
    path = directory / f"Go-{style}.ttf"
    font.save(str(path))
    return path


# -- the table --------------------------------------------------------


def test_every_face_in_a_directory_reaches_the_table(font_directory: Path) -> None:
    catalog = table(font_directory)
    assert catalog.lookup("Go", bold=False, italic=False) is not None
    assert catalog.lookup("Go", bold=True, italic=False) is not None
    assert catalog.families() == ("Go",)


def test_a_family_is_matched_without_regard_to_case(font_directory: Path) -> None:
    catalog = table(font_directory)
    for spelling in ("Go", "go", "GO", "gO"):
        assert catalog.lookup(spelling, bold=False, italic=False) is not None


def test_a_family_the_machine_has_not_got_is_a_miss(font_directory: Path) -> None:
    assert table(font_directory).lookup("Nothing", bold=False, italic=False) is None


# How a lookup relaxes, read off the reference engine one machine
# at a time: each row is a directory holding exactly those styles of one
# family, the style a `font` node declared, and the face that answered.
#
# The shape of it is that a lookup only ever takes a style away.
# A family holding nothing but a bold face does not answer a node that
# declared none, however plainly a human would say the family is there;
# and weight outranks slant, so bold italic asked of a family with
# a bold face and an italic one gets the bold.
RELAXATIONS = [
    ("R", (True, False), "R"),
    ("R", (False, True), "R"),
    ("R", (True, True), "R"),
    ("B", (True, False), "B"),
    ("B", (True, True), "B"),
    ("B", (False, True), None),
    ("B", (False, False), None),
    ("BI", (True, True), "BI"),
    ("BI", (True, False), None),
    ("BI", (False, True), None),
    ("BI", (False, False), None),
    ("R I", (True, True), "I"),
    ("R I", (False, False), "R"),
    ("B I", (True, True), "B"),
]


@pytest.mark.parametrize(("present", "asked", "answers"), RELAXATIONS)
def test_a_lookup_relaxes_the_style_within_the_family(
    tmp_path: Path, present: str, asked: tuple[bool, bool], answers: str | None
) -> None:
    directory = tmp_path / present.replace(" ", "-")
    directory.mkdir()
    for style in present.split():
        write_style(directory, style)
    found = table(directory).lookup("Go", bold=asked[0], italic=asked[1])
    if answers is None:
        assert found is None
        return
    assert found is not None
    assert (found.bold, found.italic) == STYLES[answers]


def test_the_relaxation_order_is_the_specification_s() -> None:
    assert relaxations(True, True) == (
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    )


@pytest.mark.parametrize(
    ("asked", "tries"),
    [
        ((False, False), ((False, False),)),
        ((True, False), ((True, False), (False, False))),
        ((False, True), ((False, True), (False, False))),
    ],
)
def test_a_relaxation_is_never_tried_twice(
    asked: tuple[bool, bool], tries: tuple[tuple[bool, bool], ...]
) -> None:
    assert relaxations(*asked) == tries


def test_a_collection_is_enumerated_face_by_face(
    tmp_path: Path, collection_bytes: bytes
) -> None:
    directory = tmp_path / "only"
    directory.mkdir()
    (directory / "Go.ttc").write_bytes(collection_bytes)
    catalog = table(directory)
    regular = catalog.lookup("Go", bold=False, italic=False)
    bold = catalog.lookup("Go", bold=True, italic=False)
    assert regular is not None and regular.origin.index == 0
    assert bold is not None and bold.origin.index == 1


def test_a_directory_that_is_not_there_is_not_an_error(tmp_path: Path) -> None:
    catalog = table(tmp_path / "nowhere")
    assert catalog.entries == {}
    assert catalog.diagnostics == []


# -- what is not a font -----------------------------------------------


def test_a_format_it_cannot_read_is_classified_and_skipped(
    font_directory: Path,
) -> None:
    said = "\n".join(table(font_directory).diagnostics)
    assert "bitmap.fon" in said and "bitmap font" in said
    assert "type1.pfa" in said and "Type 1" in said
    assert "webfont.woff" in said and "WOFF" in said
    assert "notes.txt" in said and "not an sfnt" in said


def test_a_file_claiming_to_be_an_sfnt_is_a_diagnostic_of_its_own(
    font_directory: Path,
) -> None:
    said = [one for one in table(font_directory).diagnostics if "truncated" in one]
    assert len(said) == 1
    assert "skipped" not in said[0]


def test_a_bitmap_only_sfnt_is_skipped_and_not_reported_as_broken(
    font_directory: Path,
) -> None:
    # The one unsupported format the first bytes do not give away.
    # It belongs on the `skipped` side of the line with the other formats
    # this engine does not read, not with the files that would not parse,
    # because those two say different things about the machine.
    said = [one for one in table(font_directory).diagnostics if "Bitmapped" in one]
    assert len(said) == 1
    assert "bitmap-only sfnt" in said[0]
    assert said[0].endswith("; skipped")


def test_a_directory_where_a_file_was_expected_is_a_diagnostic(
    tmp_path: Path,
) -> None:
    # A source that names files can hand over a directory, and the walk
    # hands over a symbolic link pointing at one for the same reason.
    # What the platform calls the failure differs -- `Is a directory`
    # where the link test below can run, `Permission denied` on Windows --
    # and what matters is that the line is there and names the path.
    offered = tmp_path / "pointer"
    offered.mkdir()
    catalog = enumerate_faces((Source(files=(offered,)),))
    assert catalog.entries == {}
    assert [one for one in catalog.diagnostics if "pointer" in one]


def test_a_damaged_collection_header_names_the_file_it_is_about(
    tmp_path: Path,
) -> None:
    # The header is read before any face is, by a function that is handed
    # bytes and has no file to name.  Every other line of an enumeration
    # report says which file it is about, and this one used not to.
    directory = tmp_path / "damaged"
    directory.mkdir()
    (directory / "Half.ttc").write_bytes(b"ttcf" + struct.pack(">I", VERSION))
    said = table(directory).diagnostics
    assert len(said) == 1
    assert "Half.ttc" in said[0]
    assert "truncated font collection header" in said[0]


def test_a_collection_claiming_no_faces_is_not_dropped_silently(
    tmp_path: Path,
) -> None:
    # A whole header, and the count in it is zero: there is no face
    # to fail to parse and nothing to put in the table, so without
    # a line of its own the file would leave no trace at all.
    directory = tmp_path / "empty"
    directory.mkdir()
    (directory / "None.ttc").write_bytes(b"ttcf" + struct.pack(">II", VERSION, 0))
    said = table(directory).diagnostics
    assert len(said) == 1
    assert "None.ttc" in said[0]
    assert "no faces" in said[0]


def test_every_diagnostic_spells_its_path_the_one_way(font_directory: Path) -> None:
    # The diagnostics of one directory are read together, and they used
    # to arrive in two spellings: the ones raised while classifying a file
    # took the path as the platform writes it, and the ones raised while
    # reading a face took it as `Origin` does.  On Windows that put both
    # slashes in one report, for two files side by side.
    said = table(font_directory).diagnostics
    assert said
    assert not [one for one in said if "\\" in one]


def test_nothing_that_was_skipped_reached_the_table(font_directory: Path) -> None:
    assert table(font_directory).families() == ("Go",)


# -- two faces claiming one key ---------------------------------------


def test_the_first_found_wins_and_the_loser_is_a_diagnostic(tmp_path: Path) -> None:
    directory = tmp_path / "twice"
    directory.mkdir()
    face = (ROOT / "example" / "fonts" / "Go-Regular.ttf").read_bytes()
    (directory / "a-first.ttf").write_bytes(face)
    (directory / "b-second.ttf").write_bytes(face)
    catalog = table(directory)
    held = catalog.lookup("Go", bold=False, italic=False)
    assert held is not None
    assert held.origin.path is not None
    assert held.origin.path.name == "a-first.ttf"
    assert len(catalog.diagnostics) == 1
    assert "b-second.ttf" in catalog.diagnostics[0]
    assert "a-first.ttf" in catalog.diagnostics[0]
    assert "claims go regular" in catalog.diagnostics[0]


def test_one_file_read_twice_does_not_lose_its_key_to_itself(
    font_directory: Path,
) -> None:
    # The regular face is named outright and then met again by the scan.
    # The collection beside it does claim the same key and is expected
    # to say so; the file reading itself is what must not.
    both = (
        Source(files=(font_directory / "Go-Regular.ttf",)),
        Source(directory=font_directory),
    )
    catalog = enumerate_faces(both)
    itself = str(font_directory / "Go-Regular")
    assert not [one for one in catalog.diagnostics if one.startswith(itself)]


@pytest.mark.parametrize(
    ("bold", "italic", "words"),
    [
        (False, False, "regular"),
        (True, False, "bold"),
        (False, True, "italic"),
        (True, True, "bold italic"),
    ],
)
def test_a_style_is_named_the_way_a_diagnostic_names_it(
    bold: bool, italic: bool, words: str
) -> None:
    assert style_words(bold, italic) == words


# -- order ------------------------------------------------------------


def test_files_are_walked_in_unicode_order_by_name(tmp_path: Path) -> None:
    # Not one name that differs from another only in case: the file
    # system this runs on would make those one file, and the ordering
    # this checks is a property of the walk rather than of the disk.
    for name in ("delta.ttf", "Alpha.ttf", "_under.ttf", "beta.ttf"):
        (tmp_path / name).write_bytes(b"")
    assert [one.name for one in walk(tmp_path, recursive=False)] == [
        "Alpha.ttf",
        "_under.ttf",
        "beta.ttf",
        "delta.ttf",
    ]


def test_a_subdirectory_is_descended_into_where_its_name_falls(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.ttf").write_bytes(b"")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "inner.ttf").write_bytes(b"")
    (tmp_path / "z.ttf").write_bytes(b"")
    assert [one.name for one in walk(tmp_path, recursive=True)] == [
        "a.ttf",
        "inner.ttf",
        "z.ttf",
    ]


def test_a_subdirectory_is_left_alone_when_the_source_is_flat(tmp_path: Path) -> None:
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "inner.ttf").write_bytes(b"")
    assert list(walk(tmp_path, recursive=False)) == []


def link(source: Path, target: Path) -> None:
    """Make a symbolic link, or skip the test where that is not allowed.

    Windows wants either developer mode or an administrator for one,
    and what these tests are about is a machine whose owner has made one.

    Args:
        source: The link to create.
        target: What it points at.

    """
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as refused:
        pytest.skip(f"symbolic links are not available here: {refused}")


def test_a_link_to_a_directory_is_not_descended_into(tmp_path: Path) -> None:
    # The link is offered as though it were a file, which is what makes
    # it reach the diagnostics instead of vanishing: a directory where
    # a font was expected is something the machine's owner should hear.
    inside = tmp_path / "elsewhere"
    inside.mkdir()
    (inside / "hidden.ttf").write_bytes(b"")
    walked = tmp_path / "fonts"
    walked.mkdir()
    link(walked / "pointer", inside)
    assert [one.name for one in walk(walked, recursive=True)] == ["pointer"]
    # And the read of it fails and says so, which is the half of this
    # that only a platform whose error is `Is a directory` exercises.
    catalog = table(walked, recursive=True)
    assert catalog.entries == {}
    assert [one for one in catalog.diagnostics if "pointer" in one]


def test_a_link_whose_target_is_gone_is_a_diagnostic(tmp_path: Path) -> None:
    # A font removed with its link left behind in `~/.fonts` is the
    # machine's own state, and the walk yields a link as a file so that
    # it is read and named.  A file that goes between the walk and the
    # read is the other thing that arrives as a missing file, and that
    # one is a race and leaves no line; the test below pins it.
    target = tmp_path / "gone.ttf"
    target.write_bytes(REGULAR.read_bytes())
    walked = tmp_path / "fonts"
    walked.mkdir()
    link(walked / "dangling.ttf", target)
    target.unlink()
    catalog = table(walked, recursive=True)
    assert catalog.entries == {}
    assert [one for one in catalog.diagnostics if "dangling.ttf" in one]


def test_a_file_that_is_simply_not_there_leaves_no_diagnostic(tmp_path: Path) -> None:
    # The Windows registry names files, and a walk hands over names that
    # a moment later are not files any more.  Neither says anything
    # about the machine that its owner can act on.
    catalog = enumerate_faces((Source(files=(tmp_path / "gone.ttf",)),))
    assert catalog.entries == {}
    assert catalog.diagnostics == []


def test_the_two_kinds_of_missing_file_are_told_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pair of tests above is the real one, and it runs only where
    # a symbolic link can be made -- not on a Windows without the
    # privilege for it.  This is the same distinction asked of the one
    # function that draws it, so that the branch is exercised anywhere.
    gone = tmp_path / "gone.ttf"
    assert peek(gone, HEAD_BYTES) is None
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    with pytest.raises(FontError, match="target is gone"):
        peek(gone, HEAD_BYTES)


def test_a_link_to_a_file_is_walked_as_that_file(tmp_path: Path) -> None:
    target = tmp_path / "Go-Regular.ttf"
    target.write_bytes(REGULAR.read_bytes())
    walked = tmp_path / "fonts"
    walked.mkdir()
    link(walked / "linked.ttf", target)
    assert table(walked).lookup("Go", bold=False, italic=False) is not None


def test_a_link_that_points_back_up_the_tree_does_not_walk_for_ever(
    tmp_path: Path,
) -> None:
    # `~/.fonts` belongs to the machine's owner, so a loop in it is
    # theirs to make and this engine's to survive.  Without the rule
    # above this raises RecursionError rather than failing an assertion.
    walked = tmp_path / "fonts"
    walked.mkdir()
    (walked / "a.ttf").write_bytes(b"")
    link(walked / "loop", walked)
    assert sorted(one.name for one in walk(walked, recursive=True)) == [
        "a.ttf",
        "loop",
    ]


# -- the platform tables ----------------------------------------------


def test_the_literal_platform_tables_answer_wherever_this_runs() -> None:
    # Linux and macOS name directories outright, so asking about
    # either from the other works.  Windows does not; see below.
    assert host_sources("linux")
    assert host_sources("darwin")


def test_the_windows_sources_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Windows row is two directories named by environment variables
    # and a registry this may not have, so it answers only where the
    # environment says where to look.  That is why the check above
    # does not ask about it, and this one sets the environment first.
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    named = [one.directory for one in host_sources("win32") if one.directory]
    assert tmp_path / "Windows" / "Fonts" in named
    assert tmp_path / "Local" / "Microsoft/Windows/Fonts" in named


def test_the_windows_sources_are_empty_where_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("WINDIR", "SystemRoot", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sr.fonts.hostenum.registry_files", tuple)
    assert host_sources("win32") == ()


def test_the_macos_sources_are_the_four_the_specification_names() -> None:
    sources = host_sources("darwin")
    assert len(sources) == len(MACOS_DIRECTORIES)
    assert [one.directory for one in sources] == [
        Path(one).expanduser() for one in MACOS_DIRECTORIES
    ]
    assert not any(one.recursive for one in sources)


def test_the_linux_sources_are_walked_as_trees() -> None:
    assert all(one.recursive for one in host_sources("linux"))


def test_the_substitute_candidates_are_the_platform_row() -> None:
    assert substitute_candidates("win32") == SUBSTITUTES["win32"]
    assert substitute_candidates("darwin") == SUBSTITUTES["darwin"]


def test_the_linux_substitutes_end_with_the_three_filenames() -> None:
    candidates = substitute_candidates("linux")
    assert candidates[-3:] == SUBSTITUTES["linux"]


# -- the machine this actually runs on --------------------------------
#
# These enumerate the real host, so what they can assert is whatever
# is true of every machine with fonts on it rather than of one.
# They are what makes running the suite on another operating system
# worth anything: the fixture tests above would pass on a platform
# whose source list came back empty, and these would not.


@pytest.fixture(scope="module")
def this_machine() -> Catalog:
    """Return the table this machine's own sources build."""
    return enumerate_faces(host_sources())


@pytest.mark.slow
def test_a_machine_with_fonts_enumerates_some(this_machine: Catalog) -> None:
    if not this_machine.entries:
        pytest.skip("no fonts on this machine")
    assert this_machine.families()


@pytest.mark.slow
def test_every_family_found_can_be_looked_up_again(this_machine: Catalog) -> None:
    if not this_machine.entries:
        pytest.skip("no fonts on this machine")
    for entry in this_machine.entries.values():
        found = this_machine.lookup(entry.family, entry.bold, entry.italic)
        assert found is not None
        assert found.family == entry.family


@pytest.mark.slow
def test_every_face_found_is_still_readable(this_machine: Catalog) -> None:
    if not this_machine.entries:
        pytest.skip("no fonts on this machine")
    for entry in this_machine.entries.values():
        assert entry.origin.path is not None
        assert entry.origin.path.exists()


@pytest.mark.slow
def test_no_two_entries_share_a_key(this_machine: Catalog) -> None:
    keys = [entry.key for entry in this_machine.entries.values()]
    assert len(keys) == len(set(keys))


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_windows_looks_at_the_registry_and_the_two_directories() -> None:
    sources = host_sources("win32")
    assert any(one.files for one in sources)
    assert any(
        one.directory is not None and one.directory.name == "Fonts" for one in sources
    )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Arial and Courier New are a Windows guarantee",
)
@pytest.mark.slow
def test_this_machine_has_a_family_every_windows_has() -> None:
    catalog = enumerate_faces(host_sources())
    assert catalog.lookup("Courier New", bold=False, italic=False) is not None
    assert catalog.lookup("Arial", bold=True, italic=False) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="fontconfig is not on Windows")
@pytest.mark.slow
def test_fontconfig_names_the_directories_it_was_configured_with() -> None:
    directories = fontconfig_directories()
    if not directories:
        pytest.skip("fontconfig is not configured on this machine")
    assert any(one.exists() for one in directories)
    named = [one.directory for one in host_sources("linux")]
    for one in directories:
        assert one in named


def test_an_entry_keys_itself_by_family_and_style() -> None:
    entry = Entry("Go Mono", bold=True, italic=False, origin=None)  # type: ignore[arg-type]
    assert entry.key == ("go mono", True, False)
