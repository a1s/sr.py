"""The KDL layer: typed property access, node paths, cardinality.

``ckdl`` reads the document; this module is everything between that
and a loader.  Three jobs, and they are here rather than in
``template/load.py`` because each of them is about the shape
of a KDL document rather than about what a report is:

* **A value is asked for by type.**  ``node.dimension("width")``
  either returns points or files a diagnostic and returns the default,
  so a loader reads as a list of the properties a node has rather than
  as a chain of type tests.
* **Every node knows its path.**  ``report > layout > detail > field``
  is built as the tree is walked, so a diagnostic raised four levels down
  names itself without anything having to pass a breadcrumb along.
* **Nothing stops at the first mistake.**  Diagnostics accumulate
  in one collector and the caller raises at the end, which is what lets
  one run of ``sr.py validate`` report every problem in a template
  rather than the first.

A parse error is the exception to the last of those:
after it there is no tree to walk, so it raises.

``ckdl`` reports no line or column, so a diagnostic from here names
the file and the node path and stops there.  doc/template.md asks
for exactly those, so this is a loss of polish rather than of conformance --
a second ``rectangle`` in a band is told apart by its path and its properties
rather than by a line.  :class:`~sr.errors.Location` carries a line for the day
a reader supplies one.

"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import ckdl

from sr.color import parse_color
from sr.errors import (
    BadValue,
    Diagnostic,
    Diagnostics,
    Location,
    NodePath,
    Step,
    TemplateError,
)
from sr.units import parse_dimension

__all__ = ["Document", "Node", "Value", "parse", "read"]

# doc/template.md#parser-requirements: KDL v2 specifically.
# Not "any", which would read a v1 document as far as its
# first v2-only spelling and then fail somewhere unrelated.
KDL_VERSION = 2

# What a document-level diagnostic names instead of a node.
DOCUMENT = "<document>"

# What a document parsed from a string in memory is called.
UNNAMED = "<text>"

# Every value a KDL property or argument can hold, once a type annotation
# has been unwrapped.  KDL calls an annotation a hint, and nothing in the
# template format reads one, so `size=(u8)10` is the integer 10.
Value = str | int | float | bool | None

# What a value parser takes, and what it returns: the accessor
# that runs one passes the first through and hands back the second.
Given = TypeVar("Given")
Parsed = TypeVar("Parsed")


def kind_of(value: Value) -> str:
    """Name the kind of a KDL value, as a diagnostic spells it.

    The order is the order of the tests: a ``bool`` is an ``int``
    to Python, and is a boolean and not an integer to KDL.

    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    return "number"


