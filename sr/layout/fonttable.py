"""The printout's font table, named as the build first uses each font.

doc/printout.md#fonts lists a font once it is used, under the name
its template gave it, and never merges two fonts of different names
however alike they resolve.  With one template that is the whole rule:
the names are unique already, since validation says so.

A [subreport](doc/layout.md#subreports) named by ``template=`` brings
fonts of its own, and the document still has one table, so two fonts
can arrive under one name.  Where they write the same entry, every field
equal, they are one entry, since sharing it renames nothing.  Where they
differ, the font used first keeps the name and the other is published
under the first of ``-2``, ``-3`` and so on after the name that the table
does not hold for something else.  Unlike a generated blob name,
the suffix steps around only the names published so far, not those
another template declares: a child's own ``x-2`` can come out as
``x-2-2``. A text mark names the font it was set in by the name
it was published under.

"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from sr.errors import BuildWarning
from sr.fonts.resolve import Resolution
from sr.printout.model import FontEntry

__all__ = ["FontTable", "font_entry"]


@dataclass
class FontTable:
    """The fonts the build has used, by the names they are published under.

    Attributes:
        entries: What the header records, by published name,
            in the order the names were first used.
        names: The name each font was published under,
            by its document and the name its template gave it.
        documents: Each document's resolutions, in the order
            the documents first measured something, for the warnings.

    """

    entries: dict[str, FontEntry] = field(default_factory=dict)
    names: dict[tuple[int, str], str] = field(default_factory=dict)
    documents: list[tuple[int, tuple[Resolution, ...]]] = field(default_factory=list)

    def document(self, owner: object, resolutions: tuple[Resolution, ...]) -> None:
        """Note a document whose fonts may be published.

        Args:
            owner: What stands for the document,
                kept for as long as the build runs.
            resolutions: Its fonts, resolved, in declaration order.

        """
        self.documents.append((id(owner), resolutions))

    def publish(
        self, owner: object, resolution: Resolution, data: str | None = None
    ) -> str:
        """Return the name a font is published under, publishing it once.

        Args:
            owner: What stands for the document that declares it.
            resolution: What resolving its `font` node produced.
            data: The `data` entry its bytes are stored under,
                for a font read out of a `data` node.

        """
        key = (id(owner), resolution.font.name)
        found = self.names.get(key)
        if found is not None:
            return found
        entry = font_entry(resolution, data)
        base = entry.name
        name = base
        suffix = 2
        while name in self.entries and replace(self.entries[name], name=base) != entry:
            name = f"{base}-{suffix}"
            suffix += 1
        self.entries.setdefault(name, replace(entry, name=name))
        self.names[key] = name
        return name

    def listed(self) -> tuple[FontEntry, ...]:
        """Return the published entries, each once, in declaration order.

        Document by document, in the order each first measured something,
        and each document's in the order its `font` nodes are declared.
        The printout writes them sorted by name; this is the order
        the in-memory printout keeps.

        """
        found: dict[str, FontEntry] = {}
        for owner, resolutions in self.documents:
            for one in resolutions:
                name = self.names.get((owner, one.font.name))
                if name is not None:
                    found.setdefault(name, self.entries[name])
        return tuple(found.values())

    def warnings(self) -> tuple[BuildWarning, ...]:
        """Return what resolving the published fonts had to say.

        A font's own warnings are carried only where the table lists it,
        so no warning names a font the document does not have.  They come
        document by document, each in the order its fonts are declared.

        """
        found: list[BuildWarning] = []
        for owner, resolutions in self.documents:
            for one in resolutions:
                if (owner, one.font.name) in self.names:
                    found.extend(one.warnings)
        return tuple(found)


def font_entry(resolution: Resolution, data: str | None = None) -> FontEntry:
    """Return one resolved font as the printout's header records it.

    Args:
        resolution: What the resolution chain produced.
        data: The `data` entry a font read out of a `data` node
            is stored under, which is another node's name where
            an entry built earlier holds the same bytes.

    """
    font = resolution.font
    origin = resolution.origin
    return FontEntry(
        name=font.name,
        size=font.size,
        bold=font.bold,
        italic=font.italic,
        underline=font.underline,
        face=resolution.face.family,
        step=resolution.step,
        requested=resolution.requested,
        file=Path(origin.path) if origin.path is not None else None,
        data=data if data is not None else origin.data,
        index=origin.index,
    )
