"""A printout, rendered: the pages, the outline, and what holds them.

:func:`render` reads a printout three times, and the order is forced.

1. **The fonts and the images are opened**, and every character each
   face sets and every image each mark shows is noted, in paint order.
   A face's codes are in code point order over the whole document,
   so nothing can be drawn until every page has been read.
2. **The pages are drawn**, once each face is frozen.  An xref's
   nested marks are drawn where the xref is, as top-level marks are,
   and the xref itself becomes a link annotation over its box.
3. **The rest is written**: the faces, the images, the outline.

The whole file is built in memory and returned as bytes, which is what
lets `sr.py render` leave a previous output in place when it fails:
doc/cli.md#sr-render renders the whole document before opening the file.

The printout is read as its writer orders it and not as memory happens
to: fonts by name, as the header lists them, and images by first use.
So a printout handed over by the engine and the same printout read back
from a file render to the same bytes, which doc/render.md#reproducibility
asks for.

"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from sr.errors import FontError, RenderError
from sr.fonts.face import Origin, build, near
from sr.fonts.text import Metrics
from sr.pdf import draw
from sr.pdf.draw import Setting
from sr.pdf.images import Picture, picture_of
from sr.pdf.objects import Name, Ref, Stream, Text, Writer
from sr.pdf.subset import Embedded
from sr.printout.model import (
    Barcode,
    FontEntry,
    Image,
    Line,
    Mark,
    Outline,
    Page,
    Paper,
    Printout,
    Rectangle,
    Xref,
)
from sr.printout.model import (
    Text as TextMark,
)

__all__ = ["pdf_date", "render"]

# RFC 3339 as the header's `built` field writes it,
# and as it may be written by a printout another engine made.
RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
    r"(?:([Zz])|([+-])(\d{2}):(\d{2}))\Z"
)

# What a URI action's URI may hold as itself: printable ASCII.
# Everything else is percent-encoded as UTF-8, which is how
# an IRI becomes the URI a PDF string can carry.
URI_SAFE = "".join(chr(code) for code in range(0x21, 0x7F))


def render(printout: Printout, compress: bool = True) -> bytes:
    """Return a printout as a PDF file.

    Args:
        printout: The document, as the engine built it
            or as :func:`sr.printout.read.read` read it.
        compress: Whether streams are Flate-compressed;
            ``False`` is ``--uncompressed``.

    Raises:
        RenderError: Something in the printout cannot be drawn:
            a font or an image that will not read, a face with
            PostScript outlines, a text mark naming a font the
            header does not carry, an xref to an outline entry
            no entry claims, or a printout with no pages.

    """
    if not printout.pages:
        raise RenderError("the printout has no pages, and a PDF has at least one")
    return Rendering(printout, Writer(compress)).run()


def pdf_date(built: str) -> str:
    """Return the header's `built` time as a PDF date string.

    Args:
        built: RFC 3339, as the header writes it.

    Raises:
        RenderError: It is not RFC 3339.

    """
    found = RFC3339.match(built)
    if found is None:
        raise RenderError(
            f"the header's `built` time {built!r} is not an RFC 3339 time"
        )
    year, month, day, hour, minute, second = found.groups()[:6]
    stamp = f"D:{year}{month}{day}{hour}{minute}{second}"
    if found.group(7):
        return stamp + "Z"
    sign, hours, minutes = found.groups()[7:]
    return f"{stamp}{sign}{hours}'{minutes}'"


@dataclass
class Entry:
    """One outline entry, as the tree holds it.

    Attributes:
        mark: The entry as the printout has it.
        page: The page it is on, from 0.
        ref: Its object.
        children: The entries one level below it, in order.

    """

    mark: Outline
    page: int
    ref: Ref
    children: list[Entry] = field(default_factory=list)

    def shown(self) -> int:
        """Return how many entries a reader shows when this one is open."""
        shown = 0
        for child in self.children:
            shown += 1 if child.mark.closed else 1 + child.shown()
        return shown


@dataclass
class Rendering:
    """One render in progress.

    Attributes:
        printout: The document.
        writer: The file's object table.
        settings: Each font entry by name, as text is set in it.
        faces: Each face once, in the order the header's entries,
            sorted by name, first reach it.
        pictures: Each image once, by the digest of its bytes.
        sources: Each image once, by where its bytes come from.
        pages: Each page's object, in order.
        entries: Every outline entry, in document order.
        named: The first entry carrying each name.

    """

    printout: Printout
    writer: Writer
    settings: dict[str, Setting] = field(default_factory=dict)
    faces: list[Embedded] = field(default_factory=list)
    pictures: dict[bytes, Picture] = field(default_factory=dict)
    sources: dict[tuple[str, str], Picture] = field(default_factory=dict)
    pages: list[Ref] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    named: dict[str, Entry] = field(default_factory=dict)

    def run(self) -> bytes:
        """Return the whole file."""
        writer = self.writer
        catalog = writer.reserve()
        tree = writer.reserve()
        info = writer.reserve()
        resources = writer.reserve()
        self.pages = [writer.reserve() for _ in self.printout.pages]
        self.typefaces()
        for ordinal, page in enumerate(self.printout.pages):
            self.survey(ordinal, page)
        for face in self.faces:
            face.freeze()
        for ordinal, page in enumerate(self.printout.pages):
            self.page(page, self.pages[ordinal], tree, resources)
        writer.put(
            tree,
            {
                "Type": Name("Pages"),
                "Kids": self.pages,
                "Count": len(self.pages),
            },
        )
        writer.put(resources, self.resources())
        root: dict[str, Any] = {"Type": Name("Catalog"), "Pages": tree}
        if self.entries:
            root["Outlines"] = self.outline()
            root["PageMode"] = Name("UseOutlines")
        writer.put(catalog, root)
        writer.put(info, self.information())
        return writer.finish(catalog, info)

    # -- the first pass --------------------------------------------------

    def typefaces(self) -> None:
        """Open every face the header names, once per face.

        Entries are read in name order, which is the order the
        header lists them in, so the faces are numbered the same way
        whether the printout came from the engine or from a file.

        """
        by_face: dict[tuple[bytes, int], Embedded] = {}
        for entry in sorted(self.printout.fonts, key=lambda one: one.name):
            data, origin = self.font_source(entry)
            key = (hashlib.sha256(data).digest(), entry.index)
            embedded = by_face.get(key)
            if embedded is None:
                embedded = self.embedded(entry, data, origin, len(by_face) + 1)
                by_face[key] = embedded
                self.faces.append(embedded)
            self.settings[entry.name] = Setting(
                embedded,
                entry.size,
                Metrics(embedded.face, entry.size),
                entry.underline,
            )

    def font_source(self, entry: FontEntry) -> tuple[bytes, Origin]:
        """Return the bytes a font entry's face is in, and where they are.

        Args:
            entry: The font entry.

        Raises:
            RenderError: The file will not read,
                or the data entry is not in the header.

        """
        if entry.data is not None:
            blob = self.printout.data.get(entry.data)
            if blob is None:
                said = f"the header holds no data {entry.data!r}"
                raise RenderError(f"font {entry.name!r}: {said}")
            data = blob.encode("utf-8") if isinstance(blob, str) else blob
            return data, Origin(data=entry.data, index=entry.index)
        if entry.file is None:
            raise RenderError(
                f"font {entry.name!r}: it names no file and no data",
            )
        try:
            data = entry.file.read_bytes()
        except OSError as refused:
            raise RenderError(
                f"font {entry.name!r}: cannot read "
                f"{near(entry.file).as_posix()}: {refused.strerror or refused}"
            ) from None
        return data, Origin(path=entry.file, index=entry.index)

    def embedded(
        self,
        entry: FontEntry,
        data: bytes,
        origin: Origin,
        ordinal: int,
    ) -> Embedded:
        """Return a face as the file will carry it.

        Args:
            entry: The first font entry to name the face.
            data: The font file.
            origin: Where it came from.
            ordinal: The face's number among the document's faces.

        Raises:
            RenderError: The face will not open,
                or its outlines are not ones this renderer embeds.

        """
        try:
            face = build(data, origin)
        except FontError as refused:
            raise RenderError(f"font {entry.name!r}: {refused}") from None
        if "glyf" not in face.font:
            if "CFF " in face.font or "CFF2" in face.font:
                raise RenderError(
                    f"font {entry.name!r}: {origin} has PostScript (CFF) "
                    "outlines, which this renderer does not embed"
                )
            raise RenderError(
                f"font {entry.name!r}: {origin} has no glyf table",
            )
        return Embedded(face, data, entry.index, f"F{ordinal}")

    def survey(self, ordinal: int, page: Page) -> None:
        """Note what one page sets and shows, and its outline entries.

        Args:
            ordinal: The page's position, from 0.
            page: The page.

        """
        for where, mark in flattened(page):
            if isinstance(mark, TextMark):
                setting = self.setting(mark, where)
                for line in mark.lines:
                    setting.font.take(line)
            elif isinstance(mark, Image):
                self.picture(mark, where)
            elif isinstance(mark, Outline):
                entry = Entry(mark, ordinal, self.writer.reserve())
                self.entries.append(entry)
                if mark.name is not None:
                    self.named.setdefault(mark.name, entry)

    def setting(self, mark: TextMark, where: str) -> Setting:
        """Return the font a text mark is set in.

        Args:
            mark: The text mark.
            where: The mark, as a diagnostic names it.

        Raises:
            RenderError: The header carries no font of that name.

        """
        setting = self.settings.get(mark.font)
        if setting is None:
            raise RenderError(
                f"{where}: the header carries no font {mark.font!r}",
            )
        return setting

    def picture(self, mark: Image, where: str) -> Picture:
        """Return the image a mark shows, embedding it on its first use.

        Two marks whose bytes are the same share one object,
        which is what makes two marks reading one source share one.
        A source is read and decoded once, however many marks show it.

        Args:
            mark: The image mark.
            where: The mark, as a diagnostic names it.

        Raises:
            RenderError: Its bytes will not read, or will not decode.

        """
        if mark.data is not None:
            source = ("data", mark.data)
        else:
            source = ("file", str(mark.file))
        known = self.sources.get(source)
        if known is not None:
            return known
        if mark.data is not None:
            blob = self.printout.data.get(mark.data)
            if blob is None:
                raise RenderError(
                    f"{where}: the header holds no data {mark.data!r}",
                )
            data = blob.encode("utf-8") if isinstance(blob, str) else blob
            shown = f"{where}: data {mark.data!r}"
        else:
            assert mark.file is not None
            try:
                data = mark.file.read_bytes()
            except OSError as refused:
                raise RenderError(
                    f"{where}: cannot read {near(mark.file).as_posix()}: "
                    f"{refused.strerror or refused}"
                ) from None
            shown = f"{where}: {near(mark.file).as_posix()}"
        key = hashlib.sha256(data).digest()
        found = self.pictures.get(key)
        if found is None:
            found = picture_of(data, f"Im{len(self.pictures) + 1}", shown)
            self.pictures[key] = found
        self.sources[source] = found
        return found

    # -- the second pass -------------------------------------------------

    def page(self, page: Page, ref: Ref, tree: Ref, resources: Ref) -> None:
        """Draw one page, and write it with its content and its links.

        Args:
            page: The page.
            ref: Its object.
            tree: The page tree it belongs to.
            resources: The resource dictionary every page shares.

        """
        paper = self.paper(page)
        height = paper.height
        painted: list[str] = []
        links: list[Ref] = []
        for where, mark in flattened(page):
            if isinstance(mark, TextMark):
                setting = self.setting(mark, where)
                painted.extend(draw.text(mark, setting, height))
            elif isinstance(mark, Line):
                painted.extend(draw.line(mark, height))
            elif isinstance(mark, Rectangle):
                painted.extend(draw.rectangle(mark, height))
            elif isinstance(mark, Image):
                picture = self.picture(mark, where)
                painted.extend(draw.image(mark, picture, height))
            elif isinstance(mark, Barcode):
                painted.extend(draw.barcode(mark, height))
            elif isinstance(mark, Xref):
                links.append(self.writer.add(self.link(mark, where, height)))
        content = self.writer.add(
            Stream({}, "".join(one + "\n" for one in painted).encode("ascii"))
        )
        found: dict[str, Any] = {
            "Type": Name("Page"),
            "Parent": tree,
            "MediaBox": [0, 0, paper.width, paper.height],
            "Resources": resources,
            "Contents": content,
        }
        if links:
            found["Annots"] = links
        self.writer.put(ref, found)

    def paper(self, page: Page) -> Paper:
        """Return the geometry a page runs at: its own, or the document's.

        Args:
            page: The page.

        """
        return page.paper or self.printout.paper

    def link(self, mark: Xref, where: str, height: float) -> dict[str, Any]:
        """Return an xref's link annotation, over its box and with no border.

        Args:
            mark: The xref.
            where: The mark, as a diagnostic names it.
            height: The page's height.

        Raises:
            RenderError: It targets an outline entry no entry claims.

        """
        box = mark.box
        found: dict[str, Any] = {
            "Type": Name("Annot"),
            "Subtype": Name("Link"),
            "Rect": [
                box.x,
                height - box.bottom,
                box.x + box.width,
                height - box.y,
            ],
            "Border": [0, 0, 0],
        }
        if mark.link == "outline":
            entry = self.named.get(mark.target)
            if entry is None:
                raise RenderError(
                    f"{where}: the xref targets the outline entry "
                    f"{mark.target!r}, and no entry is named so"
                )
            found["Dest"] = self.destination(entry)
        else:
            uri = quote(mark.target, safe=URI_SAFE, errors="surrogatepass")
            found["A"] = {"S": Name("URI"), "URI": uri.encode("ascii")}
        if mark.caption is not None:
            found["Contents"] = Text(mark.caption)
        return found

    # -- the rest --------------------------------------------------------

    def resources(self) -> dict[str, Any]:
        """Return the one resource dictionary every page shares."""
        found: dict[str, Any] = {}
        if self.faces:
            found["Font"] = {
                face.resource: face.write(self.writer) for face in self.faces
            }
        if self.pictures:
            found["XObject"] = {
                picture.resource: picture.write(self.writer)
                for picture in self.pictures.values()
            }
        return found

    def destination(self, entry: Entry) -> list[Any]:
        """Return where an outline entry goes: its own point on its page.

        Args:
            entry: The entry.

        """
        page = self.printout.pages[entry.page]
        height = self.paper(page).height
        return [
            self.pages[entry.page],
            Name("XYZ"),
            entry.mark.box.x,
            height - entry.mark.box.y,
            None,
        ]

    def outline(self) -> Ref:
        """Write the outline tree, and return its root.

        doc/render.md#outline-and-links: an entry one level deeper
        than the one before it is that one's child, and an entry that
        jumps by more than one is one deeper rather than refused.

        """
        root = self.writer.reserve()
        top: list[Entry] = []
        path: list[Entry] = []
        for entry in self.entries:
            depth = max(1, min(entry.mark.level, len(path) + 1))
            del path[depth - 1 :]
            (path[-1].children if path else top).append(entry)
            path.append(entry)
        self.siblings(top, root)
        self.writer.put(
            root,
            {
                "Type": Name("Outlines"),
                "First": top[0].ref,
                "Last": top[-1].ref,
                "Count": sum(
                    1 + (0 if one.mark.closed else one.shown()) for one in top
                ),
            },
        )
        return root

    def siblings(self, entries: list[Entry], parent: Ref) -> None:
        """Write one level of the outline, and every level below it.

        An entry with children states how many entries a reader shows
        when it is open, negated when it is closed, which is how PDF
        tells a collapsed entry from an expanded one.

        Args:
            entries: The entries at this level, in order.
            parent: The entry, or the root, they are under.

        """
        for index, entry in enumerate(entries):
            found: dict[str, Any] = {
                "Title": Text(entry.mark.title),
                "Parent": parent,
            }
            if index:
                found["Prev"] = entries[index - 1].ref
            if index + 1 < len(entries):
                found["Next"] = entries[index + 1].ref
            if entry.children:
                found["First"] = entry.children[0].ref
                found["Last"] = entry.children[-1].ref
                shown = entry.shown()
                found["Count"] = -shown if entry.mark.closed else shown
            found["Dest"] = self.destination(entry)
            self.writer.put(entry.ref, found)
            self.siblings(entry.children, entry.ref)

    def information(self) -> dict[str, Any]:
        """Return the information dictionary, from the header.

        The dates are the build time and not the moment of rendering,
        so rendering one printout twice gives one document.

        """
        report = self.printout.report
        found: dict[str, Any] = {}
        for key, value in (
            ("Title", report.name),
            ("Author", report.author),
            ("Subject", report.description),
        ):
            if value is not None:
                found[key] = Text(value)
        found["Creator"] = Text(self.printout.engine)
        found["Producer"] = Text(self.printout.engine)
        date = Text(pdf_date(self.printout.built))
        found["CreationDate"] = date
        found["ModDate"] = date
        return found


def flattened(page: Page) -> Iterator[tuple[str, Mark]]:
    """Yield a page's marks in paint order, an xref's nested ones after it.

    Each is named as doc/cli.md#sr-inspect names a mark, from 1
    in paint order and one inside an xref after the xref's number.

    Args:
        page: The page.

    """
    yield from nested(page.marks, f"page {page.number}, mark ")


def nested(marks: tuple[Mark, ...], prefix: str) -> Iterator[tuple[str, Mark]]:
    """Yield marks in paint order, descending into each xref after it.

    Args:
        marks: A page's marks, or an xref's.
        prefix: What goes before each mark's number.

    """
    for index, mark in enumerate(marks, start=1):
        where = f"{prefix}{index}"
        yield where, mark
        if isinstance(mark, Xref):
            yield from nested(mark.marks, f"{where}.")