def unwrap(value: object) -> Value:
    """Return a ckdl value with any type annotation dropped.

    Args:
        value: What ``ckdl`` produced for an argument or a property.

    """
    if isinstance(value, ckdl.Value):
        return unwrap(value.value)
    if isinstance(value, str | bool | int | float) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class Node:
    """One KDL node, with the path that names it and somewhere to complain.

    Attributes:
        name: The node name.
        args: Its positional arguments, in order.
        properties: Its named properties; KDL gives a repeated one last-wins.
        children: Its child nodes, in document order.
        path: Where this node sits, for every diagnostic about it.
        diagnostics: The collector for the document it came from.

    """

    name: str
    args: tuple[Value, ...]
    properties: dict[str, Value]
    children: tuple[Node, ...]
    path: NodePath
    diagnostics: Diagnostics

    @property
    def identity(self) -> str | None:
        """The node's identity argument, where it has one.

        doc/template.md#kdl-conventions makes a node's identity its first
        positional argument -- ``font "body"`` -- so that is what a path
        shows.  A node whose first argument is not a string has none.

        """
        first = self.args[0] if self.args else None
        return first if isinstance(first, str) else None

    def error(self, message: str, prop: str | None = None) -> Diagnostic:
        """Record a diagnostic about this node.

        Args:
            message: What is wrong, without its location.
            prop: The property it is about, where it is about one.

        """
        return self.diagnostics.error(message, path=self.path, prop=prop)

    # -- values -------------------------------------------------------

    def has(self, prop: str) -> bool:
        """Report whether the node carries the property at all."""
        return prop in self.properties

    def raw(self, prop: str, *, required: bool = False) -> Value:
        """Return a property's value as it stands.

        None means either that the property is absent or that it is
        written ``#null``; :meth:`has` is what tells those apart,
        and the typed accessors use it so that a null reads as the
        wrong type rather than as nothing at all.

        Args:
            prop: The property name.
            required: Whether its absence is a diagnostic.

        """
        return self.given(prop, required)[1]

    def given(self, prop: str, required: bool) -> tuple[bool, Value]:
        """Return whether the property is there, and its value if it is.

        Args:
            prop: The property name.
            required: Whether its absence is a diagnostic.

        """
        if self.has(prop):
            return True, self.properties[prop]
        if required:
            self.error("required", prop)
        return False, None

    def string(
        self, prop: str, *, default: str | None = None, required: bool = False
    ) -> str | None:
        """Return a string property.

        Args:
            prop: The property name.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        present, value = self.given(prop, required)
        if not present:
            return default
        if not isinstance(value, str):
            self.error(f"want a string, got {kind_of(value)}", prop)
            return default
        return value

    def integer(
        self, prop: str, *, default: int | None = None, required: bool = False
    ) -> int | None:
        """Return an integer property.

        Args:
            prop: The property name.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        present, value = self.given(prop, required)
        if not present:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self.error(f"want an integer, got {kind_of(value)}", prop)
            return default
        return value

    def boolean(
        self, prop: str, *, default: bool | None = None, required: bool = False
    ) -> bool | None:
        """Return a boolean property.

        Args:
            prop: The property name.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        present, value = self.given(prop, required)
        if not present:
            return default
        if not isinstance(value, bool):
            self.error(f"want #true or #false, got {kind_of(value)}", prop)
            return default
        return value

    def dimension(
        self, prop: str, *, default: float | None = None, required: bool = False
    ) -> float | None:
        """Return a dimension property, in points and already rounded.

        Args:
            prop: The property name.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        present, value = self.given(prop, required)
        if not present:
            return default
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            self.error(f"want a dimension, got {kind_of(value)}", prop)
            return default
        return self.parsed(parse_dimension, value, prop, default)

    def color(
        self, prop: str, *, default: str | None = None, required: bool = False
    ) -> str | None:
        """Return a colour property, canonicalised to ``"#RRGGBB"``.

        Args:
            prop: The property name.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        text = self.string(prop, required=required)
        if text is None:
            return default
        return self.parsed(parse_color, text, prop, default)

    def enum(
        self,
        prop: str,
        allowed: Iterable[str],
        *,
        default: str | None = None,
        required: bool = False,
    ) -> str | None:
        """Return a property whose value is one of a fixed set.

        Args:
            prop: The property name.
            allowed: The values the property takes, in any order:
                they are held as a set, and the diagnostic sorts them.
            default: What to return when it is absent or refused.
            required: Whether its absence is a diagnostic.

        """
        values = frozenset(allowed)
        text = self.string(prop, required=required)
        if text is None:
            return default
        if text not in values:
            wanted = " ".join(sorted(values))
            self.error(f'unknown value "{text}"; want one of: {wanted}', prop)
            return default
        return text

    def parsed(
        self,
        parser: Callable[[Given], Parsed],
        value: Given,
        prop: str,
        default: Parsed | None,
    ) -> Parsed | None:
        """Run a value parser, turning its refusal into a diagnostic.

        ``units`` and ``color`` know nothing of nodes,
        so this is where what they raise acquires a location.

        Args:
            parser: The parser to call with ``value``.
            value: What to give it.
            prop: The property it came from.
            default: What to return when the parser refuses.

        """
        try:
            return parser(value)
        except BadValue as refused:
            self.diagnostics.add(
                Diagnostic(str(refused), Location(path=self.path, prop=prop))
            )
            return default

    def known_properties(self, *known: str) -> None:
        """Report every property that is not one of ``known``.

        Args:
            *known: The properties this node takes.

        """
        for prop in self.properties:
            if prop not in known:
                self.error("unknown property", prop)

    # -- children -----------------------------------------------------

    def each(self, name: str) -> tuple[Node, ...]:
        """Return this node's children with the given name, in order."""
        return tuple(child for child in self.children if child.name == name)

    def child(self, name: str) -> Node | None:
        """Return the one child with the given name, reporting if it is not.

        Args:
            name: The child node's name.

        """
        found = self.each(name)
        if len(found) != 1:
            target = found[1] if len(found) > 1 else self
            target.error(f"a {self.name} has exactly one `{name}`")
        return found[0] if found else None

    def optional_child(self, name: str) -> Node | None:
        """Return the child with the given name, where there is at most one.

        Args:
            name: The child node's name.

        """
        found = self.each(name)
        if len(found) > 1:
            found[1].error(f"at most one {name} is allowed here")
        return found[0] if found else None

    def known_children(self, *known: str) -> None:
        """Report every child whose name is not one of ``known``.

        Args:
            *known: The nodes this one accepts, in the order to list them.

        """
        for child in self.children:
            if child.name not in known:
                child.error(
                    f"unexpected node here; {self.name} accepts: {' '.join(known)}"
                )


