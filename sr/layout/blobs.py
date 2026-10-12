"""The printout's `data` table, filled as the build meets its blobs.

doc/printout.md#data is the contract.  An entry comes from a `data` node,
which keeps its declared name, from a font read out of one, or from an
image given as `file=` or as a `content` child, which the engine names:
a file by its base name, a `content` child `image`.  Four rules decide
the names, and each is visible in :class:`DataTable`.

* **Blobs are shared on their content.**  Identical bytes are one entry
  however many elements, files or nodes they came from.
* **A declared name outranks a generated one.**  A generated blob whose
  bytes some `data` node holds takes that node's name, whatever the order
  the document mentions the two in.
* **Otherwise the first mention wins**, in the order elements are built.
  A band is built each time it is measured, a header's or a footer's
  reservation included, so a name can be taken by an image whose band
  never reaches the page.
* **Declared names are reserved from the start**, referenced or not,
  and a generated name that is taken gets ``-2``, ``-3`` and so on
  after the whole name until it is free.

A [subreport](doc/layout.md#subreports) named by ``template=``
brings `data` nodes of its own, and the document still has one table.
Every document's declared names are reserved from the start, the
subreports' along with the report's, so a generated name steps around
them all. Two documents may declare one name for different bytes, and
then the node built first holds the name and the other is suffixed as
a generated name would be.  Nothing a declared node is named by depends
on which document declared it, only on its name and its bytes.

Which entries are written is a separate question with a separate answer,
since a name can be taken without its entry being needed: only those
something in the finished document refers to.

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["DataTable"]


class DataTable:
    """The names the build has given its blobs, and their contents.

    Attributes:
        declared: The report's `data` nodes' contents, by name,
            in document order.
        taken: Every name spoken for: the declared ones,
            every document's, and every one generated since.

    """

    def __init__(self, declared: Mapping[str, bytes]) -> None:
        """Start a table with the report's declared names reserved.

        Args:
            declared: The `data` nodes' contents, by name,
                in document order.

        """
        self.declared = dict(declared)
        self.taken = set(self.declared)
        self.names: dict[bytes, str] = {}
        self.contents: dict[str, bytes] = {}
        # The first declared node holding each content, so that a generated
        # blob finds the declared name it must take without a search.
        self.holders: dict[bytes, str] = {}
        for name, content in self.declared.items():
            self.holders.setdefault(content, name)

    def reserve(self, names: Iterable[str], contents: Mapping[str, bytes]) -> None:
        """Reserve another document's declared names.

        Called for each template a subreport names, before the build
        starts, so that what is reserved is the same whichever of them
        runs first.  Its contents are known only where a node holds
        them literally, since an ``expr`` is evaluated when it runs.

        Args:
            names: Every `data` node name the document declares.
            contents: The contents its literal nodes hold, by name,
                in document order.

        """
        self.taken.update(names)
        for name, content in contents.items():
            self.holders.setdefault(content, name)

    def named(self, name: str, content: bytes | None = None) -> str:
        """Return the entry a `data` node's content is stored under.

        That is the node's own name, unless an entry built earlier
        already holds the same bytes: one content, one entry.
        And unless an entry built earlier holds that name
        for other bytes, which a node of another document can:
        then it is suffixed, as a generated name is.

        Args:
            name: The node's name.
            content: Its bytes; the report's own node's by default.

        """
        if content is None:
            content = self.declared[name]
        found = self.names.get(content)
        if found is not None:
            return found
        if name not in self.contents:
            return self.enter(name, content)
        return self.enter(self.free(name), content)

    def generated(self, base: str, content: bytes) -> str:
        """Return the entry an unnamed blob is stored under.

        Args:
            base: The name it would have if it were free:
                a file's base name, or ``image`` for a `content` child.
            content: The bytes.

        """
        found = self.names.get(content)
        if found is not None:
            return found
        holder = self.holders.get(content)
        if holder is not None:
            return self.named(holder, content)
        return self.enter(self.free(base), content)

    def free(self, base: str) -> str:
        """Return the first of a name and its suffixed forms nothing has.

        Args:
            base: The name it would have if it were free.

        """
        name = base
        suffix = 2
        while name in self.taken or name in self.contents:
            name = f"{base}-{suffix}"
            suffix += 1
        self.taken.add(name)
        return name

    def enter(self, name: str, content: bytes) -> str:
        """Record an entry, and return its name.

        Args:
            name: The name, already free or declared for this content.
            content: The bytes.

        """
        self.names[content] = name
        self.contents[name] = content
        return name

    def written(self, names: Iterable[str]) -> dict[str, bytes | str]:
        """Return the entries the finished document refers to.

        Args:
            names: The entries something in it names, in any order
                and with repeats.

        """
        return {name: self.contents[name] for name in sorted(set(names))}
