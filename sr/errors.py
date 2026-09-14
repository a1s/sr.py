"""Diagnostics: where a problem is, and what it says.

doc/template.md ends its validation section with one sentence:
"Every diagnostic names the file, the node path, and the property" --
and doc/expressions.md adds the record index for an error raised while
a band is being built.  Those four parts are what a :class:`Location` holds,
and assembling them is all this module does.

Three shapes of failure, because they behave differently:

* A **bad value** is local.  ``units`` and ``color`` raise :class:`BadValue`
  with the message and nothing else, since a parser that knew about node
  paths would be a parser the renderer could not reuse.  Whoever had the
  node attaches the location.
* A **rejected document** is a collection.  Validation runs to the end
  and reports every diagnostic it found, so one run of the tool fixes
  several mistakes; :class:`Diagnostics` accumulates them and
  :class:`TemplateError` carries them out.
* A **failed expression** is local too, and carries one thing a bad value
  does not: an offset into the expression's own text.  doc/expressions.md
  asks a compile error to name "the position within the expression",
  which is a coordinate in a string rather than in the file, so
  :class:`ExpressionError` holds it and the rest of the location
  is attached by whoever had the node.

The section a band belongs to is not a separate field.  A node path
ends up naming it (``report > layout > detail > field`` says `detail`),
so recording it twice would leave two spellings to keep in step.

"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = [
    "WARNING_KINDS",
    "BadValue",
    "BuildWarning",
    "Diagnostic",
    "Diagnostics",
    "ExpressionError",
    "Location",
    "NodePath",
    "SrError",
    "Step",
    "TemplateError",
]

# The `kind` of a warning the printout header carries, per
# doc/printout.md#header-line.  Warnings are not errors and do not change
# the exit code: they say what is wrong with a document that exists.
WARNING_KINDS = ("overflow", "glyph", "font", "unknown")


class SrError(Exception):
    """Anything this engine refuses to do, as one base class."""


class BadValue(SrError):
    """A value that is not well-formed, reported without a location.

    Raised by the value parsers, which see a string and nothing around it.
    The message is the whole diagnostic except the location, so a caller
    that knows the node needs only to prepend one.

    """


class ExpressionError(SrError):
    """An expression that would not compile, or would not evaluate.

    One class for both ends, because the difference between them is
    what the caller knows rather than what went wrong: a compile error
    happens at template load and a runtime error while a band is being
    built, and each attaches the parts of a :class:`Location` it has.
    What is held here is the part neither of them can supply -- where
    in the expression's own text the problem is.

    Attributes:
        message: What went wrong, without any location.
        offset: The 0-based character offset into the expression,
            for a problem the parser could point at.

    """

    def __init__(self, message: str, *, offset: int | None = None) -> None:
        """Carry the message, and the offset where the caller knew one.

        Args:
            message: What went wrong, without any location.
            offset: The 0-based character offset into the expression.

        """
        self.message = message
        self.offset = offset
        super().__init__(message)

    def where(self) -> str:
        """Return the offset as a diagnostic spells it, or an empty string."""
        return "" if self.offset is None else f"at offset {self.offset}"


@dataclass(frozen=True)
class Step:
    """One node on the way down to the one a diagnostic is about."""

    name: str
    identity: str | None = None

    def __str__(self) -> str:
        """Name the node, with its identity argument where it has one."""
        if self.identity is None:
            return self.name
        return f'{self.name} "{self.identity}"'


@dataclass(frozen=True)
class NodePath:
    """The chain of nodes from the document root to one node.

    Siblings are not numbered.  Two ``rectangle`` nodes under one band
    have the same path, and what tells them apart is the line -- which is
    why a :class:`Location` carries one where the reader can supply it.

    """

    steps: tuple[Step, ...] = ()

    def child(self, name: str, identity: str | None = None) -> NodePath:
        """Return the path to a child of this node."""
        return NodePath((*self.steps, Step(name, identity)))

    def __bool__(self) -> bool:
        """Report whether the path names anything at all."""
        return bool(self.steps)

    def __str__(self) -> str:
        """Join the steps the way a diagnostic spells them."""
        return " > ".join(str(step) for step in self.steps)


@dataclass(frozen=True)
class Location:
    """Where a diagnostic is, in as much detail as the caller had.

    Every part is optional and the ones that are missing leave no trace,
    so a value rejected with nothing but a file still reads as a sentence.

    Attributes:
        file: The template or data file, as the caller named it.
        path: The node path within that file.
        prop: The property on that node, written with its ``=``.
        line: The line in the file, where the reader knows it.
        record: The index of the record being formatted, from zero.

    """

    file: str | None = None
    path: NodePath | None = None
    prop: str | None = None
    line: int | None = None
    record: int | None = None

    def __str__(self) -> str:
        """Spell the location as the prefix of a diagnostic."""
        head = self.file or ""
        if self.line is not None:
            head = f"{head}:{self.line}" if head else f"line {self.line}"
        parts = [part for part in (head, str(self.path) if self.path else "") if part]
        text = ": ".join(parts)
        if self.prop:
            text = f"{text} {self.prop}=" if text else f"{self.prop}="
        if self.record is not None:
            text = f"{text}, record {self.record}" if text else f"record {self.record}"
        return text


@dataclass(frozen=True)
class Diagnostic:
    """One thing that is wrong, and where.

    Attributes:
        message: What is wrong, without its location.
        location: Where, in as much detail as the caller had.
        kind: For a warning, which of :data:`WARNING_KINDS` it is,
            so that one raised at load reaches the printout header
            under the name doc/printout.md#header-line gives it.
            An error has none: nothing carries an error into a printout,
            because a document with one is not produced.

    """

    message: str
    location: Location = Location()
    kind: str | None = None

    def at(
        self,
        *,
        file: str | None = None,
        path: NodePath | None = None,
        prop: str | None = None,
        line: int | None = None,
        record: int | None = None,
    ) -> Diagnostic:
        """Return this diagnostic with the parts given filled in.

        An argument left out leaves that part of the location as it was,
        which is what makes this the way to attach a node to a
        :class:`BadValue` raised somewhere that had none.

        Args:
            file: The document, where it was not already named.
            path: The node the diagnostic is about.
            prop: The property on that node.
            line: The line in the file.
            record: The record being formatted.

        """
        was = self.location
        return Diagnostic(
            self.message,
            Location(
                file=was.file if file is None else file,
                path=was.path if path is None else path,
                prop=was.prop if prop is None else prop,
                line=was.line if line is None else line,
                record=was.record if record is None else record,
            ),
            self.kind,
        )

    def __str__(self) -> str:
        """Spell the diagnostic as one line, location first."""
        prefix = str(self.location)
        return f"{prefix}: {self.message}" if prefix else self.message


class TemplateError(SrError):
    """A document that was refused, and every diagnostic behind it.

    One error carries the whole list because that is what the run produced:
    validation does not stop at the first mistake, so neither does the
    report.  ``str`` of it is one diagnostic per line, in the order
    they were found rather than in line order; the order of the checks
    is the order a reader can follow.

    """

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        """Carry the diagnostics, and join them as the message.

        Args:
            diagnostics: What was found, in the order it was found.

        """
        self.diagnostics = diagnostics
        super().__init__("\n".join(str(one) for one in diagnostics))


@dataclass
class Diagnostics:
    """The diagnostics one run has collected so far.

    The file is held here rather than passed to every call,
    since a collector belongs to one document and every diagnostic
    in it names the same file.

    Attributes:
        file: The document being read, named as the caller named it.
        found: What has been collected, in order.

    """

    file: str | None = None
    found: list[Diagnostic] = field(default_factory=list)

    def error(
        self,
        message: str,
        *,
        path: NodePath | None = None,
        prop: str | None = None,
        line: int | None = None,
        record: int | None = None,
        kind: str | None = None,
    ) -> Diagnostic:
        """Record one diagnostic and return it.

        Args:
            message: What is wrong, without its location.
            path: The node it is about.
            prop: The property on that node.
            line: The line in the file, where it is known.
            record: The record being formatted, where there is one.
            kind: For a warning, the printout's name for its kind.

        """
        diagnostic = Diagnostic(
            message,
            Location(file=self.file, path=path, prop=prop, line=line, record=record),
            kind,
        )
        self.found.append(diagnostic)
        return diagnostic

    def add(self, diagnostic: Diagnostic) -> Diagnostic:
        """Record a diagnostic that was built elsewhere, naming this file."""
        if diagnostic.location.file is None and self.file is not None:
            diagnostic = diagnostic.at(file=self.file)
        self.found.append(diagnostic)
        return diagnostic

    def raise_if_any(self) -> None:
        """Raise :class:`TemplateError` when anything has been collected."""
        if self.found:
            raise TemplateError(tuple(self.found))

    def __bool__(self) -> bool:
        """Report whether anything has been collected."""
        return bool(self.found)

    def __len__(self) -> int:
        """Count what has been collected."""
        return len(self.found)

    def __iter__(self) -> Iterator[Diagnostic]:
        """Iterate over what has been collected, in order."""
        return iter(self.found)

    def __str__(self) -> str:
        """Spell every diagnostic, one per line."""
        return "\n".join(str(one) for one in self.found)


@dataclass(frozen=True)
class BuildWarning:
    """Something wrong with a document that was produced anyway.

    doc/printout.md#header-line puts these in the header, so they
    survive archiving; doc/cli.md keeps them out of the exit code.
    A caller that treats warnings as failures tests the header
    rather than the status.

    Attributes:
        kind: One of :data:`WARNING_KINDS`.
        message: What happened.
        node: The node path it happened at, where there is one.
        prop: The property on that node, where it is about one.
        record: The record being formatted, where there is one.

    """

    kind: str
    message: str
    node: str | None = None
    prop: str | None = None
    record: int | None = None

    def __post_init__(self) -> None:
        """Reject a kind the printout format does not define."""
        if self.kind not in WARNING_KINDS:
            raise ValueError(
                f"unknown warning kind {self.kind!r}; "
                f"expected one of {', '.join(WARNING_KINDS)}"
            )

    def __str__(self) -> str:
        """Spell the warning the way the command line reports one."""
        node = f"{self.node} {self.prop}=" if self.node and self.prop else self.node
        parts = [node] if node else []
        if self.record is not None:
            parts.append(f"record {self.record}")
        where = f" ({', '.join(parts)})" if parts else ""
        return f"{self.kind}: {self.message}{where}"
