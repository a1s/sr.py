"""Every face the machine has, in one table keyed by family and style.

Step 2 of doc/template.md#font-resolution, and the one part of this engine
whose answer depends on where it runs.  The sources differ per platform
and the table does not, which is the shape this module is built to:
:func:`host_sources` is the only function that asks what platform this is,
and :func:`enumerate_faces` walks whatever it is handed.

That split is deliberate rather than tidy.  It is what lets most of
this be tested by handing :func:`enumerate_faces` a fixture directory
and checking the table it builds, on any machine, without caring what
is installed.

The platform code itself still has to be run somewhere, and two of
doc/template.md's three rows have been.  **Windows** is the machine
this was written on.  The **Linux** row is a Debian container -- that is
what ``make test-linux`` runs, and the distribution matters: the kernel
supplies none of this, and where a font lives and what it is called are
decisions a distribution makes.  That run is what caught
:func:`host_sources` being asked about a platform whose sources
come from environment variables the asking machine has not got.

What it exercises that a fixture cannot: fontconfig's configuration
is really parsed, including its ``xdg`` prefix; the recursive walk meets
a real nested tree; ``fc-match`` really answers for the generic family;
Debian's `xfonts-base` supplies 480 `.pcf.gz` files for the
classify-and-skip rule; and the alias table resolves through its
later candidates, which a machine with Arial on it never reaches.

Alpine has been spot-checked once, by hand rather than in the suite,
to find out how much of that is Debian's rather than the row's.
All of it held on musl, and the one difference is the one that matters:
DejaVu lives at `/usr/share/fonts/dejavu` there and
`/usr/share/fonts/truetype/dejavu` on Debian, which is why this row
is walked recursively rather than as a list of directories.  On both,
the number of sfnt faces read equals what `fc-list` counts; the table
holds fewer, because faces that collide on a family and style are one
entry and a diagnostic.

**macOS is still not exercised.**  Its directories are literal paths so
:func:`host_sources` answers for it from anywhere, and the tests check
that it names the four the specification names, in order -- but nothing
has read a `.dfont`, and no `.ttc` holding `Helvetica` has been
enumerated, which is the case doc/template.md warns is two thirds
of that platform's faces.

Four rules from doc/template.md#host-enumeration are implemented here.
The rest belong to one face and are in :mod:`sr.fonts.face`.

* **Sources are merged, not tried in turn.**  There is one table,
  and the printout records ``host`` without saying which source answered.
* **The first found wins**, scanning sources in the tabulated order and
  files within a directory in Unicode order by name.  The loser becomes
  an enumeration diagnostic, which is not rare: a font installed in two
  directories will do it, and so will an ornament face that declares its
  parent's family.
* **Collections are enumerated face by face.**  Not a refinement to add
  later -- on macOS two thirds of the installed faces live in `.ttc`
  files, `Helvetica` and `Times` among them.
* **Nothing is dropped silently.**  A file in a format this engine does
  not read is classified and skipped; a file that claims to be an sfnt
  and then fails to parse is a warning.  Both are enumeration diagnostics,
  which doc/template.md keeps out of the printout's warning list because
  they describe the machine rather than the document.

A directory that does not exist is not an error and leaves no diagnostic.
A stock macOS has three files that can never be parsed and cannot be
removed, which is the reason these are reported by `sr validate` and
by the library's diagnostic hook rather than attached to every printout.

"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sr.errors import FontError
from sr.fonts.face import (
    Origin,
    UnsupportedFont,
    build,
    faces_in,
    near,
    read_bytes,
    sniff,
)

__all__ = [
    "Catalog",
    "Entry",
    "Key",
    "Source",
    "enumerate_faces",
    "fontconfig_monospace",
    "host_sources",
    "relaxations",
    "substitute_candidates",
]

# The Windows registry keys that name installed faces, machine-wide
# first.  A value's name is the face's display name and its data is
# a filename -- relative to the system font directory, or absolute
# for a font installed somewhere else.
REGISTRY_KEYS = (
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ("HKEY_CURRENT_USER", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
)

# Where fontconfig keeps the configuration that names its directories,
# and the directories to fall back on when that cannot be read.
#
# The fallback list is a guess and is known to be one: Debian's
# fontconfig names three of these four and Alpine's names three,
# and neither names the same three.  It is there for a machine whose
# configuration reaches its directories through `<include>` files
# this does not follow, and a directory in it that does not exist
# costs nothing.
FONTCONFIG_FILES = (
    Path("/etc/fonts/fonts.conf"),
    Path("/etc/fonts/local.conf"),
)
LINUX_DIRECTORIES = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "~/.local/share/fonts",
    "~/.fonts",
)

# doc/template.md#host-enumeration's macOS row, in its order.
MACOS_DIRECTORIES = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    "~/Library/Fonts",
)

# doc/template.md#the-substitute-face, per platform and in its order.
# A bare name is looked for in the directories above; a name with
# a separator is a path.  The Linux row's generic family is asked
# of fontconfig first, which is why its filenames come after.
SUBSTITUTES = {
    "win32": ("cour.ttf", "consola.ttf"),
    "linux": (
        "DejaVuSansMono.ttf",
        "LiberationMono-Regular.ttf",
        "NotoSansMono-Regular.ttf",
    ),
    "darwin": (
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ),
}

# The generic family fontconfig is asked for on Linux, before the
# filenames above are looked for.  Asking a matcher that answers every
# query is what the substitute step wants and what the host lookup
# forbids: here a default *is* the answer, where in step 2 it would be
# a family that does not exist being reported as one that does.
MONOSPACE_GENERIC = "monospace"
MONOSPACE_QUERY = ("fc-match", "--format=%{file}", MONOSPACE_GENERIC)

# How much of a file is read to classify it.  The longest prefix
# :func:`sr.fonts.face.sniff` matches is nine bytes, and a collection
# header's face count ends at twelve.
HEAD_BYTES = 12

# A key in the table: the family lowercased, then the two style flags.
Key = tuple[str, bool, bool]


@dataclass(frozen=True)
class Source:
    """One place the host keeps faces.

    Attributes:
        directory: The directory to scan, for a source that is one.
        files: Files named outright, for a source that lists them --
            the Windows registry, and fontconfig's own list.
        recursive: Whether to descend into subdirectories.
            The directories doc/template.md names on Windows and macOS
            are scanned flat, and `Supplemental` is a source in its own
            right precisely because it is named separately; fontconfig's
            directories hold a tree and are walked as one.

    """

    directory: Path | None = None
    files: tuple[Path, ...] = ()
    recursive: bool = False


@dataclass(frozen=True)
class Entry:
    """One face in the table, and where it came from.

    Attributes:
        family: The face's family, as its name record spells it.
        bold: What its style bits say about weight.
        italic: What they say about slant.
        origin: The file and index it was read from.

    """

    family: str
    bold: bool
    italic: bool
    origin: Origin

    @property
    def key(self) -> Key:
        """Return the key this face claims in the table."""
        return (self.family.casefold(), self.bold, self.italic)


@dataclass
class Catalog:
    """The machine's faces, and what enumerating them had to say.

    Attributes:
        entries: One face per family-and-style key, the first found.
        diagnostics: What was skipped, what would not parse,
            and which faces lost a key to another.  About the machine,
            not the document, so these do not become printout warnings.
        files: Every readable font file found, by lowercased base name.
            The substitute step looks a filename up here rather than
            walking the directories again.

    """

    entries: dict[Key, Entry] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)

    def lookup(self, family: str, bold: bool, italic: bool) -> Entry | None:
        """Return the face a family and style name, or ``None`` for a miss.

        Matching is case-insensitive, and the style relaxes within the
        family per doc/template.md#host-enumeration: the declared style,
        then the same weight upright, then the same slant at regular
        weight, then regular.  A miss is still a miss.  Nothing here
        answers with the machine's default, which doc/template.md forbids
        precisely because a matcher that always answers cannot be asked
        whether a family exists.

        Args:
            family: The typeface the template named.
            bold: Whether the `font` node declared bold.
            italic: Whether it declared italic.

        """
        name = family.casefold()
        for weight, slant in relaxations(bold, italic):
            entry = self.entries.get((name, weight, slant))
            if entry is not None:
                return entry
        return None

    def families(self) -> tuple[str, ...]:
        """Return every family in the table, once each, in Unicode order."""
        return tuple(sorted({one.family for one in self.entries.values()}))

    def admit(self, entry: Entry) -> None:
        """Add a face to the table, or record that it lost its key.

        Args:
            entry: The face and where it was read from.

        """
        held = self.entries.get(entry.key)
        if held is None:
            self.entries[entry.key] = entry
            return
        style = style_words(entry.bold, entry.italic)
        self.diagnostics.append(
            f"{entry.origin} claims {entry.family.casefold()} {style}, "
            f"which {held.origin} already holds; the first is used"
        )


def relaxations(bold: bool, italic: bool) -> tuple[tuple[bool, bool], ...]:
    """Return the styles a lookup tries, best first, each one once.

    doc/template.md#host-enumeration's order.  It only ever takes a style
    away, so a family that has nothing but a bold face never answers a
    node that declared none, and weight outranks slant: asked for bold
    italic, a family holding a bold face and an italic one answers with
    the bold.

    Args:
        bold: Whether the `font` node declared bold.
        italic: Whether it declared italic.

    """
    wanted = ((bold, italic), (bold, False), (False, italic), (False, False))
    found: list[tuple[bool, bool]] = []
    for one in wanted:
        if one not in found:
            found.append(one)
    return tuple(found)


def style_words(bold: bool, italic: bool) -> str:
    """Return the style as an enumeration diagnostic spells it.

    Args:
        bold: What the face's style bits say about weight.
        italic: What they say about slant.

    """
    words = [word for word, on in (("bold", bold), ("italic", italic)) if on]
    return " ".join(words) if words else "regular"


# -- what the host offers ---------------------------------------------


def host_sources(platform: str | None = None) -> tuple[Source, ...]:
    """Return the places this machine keeps faces, in the tabulated order.

    The three platforms do not answer alike when asked about one another,
    and the asymmetry is in doc/template.md's table rather than here.
    The Linux and macOS rows are literal paths, so they come back
    wherever this runs.  The Windows row is the registry plus two
    directories named by ``WINDIR`` and ``LOCALAPPDATA``, and none of
    those exists off Windows, so asking for it there gives nothing --
    correctly, since there is nothing to give.

    Args:
        platform: The platform to answer for; this one by default.
            Named so a test can ask for another machine's list
            without being run on it.

    """
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return windows_sources()
    if platform == "darwin":
        return tuple(Source(directory=expand(one)) for one in MACOS_DIRECTORIES)
    return linux_sources()


def windows_sources() -> tuple[Source, ...]:
    """Return the registry's faces, then the two font directories.

    The registry comes first because doc/template.md#host-enumeration
    tabulates it first, and that order is what decides which of two
    faces claiming one key is used.

    """
    sources: list[Source] = []
    registered = registry_files()
    if registered:
        sources.append(Source(files=registered))
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        sources.append(Source(directory=Path(windir) / "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        sources.append(Source(directory=Path(local) / "Microsoft/Windows/Fonts"))
    return tuple(sources)


def registry_files() -> tuple[Path, ...]:
    """Return the font files the Windows registry names, in value order.

    A value's data is a filename relative to the system font directory,
    or an absolute path for a font installed elsewhere.  Values are taken
    in Unicode order by name, which is the order the enumeration then
    sees them in and therefore part of which face wins a key.

    """
    try:
        # Imported here because it is present only on Windows.
        import winreg
    except ImportError:
        return ()
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    fonts = Path(windir) / "Fonts"
    found: list[Path] = []
    seen: set[Path] = set()
    for root_name, subkey in REGISTRY_KEYS:
        try:
            with winreg.OpenKey(getattr(winreg, root_name), subkey) as key:
                values = [
                    winreg.EnumValue(key, index)
                    for index in range(winreg.QueryInfoKey(key)[1])
                ]
        except OSError:
            continue
        for _, data, _kind in sorted(values, key=lambda one: str(one[0])):
            if not isinstance(data, str) or not data:
                continue
            path = Path(data)
            if not path.is_absolute():
                path = fonts / path
            if path not in seen:
                seen.add(path)
                found.append(path)
    return tuple(found)


def linux_sources() -> tuple[Source, ...]:
    """Return fontconfig's directories, then the ones a machine has anyway.

    fontconfig's own list is read from its configuration rather than from
    `fc-match`, which doc/template.md forbids delegating to: it answers
    every query, so a family that does not exist comes back as the host's
    default instead of as a miss.  Reading the configured directories and
    matching in this engine keeps a miss a miss.

    """
    directories: list[Path] = []
    for one in fontconfig_directories():
        if one not in directories:
            directories.append(one)
    for name in LINUX_DIRECTORIES:
        one = expand(name)
        if one not in directories:
            directories.append(one)
    return tuple(Source(directory=one, recursive=True) for one in directories)


def fontconfig_directories() -> tuple[Path, ...]:
    """Return the directories fontconfig's configuration names.

    Only the `<dir>` elements are read, and only from the two files
    that name them on a stock machine.  A configuration that reaches its
    directories through `<include>` gives nothing here, and the fallback
    list in :func:`linux_sources` is what covers that.

    """
    import xml.etree.ElementTree as parser

    found: list[Path] = []
    for config in FONTCONFIG_FILES:
        try:
            tree = parser.parse(config)
        except (OSError, parser.ParseError):
            continue
        for element in tree.getroot().iter("dir"):
            text = (element.text or "").strip()
            if not text:
                continue
            prefix = element.get("prefix")
            if prefix == "xdg":
                base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
                found.append(expand(f"{base}/{text}"))
            else:
                found.append(expand(text))
    return tuple(found)


def expand(name: str) -> Path:
    """Return a directory name with ``~`` and any variables resolved.

    Args:
        name: The directory, as the table above spells it.

    """
    return Path(os.path.expandvars(os.path.expanduser(name)))


def substitute_candidates(platform: str | None = None) -> tuple[str, ...]:
    """Return the substitute face's candidates, in the order to try them.

    On Linux fontconfig's answer for the generic family `monospace` comes
    first, as doc/template.md#the-substitute-face asks, and the three
    filenames follow it.  Where fontconfig is not installed the list is
    just the filenames, which is the same order with one entry missing.

    Args:
        platform: The platform to answer for; this one by default.

    """
    platform = sys.platform if platform is None else platform
    if platform in SUBSTITUTES and platform != "linux":
        return SUBSTITUTES[platform]
    generic = fontconfig_monospace()
    return ((generic,) if generic else ()) + SUBSTITUTES["linux"]


def fontconfig_monospace() -> str | None:
    """Return the file fontconfig gives for `monospace`, where it answers.

    Returns ``None`` when fontconfig is not installed, when it fails,
    or when it takes longer than a second -- all of which mean the
    same thing to the caller, which is to try the next candidate.

    """
    import subprocess

    try:
        done = subprocess.run(
            MONOSPACE_QUERY, capture_output=True, text=True, timeout=1, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    answer = done.stdout.strip()
    return answer if done.returncode == 0 and answer else None


# -- building the table -----------------------------------------------


def enumerate_faces(sources: tuple[Source, ...]) -> Catalog:
    """Return the table of every face the given sources hold.

    Args:
        sources: Where to look, in the order that decides which
            of two faces claiming one key is used.

    """
    catalog = Catalog()
    seen: set[Path] = set()
    for source in sources:
        for path in files_of(source):
            resolved = resolve_once(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            read_file(catalog, path)
    return catalog


def files_of(source: Source) -> Iterator[Path]:
    """Yield the files one source offers, in the order it offers them.

    Args:
        source: A directory to scan, or a list of files.

    """
    yield from source.files
    if source.directory is not None:
        yield from walk(source.directory, source.recursive)


def walk(directory: Path, recursive: bool) -> Iterator[Path]:
    """Yield the files in a directory, in Unicode order by name.

    A directory that is not there yields nothing, which
    doc/template.md#host-enumeration asks for outright: the tabulated
    sources include directories a given machine will not have.

    A symbolic link is yielded, never descended into.  `~/.fonts` is
    a directory the machine's owner writes, so a link back up the tree
    is theirs to make, and a walk that followed one would not come back.

    Args:
        directory: Where to look.
        recursive: Whether to descend into subdirectories.
            They are descended into at the point their own name falls
            in the ordering, so that the whole walk is one Unicode order.

    """
    try:
        entries = sorted(directory.iterdir(), key=lambda one: one.name)
    except OSError:
        return
    for entry in entries:
        try:
            descend = entry.is_dir() and not entry.is_symlink()
        except OSError:
            continue
        if descend:
            if recursive:
                yield from walk(entry, recursive)
            continue
        # A symbolic link is never descended into, which is what stops
        # a link that points at one of its own parents from walking
        # for ever.  It is offered as a file instead: a link to a font
        # is read as that font, and a link to a directory fails to read
        # and is recorded as a diagnostic naming it.
        yield entry


def resolve_once(path: Path) -> Path:
    """Return a path in the form two sources naming one file agree on.

    The Windows registry names the same files the system font directory
    holds, so without this every one of them would be read twice and
    lose its own key to itself.

    Args:
        path: The file, as its source named it.

    """
    try:
        return path.resolve()
    except OSError:
        return path


def read_file(catalog: Catalog, path: Path) -> None:
    """Add every face in one file to the table, or say why it was not.

    Args:
        catalog: The table being built.
        path: The file to read.

    """
    try:
        head = peek(path, HEAD_BYTES)
    except FontError as refused:
        catalog.diagnostics.append(str(refused))
        return
    if head is None:
        return
    kind, description = sniff(head)
    if kind is None:
        catalog.diagnostics.append(f"{near(path).as_posix()}: {description}; skipped")
        return
    try:
        data = read_bytes(path)
        count = faces_in(data)
    except FontError as refused:
        catalog.diagnostics.append(str(refused))
        return
    catalog.files.setdefault(path.name.casefold(), path)
    for index in range(count):
        add_face(catalog, data, Origin(path=path, index=index))


def add_face(catalog: Catalog, data: bytes, origin: Origin) -> None:
    """Add one face of one file to the table, or say why it was not.

    A file that presents itself as sfnt and then will not parse is
    a warning rather than a silent skip, which is the distinction
    doc/template.md#host-enumeration draws.  A face refused for its
    format rather than for its condition is on the other side of that
    line and is spelled as :func:`read_file` spells the ones the first
    bytes gave away, because a reader sorts on the wording and not on
    which function produced it.

    Args:
        catalog: The table being built.
        data: The whole file.
        origin: Which face of it.

    """
    try:
        face = build(data, origin)
    except UnsupportedFont as refused:
        catalog.diagnostics.append(f"{refused}; skipped")
        return
    except FontError as refused:
        catalog.diagnostics.append(str(refused))
        return
    if not face.family:
        catalog.diagnostics.append(f"{origin}: no family name; skipped")
        return
    catalog.admit(Entry(face.family, face.bold, face.italic, origin))


def peek(path: Path, count: int) -> bytes | None:
    """Return the first bytes of a file, or ``None`` where it is not one.

    Args:
        path: The file.
        count: How many bytes to read.

    Raises:
        FontError: The file is there and could not be read.

    """
    try:
        with path.open("rb") as stream:
            return stream.read(count)
    except IsADirectoryError:
        return None
    except FileNotFoundError:
        return None
    except OSError as refused:
        raise FontError(
            f"cannot read {near(path).as_posix()}: {refused.strerror or refused}"
        ) from None