@dataclass(frozen=True)
class Document:
    """A parsed KDL document, and what reading it has had to say.

    Attributes:
        file: The document, named as the caller named it.
        nodes: Its root nodes, in order.
        diagnostics: Everything reading it has collected so far.

    """

    file: str
    nodes: tuple[Node, ...]
    diagnostics: Diagnostics

    @property
    def path(self) -> NodePath:
        """What a diagnostic about the document itself names."""
        return NodePath((Step(DOCUMENT),))

    def only_root(self, name: str) -> Node | None:
        """Return the single root node, which must have the given name.

        Args:
            name: The node a template's root must be.

        """
        if len(self.nodes) == 1 and self.nodes[0].name == name:
            return self.nodes[0]
        self.diagnostics.error(
            f"a template's root node is a single `{name}`", path=self.path
        )
        return None


def convert(node: ckdl.Node, parent: NodePath, diagnostics: Diagnostics) -> Node:
    """Turn one ckdl node and its subtree into nodes of our own.

    Args:
        node: What ``ckdl`` parsed.
        parent: The path of the node above it.
        diagnostics: The collector for this document.

    """
    args = tuple(unwrap(argument) for argument in node.args)
    first = args[0] if args else None
    path = parent.child(node.name, first if isinstance(first, str) else None)
    return Node(
        name=node.name,
        args=args,
        properties={name: unwrap(value) for name, value in node.properties.items()},
        children=tuple(convert(child, path, diagnostics) for child in node.children),
        path=path,
        diagnostics=diagnostics,
    )


def parse(text: str, file: str = UNNAMED) -> Document:
    """Parse KDL v2 text into a document.

    Args:
        text: The document.
        file: What to name it in diagnostics.

    Raises:
        TemplateError: The text is not KDL v2.  Nothing accumulates here:
            a document that did not parse has no tree to go on reading.

    """
    diagnostics = Diagnostics(file=file)
    try:
        parsed = ckdl.parse(text, version=KDL_VERSION)
    except ckdl.ParseError as refused:
        raise TemplateError(
            (Diagnostic(f"parse error: {refused}", Location(file=file)),)
        ) from refused
    return Document(
        file=file,
        nodes=tuple(convert(node, NodePath(), diagnostics) for node in parsed.nodes),
        diagnostics=diagnostics,
    )


def read(path: Path | str) -> Document:
    """Read a KDL v2 document from a file.

    Args:
        path: The file to read.

    Raises:
        TemplateError: The file is not UTF-8, or is not KDL v2.

    """
    file = str(path)
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as refused:
        raise TemplateError(
            (Diagnostic(f"not UTF-8: {refused}", Location(file=file)),)
        ) from refused
    return parse(text, file=file)
