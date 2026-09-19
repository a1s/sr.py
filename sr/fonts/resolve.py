"""The four-step chain of doc/template.md#font-resolution.

A `font` node says either where its face is or what family it wants,
and this is what turns either into a face that can be measured with:

1. An explicit ``file`` or ``data``.  Resolution ends there, and
   failure is an error rather than a reason to look further.
2. [Host enumeration](:mod:`sr.fonts.hostenum`), matched by family
   and style.
3. The **family alias** table, each alias then looked for by step 2.
4. The **substitute** face, which is a guess and says so.

The order of 2 and 3 is the part worth stating twice: an alias is what
to try when the machine has no family of that name, so a machine that
has one must win.  `Helvetica`, `Times` and `Courier` are all real
families on macOS, and consulting the table first would set a macOS
report in Arial.

**Enumeration is lazy.**  A template whose fonts all name a `file`
never builds the host table, which is what makes `sr validate
--strict-fonts` fast and what keeps a machine's font troubles out
of a build that does not depend on the machine.  :class:`Resolver`
holds the catalog and builds it on the first `typeface` that needs it.

Three things come out of a resolution besides the face: which step
produced it, what the printout should record, and any warnings.
Two warnings are raised here and both are doc/template.md's.  A `font`
node that declares a style the face does not carry is reported, because
the printout's font entry is read as a description of the face.
The reverse is not: naming `Go-Bold.ttf` without `bold=#true` is
ordinary use, since the flag would only repeat what the file already says.
And a substitute is reported, because a substitute is a guess and the page
will not always show it -- text set in one may overlap rather than overflow
visibly, so the dependable signal is the printout rather than the appearance.

"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from sr.errors import BuildWarning, FontError
from sr.expr.values import quote
from sr.fonts.face import Face, Origin, build, faces_in, open_face, read_bytes
from sr.fonts.hostenum import (
    Catalog,
    Entry,
    enumerate_faces,
    host_sources,
    substitute_candidates,
)
from sr.template.model import Font

__all__ = [
    "ALIASES",
    "STEPS",
    "Resolution",
    "Resolver",
    "aliases_for",
    "tidy",
]

# The step of the chain that produced a face, as doc/printout.md#fonts
# names them.
STEPS = ("explicit", "host", "alias", "substitute")

# The family alias table of step 3: doc/template.md#the-family-alias-table,
# keyed by the case-folded family a template asks for.
#
# Each entry is an **ordered list** and not one family.  A machine has
# some of a list and not others, and which it has is the whole question, so
# an entry names every family that will do and step 2 decides between them.
#
# The core three are symmetric -- `arial` names Helvetica and `helvetica`
# names Arial -- because a template is written on one machine and built on
# another, and the name its author had is as likely to be the absent one
# as the present one.  The metric-compatible free families come last:
# Liberation Sans has Helvetica's widths, so a page set in one breaks
# its lines where a page set in the other does, which makes it a fallback
# rather than a preference.
#
# What is *not* here matters as much.  `Arial Narrow`, `Gill Sans` and
# `Monaco` are real families that some machines have and others do not,
# and aliasing one to a near neighbour would set a report in a face nobody
# asked for while recording `alias` rather than `substitute`.  A family
# that is simply absent reaches step 4 and is warned about.
ALIASES = {
    "arial": ("Helvetica", "Liberation Sans", "Nimbus Sans"),
    "helvetica": ("Arial", "Liberation Sans", "Nimbus Sans"),
    "helvetica neue": ("Arial", "Liberation Sans"),
    "times": ("Times New Roman", "Liberation Serif", "Nimbus Roman"),
    "times new roman": ("Times", "Liberation Serif", "Nimbus Roman"),
    "courier": ("Courier New", "Liberation Mono", "Nimbus Mono PS"),
    "courier new": ("Courier", "Liberation Mono", "Nimbus Mono PS"),
    "palatino": ("Palatino Linotype", "URW Palladio L"),
    "bookman": ("Bookman Old Style", "URW Bookman L"),
    "avantgarde": ("Century Gothic", "URW Gothic L"),
    "zapfdingbats": ("Zapf Dingbats", "Dingbats"),
    "symbol": ("OpenSymbol",),
    "sans-serif": ("Arial", "Helvetica", "DejaVu Sans", "Liberation Sans"),
    "serif": ("Times New Roman", "Times", "DejaVu Serif", "Liberation Serif"),
    "monospace": ("Courier New", "Consolas", "DejaVu Sans Mono", "Liberation Mono"),
}


def aliases_for(typeface: str) -> tuple[str, ...]:
    """Return the families to try for a typeface the host did not have.

    The whole name is looked up with case folded and nothing else
    normalised, so `Helvetica Neue` is a key of its own and a name
    with a trailing space is not a key at all.

    Args:
        typeface: The family the template named, as it wrote it.

    Returns:
        The aliased families, in the order to try them;
        empty where the table has nothing for this name.

    """
    return ALIASES.get(typeface.casefold(), ())


@dataclass(frozen=True)
class Resolution:
    """One `font` node, and the face it resolved to.

    Attributes:
        font: The node, for its name, size and declared style.
        face: The face that will be measured with.
        step: Which of :data:`STEPS` produced it.
        warnings: What the printout header should carry about it.

    """

    font: Font
    face: Face
    step: str
    warnings: tuple[BuildWarning, ...] = ()

    @property
    def origin(self) -> Origin:
        """Return where the face was read from."""
        return self.face.origin

    @property
    def requested(self) -> str | None:
        """Return the typeface the template asked for, where it asked.

        A `font` that named a ``file`` or ``data`` has no typeface
        to record, and doc/printout.md#fonts leaves ``requested``
        out entirely for it.

        """
        return self.font.typeface


class Resolver:
    """The chain, with the host table it may or may not need.

    One resolver serves one build.  It holds the catalog so that
    a report with four `font` nodes enumerates the machine once,
    and it holds the blobs so that a `font data=` finds its bytes.

    Attributes:
        basedir: What a ``file`` on a `font` node resolves against.
        blobs: The report's ``data`` nodes, by name.
        strict: Whether ``--strict-fonts`` is set,
            which stops the chain after step 1.

    """

    def __init__(
        self,
        *,
        basedir: Path | None = None,
        blobs: dict[str, bytes] | None = None,
        strict: bool = False,
        catalog: Catalog | None = None,
        platform: str | None = None,
    ) -> None:
        """Hold what resolving a report's fonts will need.

        Args:
            basedir: What a ``file`` resolves against;
                the working directory by default.
            blobs: The bytes of the report's ``data`` nodes, by name.
            strict: Whether only a font named by path or blob resolves.
            catalog: A host table to use instead of enumerating,
                which is how a test asks about a machine it is not running on.
            platform: The platform to answer for; this one by default.

        """
        self.basedir = Path() if basedir is None else basedir
        self.blobs = {} if blobs is None else blobs
        self.strict = strict
        self.platform = platform
        self.catalog = catalog
        self.enumerated = catalog is not None
        self.substitute: Face | None = None
        self.substitute_warnings: tuple[BuildWarning, ...] = ()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        """Return what enumerating the host had to say, if it ran.

        Empty when it did not, which is the ordinary case
        for a template whose fonts all name a file.

        """
        return () if self.catalog is None else tuple(self.catalog.diagnostics)

    def host(self) -> Catalog:
        """Return the host table, enumerating the machine on first use."""
        if self.catalog is None:
            self.catalog = enumerate_faces(host_sources(self.platform))
        return self.catalog

    def resolve(self, font: Font) -> Resolution:
        """Return the face a `font` node resolves to.

        Args:
            font: The node, as the template model holds it.

        Raises:
            FontError: The chain ran out, or strict mode stopped it.

        """
        if font.file is not None or font.data is not None:
            return self.explicit(font)
        typeface = font.typeface or ""
        if self.strict:
            raise FontError(
                "strict mode admits only a font the template names by file "
                f"or data, and this one asks for typeface {quote(typeface)}"
            )
        found = self.from_host(font, typeface)
        if found is not None:
            return found
        for alias in aliases_for(typeface):
            found = self.from_host(font, alias, step="alias")
            if found is not None:
                return found
        return self.substituted(font, typeface)

    # -- step 1 --------------------------------------------------------

    def explicit(self, font: Font) -> Resolution:
        """Return the face a `font` node names outright.

        Resolution ends here whether or not it succeeds, which is what
        doc/template.md#font-resolution means by "failure is an error":
        a template that pinned a file did not ask for a search.

        Args:
            font: The node.

        Raises:
            FontError: The file or blob is not there, or will not parse.

        """
        if font.data is not None:
            data = self.blobs.get(font.data)
            if data is None:
                raise FontError(f"data node {quote(font.data)} has no content yet")
            origin = Origin(data=font.data)
        else:
            path = tidy(self.basedir / str(font.file))
            data = read_bytes(path)
            origin = Origin(path=path)
        face = build(data, replace(origin, index=self.face_in(data, origin, font)))
        return Resolution(font, face, "explicit", self.declared_style(font, face))

    def face_in(self, data: bytes, origin: Origin, font: Font) -> int:
        """Return which face of a collection a `font` node selects.

        doc/template.md#font makes `bold` and `italic` choose among the
        faces of a `.ttc` or `.otc`: the first whose own style bits are
        exactly the ones declared, and the file's first face when none
        matches.  Style, not position -- face 0 of a collection is not
        reliably its regular one.

        Args:
            data: The whole file.
            origin: Where it came from.
            font: The node, for the style it declares.

        """
        if data[:4] != b"ttcf":
            return 0
        for index in range(faces_in(data)):
            face = build(data, Origin(path=origin.path, data=origin.data, index=index))
            if face.bold == font.bold and face.italic == font.italic:
                return index
        return 0

    # -- steps 2 and 3 -------------------------------------------------

    def from_host(
        self, font: Font, family: str, step: str = "host"
    ) -> Resolution | None:
        """Return the face the host has for a family, or ``None`` for a miss.

        Args:
            font: The node, for the style it declares.
            family: The family to look for:
                the typeface itself at step 2, an alias at step 3.
            step: Which of :data:`STEPS` this call is.

        """
        entry = self.host().lookup(family, font.bold, font.italic)
        if entry is None:
            return None
        try:
            face = self.face_of(entry)
        except FontError as refused:
            self.host().diagnostics.append(str(refused))
            return None
        return Resolution(font, face, step)

    def face_of(self, entry: Entry) -> Face:
        """Return the face a table entry names.

        Args:
            entry: The entry, with the file and index it was read from.

        Raises:
            FontError: The file has gone since it was enumerated.

        """
        return open_face(Path(str(entry.origin.path)), entry.origin.index)

    # -- step 4 --------------------------------------------------------

    def substituted(self, font: Font, typeface: str) -> Resolution:
        """Return the last-resort face, with the warning that names it.

        Args:
            font: The node.
            typeface: The family that was not found, for the warning.

        Raises:
            FontError: Every candidate was missing.

        """
        first = self.substitute is None
        face = self.find_substitute()
        warnings: tuple[BuildWarning, ...] = (
            BuildWarning(
                "font",
                f"typeface {quote(typeface)} was not found; text is set in "
                f"the substitute face {quote(face.family)} and may overflow "
                "or overlap",
                node=str(font.path),
            ),
        )
        if first:
            warnings += self.substitute_warnings
        return Resolution(font, face, "substitute", warnings)

    def find_substitute(self) -> Face:
        """Return the substitute face, opening it once per build.

        `bold` and `italic` are ignored here, since only regular faces
        are named.  A collection resolves to the face whose style bits
        say neither bold nor slanted -- not to face 0, which is the same
        face in `Menlo.ttc` but is not a rule collections keep.

        Raises:
            FontError: Every candidate was missing.

        """
        if self.substitute is not None:
            return self.substitute
        tried: list[str] = []
        for candidate in substitute_candidates(self.platform):
            path = self.substitute_path(candidate)
            tried.append(candidate)
            if path is None:
                continue
            try:
                data = read_bytes(path)
                face = build(data, Origin(path=path, index=regular(data)))
            except FontError:
                continue
            self.substitute = face
            self.substitute_warnings = monospace_warning(face)
            return face
        raise FontError(
            "no substitute face was found; tried " + ", ".join(tried or ["nothing"])
        )

    def substitute_path(self, candidate: str) -> Path | None:
        """Return where a substitute candidate is, or ``None`` if nowhere.

        A candidate with a separator in it is a path.  A bare filename
        is looked for among the files the host enumeration found, which
        is the same set of directories doc/template.md#the-substitute-face
        says to look in.

        Args:
            candidate: One row of the platform's table.

        """
        path = Path(candidate)
        if len(path.parts) > 1:
            return path if path.exists() else None
        return self.host().files.get(candidate.casefold())

    # -- what the template said about the face -------------------------

    def declared_style(self, font: Font, face: Face) -> tuple[BuildWarning, ...]:
        """Return the warning for a declared style the face does not carry.

        Only the one direction is reported.  The printout's font entry is
        read as a description of the face, so a `font` node declaring bold
        over a regular face is a claim that is not true; the reverse only
        leaves out what the file already says.

        Args:
            font: The node.
            face: The face it resolved to.

        """
        declared = [
            word
            for word, on, has in (
                ("bold", font.bold, face.bold),
                ("italic", font.italic, face.italic),
            )
            if on and not has
        ]
        if not declared:
            return ()
        return (
            BuildWarning(
                "font",
                f"font {quote(font.name)} declares {' and '.join(declared)}, "
                f"and the face taken from {face.origin} is not; no weight or "
                "slant is synthesized, and the printout records the "
                "declaration as it stands",
                node=str(font.path),
            ),
        )


def tidy(path: Path) -> Path:
    """Return a path with its ``.`` and ``..`` segments taken out.

    Not :meth:`~pathlib.Path.resolve`, which would make the path
    absolute and follow symbolic links.  A `font file=` is joined
    to the report's ``basedir``, and a template that reaches up out
    of its own directory produces `example/sakila/../fonts/Go-Bold.ttf` --
    which names the right file and reads as though it were two.  The path
    is recorded in the printout as well as shown, so tidying it here means
    tidying it once.

    Args:
        path: The joined path.

    """
    return Path(os.path.normpath(path))


def monospace_warning(face: Face) -> tuple[BuildWarning, ...]:
    """Return the warning for a substitute face that is not monospaced.

    Every candidate in doc/template.md#the-substitute-face is meant to be
    monospaced and the engine verifies it rather than trusting the list.
    A face that fails still stands in -- there is nothing better to reach
    for -- so this is a warning and not an error, and it goes in the
    printout rather than among the enumeration diagnostics because
    this face is one the report used.

    Args:
        face: The substitute that was opened.

    """
    uneven = face.uneven_advances()
    if not uneven:
        return ()
    widths = ", ".join(str(one) for one in uneven)
    return (
        BuildWarning(
            "font",
            f"the substitute face {quote(face.family)} is not monospaced; its "
            f"Latin-1 characters advance by {widths} units of "
            f"{face.units_per_em}",
        ),
    )


def regular(data: bytes) -> int:
    """Return the index of the unstyled face in a font file's bytes.

    Args:
        data: The whole file.

    """
    count = faces_in(data)
    if count == 1:
        return 0
    for index in range(count):
        face = build(data, Origin(index=index))
        if not face.bold and not face.italic:
            return index
    return 0
