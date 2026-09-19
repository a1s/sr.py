"""A KDL document becomes a model.

One walk down the tree, building the dataclasses of ``model`` and filing
a diagnostic wherever a node does not say what it should.  Nothing here
stops at the first mistake: every accessor takes a default, so a refused
property leaves a usable node behind and the walk carries on.
What comes back is a :class:`Loaded` holding the report, the errors
and the warnings, and it is the caller that decides whether to raise.

Three things happen here that are not simply copying values across.

* **Expressions are compiled**, not stored as text, because
  doc/template.md#validation asks that they parse and because
  every later stage wants the compiled form anyway.  One that
  will not compile is a diagnostic and a ``None`` in its place.

* **Referenced templates are read.**  ``subreport template=`` "is read
  when this template is read", so a ``.kdl`` named by one is loaded
  depth first and hangs off the node.  A file named twice is read once;
  a file that reaches itself is refused, which is what the reachability
  set on each cached entry is for.

* **``embedded`` names are resolved once.**  The scope is lexical and
  runs outward (the layouts declared beside the one a `subreport` is
  written in, then those of each enclosing layout), so the loader
  carries that chain down and records the qualified name it resolved to.
  A name is resolved at load "so the check and the build always mean the
  same layout by it", and a qualified name is what keeps that true when
  two unrelated layouts each nest a private one of the same name.

Validation proper is not here.  What this module reports is what it
cannot get past: a value of the wrong type, a node in the wrong place,
an expression that will not parse.  Everything that needs the whole
document -- a `style` naming a font, an `arg` naming a parameter --
belongs to ``validate``, and runs once this has built something to check.

"""

from __future__ import annotations

import base64
import binascii
import gzip
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sr import kdl
from sr.errors import Diagnostic, ExpressionError, TemplateError
from sr.expr import Expression, compile_expression
from sr.template import model
from sr.template.model import (
    ALIGNS,
    BARCODE_TYPES,
    CALCS,
    COMPRESSIONS,
    DASHES,
    EJECT_TYPES,
    ENCODINGS,
    HALIGNS,
    IMAGE_SCALES,
    IMAGE_TYPES,
    SCOPES,
    SECTIONS,
    VALIGNS,
    VALUE_TYPES,
    XREF_TYPES,
    Arg,
    Barcode,
    Blob,
    Box,
    Columns,
    Eject,
    Element,
    Embedded,
    Field,
    Font,
    Group,
    Image,
    Layout,
    Line,
    Member,
    Nesting,
    Outline,
    Paper,
    Parameter,
    Records,
    Rectangle,
    Report,
    Section,
    Style,
    Subreport,
    Variable,
    Xref,
)

__all__ = [
    "Loaded",
    "Loader",
    "Options",
    "Scope",
    "bands_of",
    "elements_of",
    "levels_of",
    "load",
    "load_text",
    "read",
    "sections_of",
    "subreports_of",
    "xrefs_of",
]

# The geometry every box takes, plus the two aliases and the two clamps.
# `width` is missing on purpose: a `line` and a `rectangle` spend it
# on the pen instead, so it is named per element kind rather than here.
GEOMETRY = ("left", "right", "top", "bottom", "height", "x", "y")
CLAMPS = ("maxwidth", "maxheight")

# Alignment is not on every element.  A `line` and a `rectangle` have
# no content to position inside their box, and doc/template.md lists
# the two properties for the three elements that do.
ALIGNMENT = ("halign", "valign")

# What every body element takes besides its own properties.
COMMON = (*GEOMETRY, *CLAMPS, "float", "printwhen")

# The body elements, in the order doc/template.md lists them.
BODY = ("field", "line", "rectangle", "image", "barcode")

# What a section accepts as a child, in the order the document model
# lists them: the formatting rules first, then the content.
SECTION_CHILDREN = ("style", "eject", "outline", *BODY, "xref", "subreport")

# The narrow bar width a `barcode` takes when it names none: "10mil".
DEFAULT_MODULE = 0.72


@dataclass(frozen=True)
class Scope:
    """One link of the chain an ``embedded`` name is resolved along.

    Attributes:
        owner: The qualified name of the layout that declares these,
            empty for the report's own ``layout``.
        names: What it declares, in document order.

    """

    owner: tuple[str, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class Loaded:
    """A template that was read, and everything reading it had to say.

    Attributes:
        report: The model, or ``None`` where the document was refused
            before one could be built.
        errors: What makes the template invalid, in the order found.
        warnings: What is wrong with a template that is usable anyway.

    """

    report: Report | None
    errors: tuple[Diagnostic, ...]
    warnings: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        """Report whether the template loaded and validated."""
        return self.report is not None and not self.errors

    def require(self) -> Report:
        """Return the report, raising when it did not load.

        Raises:
            TemplateError: The template is invalid, with every diagnostic.

        """
        if self.report is None or self.errors:
            raise TemplateError(self.errors)
        return self.report


def load(path: Path | str, options: Options | None = None) -> Loaded:
    """Read and validate a template, and everything it names.

    Args:
        path: The ``.kdl`` file to read.
        options: What to ask of the load; the defaults otherwise.

    """
    loader = Loader(options)
    named = Path(path)
    return checked(loader, loader.reading(named, named.resolve()))


def load_text(
    text: str,
    file: str = kdl.UNNAMED,
    basedir: Path | None = None,
    options: Options | None = None,
) -> Loaded:
    """Read and validate a template held in memory.

    For a test, and for a caller that has the document rather than a
    path.  A ``subreport template=`` in it resolves against ``basedir``
    like any other, since a template in memory still names files on disk.

    Args:
        text: The document.
        file: What to name it in diagnostics.
        basedir: What relative paths resolve against; the working
            directory when the caller names none.
        options: What to ask of the load; the defaults otherwise.

    """
    loader = Loader(options)
    return checked(loader, loader.text(text, file, basedir or Path()))


def read(path: Path | str, options: Options | None = None) -> Report:
    """Read a template, raising on anything wrong with it.

    Args:
        path: The ``.kdl`` file to read.
        options: What to ask of the load; the defaults otherwise.

    Raises:
        TemplateError: The template is invalid, with every diagnostic.

    """
    return load(path, options).require()


def checked(loader: Loader, report: Report | None) -> Loaded:
    """Validate what a loader built, and gather both sets of diagnostics.

    Validation is imported here rather than at the top because it reads
    this module's tree walkers, and the two would otherwise import each
    other.  It is one cycle, and this is the end of it that can wait.

    Args:
        loader: The load that produced the report, holding its diagnostics.
        report: What it built, or ``None``.

    """
    from sr.template.validate import validate

    errors = list(loader.diagnostics)
    warnings: list[Diagnostic] = list(loader.warnings)
    if report is not None:
        found, warned = validate(report)
        errors.extend(found)
        warnings.extend(warned)
    return Loaded(report, tuple(errors), tuple(warnings))


@dataclass(frozen=True)
class Options:
    """What a caller asks of a load beyond the file to read.

    Both are doc/template.md#unknown-names', and both reach every template
    a load touches, including the ones a ``subreport template=`` pulls in:
    a name is deliberate or it is not, and that does not change with the
    file it appears in.

    Attributes:
        accepted: Names to take in silence wherever they appear,
            as ``--accept`` supplies them.  A template's own ``accept``
            adds to this rather than replacing it.
        strict: Whether an unknown name is an error rather than a warning.

    """

    accepted: frozenset[str] = frozenset()
    strict: bool = False


class Loader:
    """One load, and everything it has read so far.

    A load spans more than one file, because a ``subreport template=``
    pulls another in, so the cache, the cycle check and the collected
    diagnostics live here rather than in a free function.

    Attributes:
        options: What the caller asked of this load.
        diagnostics: Everything found, across every file, in order.
        warnings: What was found that does not refuse the template.

    """

    def __init__(self, options: Options | None = None) -> None:
        """Start a load with nothing read.

        Args:
            options: What the caller asks of it; the defaults otherwise.

        """
        self.options = options or Options()
        self.diagnostics: list[Diagnostic] = []
        self.warnings: list[Diagnostic] = []
        # Resolved path -> the report it holds.  A file named twice
        # is read once, which is also what makes two references to one
        # subreport the same object downstream.
        self.cache: dict[Path, Report | None] = {}
        # Resolved path -> every template reachable from it, itself
        # included.  A cycle is a reference whose reachable set meets the
        # stack, which is the test a cache alone cannot make: a template
        # already read is not re-walked, so the loop would not be seen.
        self.reaches: dict[Path, frozenset[Path]] = {}
        # The files being read right now, outermost first.
        self.stack: list[Path] = []

    # -- documents ----------------------------------------------------

    def document(self, path: Path) -> Report | None:
        """Read one template file.

        Args:
            path: The file to read.

        """
        try:
            document = kdl.read(path)
        except TemplateError as refused:
            self.diagnostics.extend(refused.diagnostics)
            return None
        except OSError as refused:
            message = refused.strerror or str(refused)
            self.diagnostics.append(
                Diagnostic(f"cannot read: {message}").at(file=str(path))
            )
            return None
        return self.build(document, path.resolve().parent)

    def text(self, text: str, file: str, basedir: Path) -> Report | None:
        """Read one template from a string.

        Args:
            text: The document.
            file: What to name it in diagnostics.
            basedir: What relative paths in it resolve against.

        """
        try:
            document = kdl.parse(text, file=file)
        except TemplateError as refused:
            self.diagnostics.extend(refused.diagnostics)
            return None
        return self.build(document, basedir)

    def build(self, document: kdl.Document, basedir: Path) -> Report | None:
        """Turn a parsed document into a report.

        Args:
            document: What the KDL layer produced.
            basedir: The directory the file was read from,
                which is the default for ``basedir``
                and what a relative one resolves against.

        """
        root = document.only_root("report")
        if root is None:
            self.diagnostics.extend(document.diagnostics)
            return None
        self.accepts(root, document.names)
        report = self.report(root, document.file, basedir)
        self.diagnostics.extend(document.diagnostics)
        self.warnings.extend(document.warnings)
        return report

    def accepts(self, root: kdl.Node, names: kdl.Names) -> None:
        """Fill in a document's unknown-name policy before its tree is walked.

        doc/template.md#accept: the names add up across every ``accept``
        node, and what the caller supplied adds to those, so a name
        registered either way is taken in silence wherever it appears.
        Every name is collected before any is checked, so that an
        ``accept`` node's own properties are read under the finished
        policy rather than under half of it.

        Args:
            root: The document's ``report`` node.
            names: Its policy, to fill in.

        """
        nodes = root.each("accept")
        names.accepted |= self.options.accepted
        names.strict = self.options.strict
        for node in nodes:
            names.accepted.update(one for one in node.args if isinstance(one, str))
        for node in nodes:
            node.known_properties()
            node.known_children()
            if not node.args:
                node.error("an `accept` names at least one node or property")
            for argument in node.args:
                if not isinstance(argument, str):
                    node.error(
                        f"an `accept` name is a string, not {kdl.kind_of(argument)}"
                    )

    # -- the report ---------------------------------------------------

    def report(self, node: kdl.Node, file: str, directory: Path) -> Report:
        """Build the root node.

        Args:
            node: The ``report`` node.
            file: The document, as the caller named it.
            directory: The directory it was read from.

        """
        node.known_properties("name", "description", "version", "author", "basedir")
        node.known_children(
            "accept", "parameter", "records", "variable", "font", "data", "layout"
        )
        written = node.string("basedir")
        basedir = directory if written is None else (directory / written).resolve()
        layouts: dict[tuple[str, ...], Embedded] = {}
        found = node.child("layout")
        return Report(
            file=file,
            basedir=basedir,
            name=node.string("name"),
            description=node.string("description"),
            version=node.string("version"),
            author=node.string("author"),
            parameters=tuple(self.parameter(child) for child in node.each("parameter")),
            records=self.records(node),
            variables=tuple(self.variable(child) for child in node.each("variable")),
            fonts=tuple(self.font(child) for child in node.each("font")),
            data=tuple(self.blob(child) for child in node.each("data")),
            layout=None if found is None else self.layout(found, basedir, layouts),
            layouts=layouts,
        )

    def identity(self, node: kdl.Node) -> str:
        """Return a node's identity argument, reporting where it has none.

        Args:
            node: A node whose first positional argument names it.

        """
        if len(node.args) > 1:
            node.error(f"a {node.name} takes one name, not {len(node.args)}")
        name = node.identity
        if name is None:
            node.error(f"a {node.name} needs a name as its first argument")
            return ""
        return name

    def parameter(self, node: kdl.Node) -> Parameter:
        """Build a ``parameter`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("type", "default", "defaultexpr", "format", "prompt")
        node.known_children()
        kind = node.enum("type", VALUE_TYPES, default="string")
        default = node.string("default")
        layout = node.string("format")
        value: Any = None
        if default is not None:
            value = node.parsed(
                lambda text: model.parse_text(kind, text, layout),
                default,
                "default",
                None,
            )
        return Parameter(
            name=self.identity(node),
            kind=kind,
            default=default,
            value=value,
            defaultexpr=self.expression(node, "defaultexpr"),
            format=layout,
            prompt=node.boolean("prompt", default=False),
            path=node.path,
        )

    def records(self, node: kdl.Node) -> Records | None:
        """Build the ``records`` node of a report or an embedded layout.

        Args:
            node: The node that may carry one.

        """
        found = node.optional_child("records")
        if found is None:
            return None
        found.known_properties()
        found.known_children("member")
        return Records(
            members=tuple(self.member(child) for child in found.each("member")),
            path=found.path,
        )

    def member(self, node: kdl.Node) -> Member:
        """Build a ``member`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("type", "nullable", "format")
        node.known_children()
        return Member(
            name=self.identity(node),
            kind=node.enum("type", VALUE_TYPES, default="string"),
            nullable=node.boolean("nullable", default=False),
            format=node.string("format"),
            path=node.path,
        )

    def variable(self, node: kdl.Node) -> Variable:
        """Build a ``variable`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(
            "expr", "init", "calc", "iter", "itergrp", "reset", "resetgrp"
        )
        node.known_children()
        return Variable(
            name=self.identity(node),
            expr=self.expression(node, "expr", required=True),
            init=self.expression(node, "init"),
            calc=node.enum("calc", CALCS, default="first"),
            iterate=node.enum("iter", SCOPES, default="detail"),
            itergrp=node.string("itergrp"),
            reset=node.enum("reset", SCOPES, default="report"),
            resetgrp=node.string("resetgrp"),
            path=node.path,
        )

    def font(self, node: kdl.Node) -> Font:
        """Build a ``font`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(
            "typeface", "file", "data", "size", "bold", "italic", "underline"
        )
        node.known_children()
        return Font(
            name=self.identity(node),
            typeface=node.string("typeface"),
            file=node.string("file"),
            data=node.string("data"),
            size=node.integer("size", default=0, required=True),
            bold=node.boolean("bold", default=False),
            italic=node.boolean("italic", default=False),
            underline=node.boolean("underline", default=False),
            path=node.path,
        )

    def blob(self, node: kdl.Node) -> Blob:
        """Build a ``data`` node, decoding its content.

        Args:
            node: The node to read.

        """
        node.known_properties("encoding", "compress", "expr")
        node.known_children("content")
        encoding = node.enum("encoding", ENCODINGS)
        compress = node.enum("compress", COMPRESSIONS)
        text = self.content(node)
        return Blob(
            name=self.identity(node),
            encoding=encoding,
            compress=compress,
            expr=self.expression(node, "expr"),
            content=(
                None if text is None else self.decode(node, text, encoding, compress)
            ),
            has_content=text is not None,
            path=node.path,
        )

    def content(self, node: kdl.Node) -> str | None:
        """Return the text of a node's ``content`` child, where it has one.

        Args:
            node: A ``data`` or an ``image`` node.

        """
        found = node.each("content")
        if not found:
            return None
        if len(found) > 1:
            found[1].error("at most one content is allowed here")
        child = found[0]
        child.known_properties()
        child.known_children()
        if len(child.args) != 1 or not isinstance(child.args[0], str):
            child.error("a content holds one string")
            return None
        return child.args[0]

    def decode(
        self, node: kdl.Node, text: str, encoding: str | None, compress: str | None
    ) -> bytes | None:
        """Return the bytes a ``content`` spells, decoded and decompressed.

        Args:
            node: The node the content belongs to, for the diagnostic.
            text: The content as the template wrote it.
            encoding: ``base64``, or ``None`` for literal text.
            compress: ``zlib``, ``gzip``, or ``None``.

        """
        if encoding == "base64":
            # A long blob is a multi-line string, so it arrives with
            # the line breaks the template laid it out on.  Those are
            # not base64 and are dropped; anything else that is not base64
            # is a corrupt blob and is reported rather than skipped over.
            try:
                raw = base64.b64decode("".join(text.split()), validate=True)
            except (binascii.Error, ValueError) as refused:
                node.error(f"content is not base64: {refused}")
                return None
        else:
            raw = text.encode("utf-8")
        if compress is None:
            return raw
        try:
            if compress == "zlib":
                return zlib.decompress(raw)
            return gzip.decompress(raw)
        except (zlib.error, OSError, EOFError) as refused:
            node.error(f"content is not {compress}: {refused}")
            return None

    # -- expressions --------------------------------------------------

    def expression(
        self, node: kdl.Node, prop: str, *, required: bool = False
    ) -> Expression | None:
        """Compile an expression property.

        Args:
            node: The node it is written on.
            prop: The property name.
            required: Whether its absence is a diagnostic.

        """
        source = node.string(prop, required=required)
        if source is None:
            return None
        try:
            return compile_expression(source)
        except ExpressionError as refused:
            node.error(f"{refused.message} {refused.where()}".strip(), prop)
            return None

    # -- the layout tree ----------------------------------------------

    def layout(
        self,
        node: kdl.Node,
        basedir: Path,
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Layout:
        """Build the ``layout`` node.

        Args:
            node: The node to read.
            basedir: What a ``subreport template=`` resolves against.
            layouts: Where every embedded layout is indexed, by qualified
                name; filled as they are built.

        """
        node.known_properties(
            "pagesize",
            "width",
            "height",
            "landscape",
            "leftmargin",
            "rightmargin",
            "topmargin",
            "bottommargin",
        )
        node.known_children(
            "style", "embedded", *SECTIONS[:4], "columns", "group", "detail"
        )
        scopes = (Scope((), self.declared(node)),)
        return Layout(
            paper=self.paper(node),
            header=self.section(node, "header", basedir, scopes, layouts),
            footer=self.section(node, "footer", basedir, scopes, layouts),
            embedded=tuple(
                self.embedded(child, (), basedir, scopes, layouts)
                for child in node.each("embedded")
            ),
            **self.nesting(node, basedir, scopes, layouts),
        )

    def paper(self, node: kdl.Node) -> Paper:
        """Build the page geometry of a ``layout``.

        Args:
            node: The ``layout`` node.

        """
        name = node.enum("pagesize", model.PAGE_SIZES)
        width = height = 0.0
        if name is not None:
            width, height = model.page_size(name)
            if node.has("width") or node.has("height"):
                node.error("pagesize and an explicit width or height are alternatives")
        elif node.has("width") and node.has("height"):
            width = node.dimension("width", default=0.0)
            height = node.dimension("height", default=0.0)
        elif not node.has("pagesize"):
            node.error("a layout needs a pagesize, or both a width and a height")
        if node.boolean("landscape", default=False):
            width, height = height, width
        return Paper(
            width=width,
            height=height,
            left=node.dimension("leftmargin", default=0.0),
            right=node.dimension("rightmargin", default=0.0),
            top=node.dimension("topmargin", default=0.0),
            bottom=node.dimension("bottommargin", default=0.0),
        )

    def declared(self, node: kdl.Node) -> tuple[str, ...]:
        """Return the ``embedded`` names a node declares directly.

        This is one link of the lexical chain a ``subreport embedded=`` is
        resolved along.  It is read off the KDL children rather than off
        the model, because the layouts have to be nameable before any of
        them is built: a layout may invoke itself.

        Args:
            node: A ``layout`` or an ``embedded`` node.

        """
        names = []
        for child in node.each("embedded"):
            name = child.identity
            if name is not None and name not in names:
                names.append(name)
        return tuple(names)

    def embedded(
        self,
        node: kdl.Node,
        outer: tuple[str, ...],
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Embedded:
        """Build an ``embedded`` node.

        Args:
            node: The node to read.
            outer: The qualified name of the layout enclosing this one.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain, outermost first, as seen from the
                layout this one is written in.
            layouts: Where every embedded layout is indexed.

        """
        node.known_properties()
        node.known_children(
            "parameter",
            "records",
            "variable",
            "style",
            "embedded",
            *SECTIONS[:4],
            "columns",
            "group",
            "detail",
        )
        name = self.identity(node)
        qualname = (*outer, name)
        inner = (*scopes, Scope(qualname, self.declared(node)))
        built = Embedded(
            name=name,
            qualname=qualname,
            parameters=tuple(self.parameter(child) for child in node.each("parameter")),
            records=self.records(node),
            variables=tuple(self.variable(child) for child in node.each("variable")),
            header=self.section(node, "header", basedir, inner, layouts),
            footer=self.section(node, "footer", basedir, inner, layouts),
            embedded=tuple(
                self.embedded(child, qualname, basedir, inner, layouts)
                for child in node.each("embedded")
            ),
            **self.nesting(node, basedir, inner, layouts),
        )
        layouts[qualname] = built
        return built

    def nesting(
        self,
        node: kdl.Node,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> dict[str, Any]:
        """Build what every band-holding level has, as keyword arguments.

        Args:
            node: A ``layout``, ``group`` or ``embedded`` node.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        group = node.optional_child("group")
        detail = node.optional_child("detail")
        if (group is None) == (detail is None):
            node.error(f"a {node.name} holds exactly one group or one detail")
        columns = node.optional_child("columns")
        return {
            "styles": tuple(self.style(child) for child in node.each("style")),
            "title": self.section(node, "title", basedir, scopes, layouts),
            "summary": self.section(node, "summary", basedir, scopes, layouts),
            "columns": (
                None
                if columns is None
                else self.columns(columns, basedir, scopes, layouts)
            ),
            "group": (
                None if group is None else self.group(group, basedir, scopes, layouts)
            ),
            "detail": (
                None
                if detail is None
                else self.band(detail, "detail", basedir, scopes, layouts)
            ),
            "path": node.path,
        }

    def group(
        self,
        node: kdl.Node,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Group:
        """Build a ``group`` node.

        Args:
            node: The node to read.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        node.known_properties("expr", "keeptogether", "minrows", "mintailrows")
        node.known_children("style", "title", "summary", "columns", "group", "detail")
        return Group(
            name=self.identity(node),
            expr=self.expression(node, "expr", required=True),
            keeptogether=node.boolean("keeptogether", default=False),
            minrows=node.integer("minrows", default=1),
            mintailrows=node.integer("mintailrows", default=1),
            **self.nesting(node, basedir, scopes, layouts),
        )

    def columns(
        self,
        node: kdl.Node,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Columns:
        """Build a ``columns`` node.

        Args:
            node: The node to read.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        node.known_properties("count", "gap", "balance")
        node.known_children("style", "header", "footer")
        return Columns(
            count=node.integer("count", default=0, required=True),
            gap=node.dimension("gap", default=0.0),
            balance=node.boolean("balance", default=False),
            styles=tuple(self.style(child) for child in node.each("style")),
            header=self.section(node, "header", basedir, scopes, layouts),
            footer=self.section(node, "footer", basedir, scopes, layouts),
            path=node.path,
        )

    def section(
        self,
        parent: kdl.Node,
        kind: str,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Section | None:
        """Build a named band of a parent node, where it has one.

        Args:
            parent: The node that may carry the band.
            kind: One of :data:`~sr.template.model.SECTIONS`.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        node = parent.optional_child(kind)
        if node is None:
            return None
        return self.band(node, kind, basedir, scopes, layouts)

    def band(
        self,
        node: kdl.Node,
        kind: str,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Section:
        """Build one band.

        Args:
            node: The node to read.
            kind: One of :data:`~sr.template.model.SECTIONS`.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        swaps = {"title": ("swapheader",), "summary": ("swapfooter",)}.get(kind, ())
        node.known_properties(
            "height", "printwhen", "split", "orphans", "widows", *swaps
        )
        node.known_children(*SECTION_CHILDREN)
        subreports = tuple(
            self.subreport(child, order, basedir, scopes, layouts)
            for order, child in enumerate(node.each("subreport"))
        )
        return Section(
            kind=kind,
            height=self.band_height(node),
            printwhen=self.expression(node, "printwhen"),
            split=node.boolean("split", default=False),
            orphans=node.integer("orphans", default=1),
            widows=node.integer("widows", default=1),
            swapheader=node.boolean("swapheader", default=False),
            swapfooter=node.boolean("swapfooter", default=False),
            styles=tuple(self.style(child) for child in node.each("style")),
            ejects=tuple(self.eject(child) for child in node.each("eject")),
            outlines=tuple(self.outline(child) for child in node.each("outline")),
            elements=tuple(
                self.element(child, basedir, scopes, layouts)
                for child in node.children
                if child.name in BODY or child.name == "xref"
            ),
            # doc/template.md#ordering-rules: by `seq`, ties on document
            # order, which a stable sort over a tuple already in document
            # order keeps without the key having to say so.
            subreports=tuple(sorted(subreports, key=lambda one: one.seq)),
            path=node.path,
        )

    def band_height(self, node: kdl.Node) -> float | None:
        """Return a band's declared minimum height, or ``None`` for auto.

        ``height="auto"`` is the default and means a minimum of zero,
        which is not the same as a declared ``height=0``: the warning
        about a band that collapses is about the bands that declared
        nothing, and a template that wrote a zero said what it meant.

        Args:
            node: The band node.

        """
        if not node.has("height"):
            return None
        if node.raw("height") == "auto":
            return None
        return node.dimension("height")

    def style(self, node: kdl.Node) -> Style:
        """Build a ``style`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("when", "font", "color", "bgcolor")
        node.known_children()
        return Style(
            when=self.expression(node, "when"),
            font=node.string("font"),
            color=node.color("color"),
            bgcolor=node.color("bgcolor"),
            path=node.path,
        )

    def eject(self, node: kdl.Node) -> Eject:
        """Build an ``eject`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("type", "when", "require")
        node.known_children()
        return Eject(
            kind=node.enum("type", EJECT_TYPES, default="page"),
            when=self.expression(node, "when"),
            require=node.dimension("require"),
            path=node.path,
        )

    def outline(self, node: kdl.Node) -> Outline:
        """Build an ``outline`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("title", "level", "name", "when", "closed")
        node.known_children()
        return Outline(
            title=self.expression(node, "title", required=True),
            level=node.integer("level", default=1),
            name=self.expression(node, "name"),
            when=self.expression(node, "when"),
            closed=node.boolean("closed", default=False),
            path=node.path,
        )

    # -- body elements ------------------------------------------------

    def element(
        self,
        node: kdl.Node,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Element | Xref:
        """Build one body element, or an ``xref`` and everything in it.

        Args:
            node: The node to read.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        if node.name == "xref":
            return self.xref(node, basedir, scopes, layouts)
        builders = {
            "field": self.field,
            "line": self.line,
            "rectangle": self.rectangle,
            "image": self.image,
            "barcode": self.barcode,
        }
        return builders[node.name](node)

    def common(
        self, node: kdl.Node, *, stroke: bool = False, aligned: bool = True
    ) -> dict[str, Any]:
        """Build what every body element has, as keyword arguments.

        Args:
            node: The node to read.
            stroke: Whether ``width`` is this element's pen
                rather than its horizontal extent, which is true
                of a ``line`` and a ``rectangle`` and of nothing else.
            aligned: Whether the element positions content inside its box.

        """
        return {
            "path": node.path,
            "box": self.box(node, stroke=stroke),
            "halign": (
                node.enum("halign", HALIGNS, default="left") if aligned else "left"
            ),
            "valign": (
                node.enum("valign", VALIGNS, default="top") if aligned else "top"
            ),
            "floating": node.boolean("float", default=False),
            "printwhen": self.expression(node, "printwhen"),
            "styles": tuple(self.style(child) for child in node.each("style")),
        }

    def box(self, node: kdl.Node, *, stroke: bool = False) -> Box:
        """Build an element's declared geometry.

        Args:
            node: The node to read.
            stroke: Whether ``width`` is the pen rather than the extent.

        """
        across = (
            self.alias(node, "left", "x"),
            node.dimension("right"),
            None if stroke else node.dimension("width"),
        )
        down = (
            self.alias(node, "top", "y"),
            node.dimension("bottom"),
            node.dimension("height"),
        )
        self.two_of_three(node, across, ("left", "right", "width"), stroke=stroke)
        self.two_of_three(node, down, ("top", "bottom", "height"))
        return Box(
            across=model.span(*across, node.dimension("maxwidth")),
            down=model.span(*down, node.dimension("maxheight")),
        )

    def two_of_three(
        self,
        node: kdl.Node,
        given: tuple[float | None, float | None, float | None],
        names: tuple[str, str, str],
        *,
        stroke: bool = False,
    ) -> None:
        """Report an axis that was over-determined.

        Args:
            node: The node the box belongs to.
            given: The three values, in ``names`` order.
            names: What the three are called on this axis.
            stroke: Whether the third name is the pen here, in which case
                there is no third value and nothing to over-determine.

        """
        if stroke or sum(value is not None for value in given) <= 2:
            return
        node.error(f"{names[0]}, {names[1]} and {names[2]} are one too many")

    def alias(self, node: kdl.Node, prop: str, alias: str) -> float | None:
        """Return a dimension written either as itself or as its alias.

        ``x`` and ``y`` are accepted for ``left`` and ``top``.
        Writing both is refused rather than resolved: there is no reading
        of ``left=10 x=20`` that is not a guess at which was meant.

        Args:
            node: The node to read.
            prop: The property's own name.
            alias: The other spelling of it.

        """
        if node.has(prop) and node.has(alias):
            node.error(f"{prop} and {alias} are the same property", alias)
            return node.dimension(prop)
        if node.has(alias):
            return node.dimension(alias)
        return node.dimension(prop)

    def field(self, node: kdl.Node) -> Field:
        """Build a ``field`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(
            *COMMON,
            *ALIGNMENT,
            "width",
            "expr",
            "text",
            "data",
            "evaltime",
            "align",
            "format",
            "stretch",
        )
        node.known_children("style")
        return Field(
            expr=self.expression(node, "expr"),
            text=node.string("text"),
            data=node.string("data"),
            evaltime=node.string("evaltime"),
            align=node.enum("align", ALIGNS),
            format=node.string("format", default="%s"),
            stretch=node.boolean("stretch", default=False),
            **self.common(node),
        )

    def line(self, node: kdl.Node) -> Line:
        """Build a ``line`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(*COMMON, "width", "dash", "backslant")
        node.known_children("style")
        return Line(
            stroke=node.dimension("width", default=0.0),
            dash=node.enum("dash", DASHES, default="solid"),
            backslant=node.boolean("backslant", default=False),
            **self.common(node, stroke=True, aligned=False),
        )

    def rectangle(self, node: kdl.Node) -> Rectangle:
        """Build a ``rectangle`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(*COMMON, "width", "dash", "radius", "opaque", "stroke")
        node.known_children("style")
        return Rectangle(
            stroke=node.dimension("width", default=0.0),
            dash=node.enum("dash", DASHES, default="solid"),
            radius=node.dimension("radius", default=0.0),
            opaque=node.boolean("opaque", default=True),
            outlined=node.boolean("stroke", default=True),
            **self.common(node, stroke=True, aligned=False),
        )

    def image(self, node: kdl.Node) -> Image:
        """Build an ``image`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(
            *COMMON,
            *ALIGNMENT,
            "width",
            "file",
            "data",
            "type",
            "scale",
            "proportional",
            "embed",
        )
        node.known_children("style", "content")
        return Image(
            file=node.string("file"),
            data=node.string("data"),
            content=self.content(node),
            kind=node.enum("type", IMAGE_TYPES),
            scale=node.enum("scale", IMAGE_SCALES, default="cut"),
            proportional=node.boolean("proportional", default=True),
            embed=node.boolean("embed", default=True),
            **self.common(node),
        )

    def barcode(self, node: kdl.Node) -> Barcode:
        """Build a ``barcode`` node.

        Args:
            node: The node to read.

        """
        node.known_properties(
            *COMMON,
            *ALIGNMENT,
            "width",
            "type",
            "expr",
            "text",
            "data",
            "evaltime",
            "format",
            "module",
            "vertical",
            "grow",
            "ink",
            "paper",
        )
        node.known_children("style")
        return Barcode(
            kind=node.enum("type", BARCODE_TYPES, default="", required=True),
            expr=self.expression(node, "expr"),
            text=node.string("text"),
            data=node.string("data"),
            evaltime=node.string("evaltime"),
            format=node.string("format", default="%s"),
            module=node.dimension("module", default=DEFAULT_MODULE),
            vertical=node.boolean("vertical", default=False),
            grow=node.boolean("grow", default=False),
            ink=node.color("ink", default="#000000"),
            paper=node.color("paper"),
            **self.common(node),
        )

    def xref(
        self,
        node: kdl.Node,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Xref:
        """Build an ``xref`` node and the elements inside it.

        Args:
            node: The node to read.
            basedir: What a ``subreport template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        node.known_properties(
            *GEOMETRY, *CLAMPS, *ALIGNMENT, "width", "type", "target", "caption"
        )
        node.known_children(*BODY, "xref")
        return Xref(
            path=node.path,
            box=self.box(node),
            halign=node.enum("halign", HALIGNS, default="left"),
            valign=node.enum("valign", VALIGNS, default="top"),
            kind=node.enum("type", XREF_TYPES, default="", required=True),
            target=self.expression(node, "target", required=True),
            caption=self.expression(node, "caption"),
            elements=tuple(
                self.element(child, basedir, scopes, layouts)
                for child in node.children
                if child.name in BODY or child.name == "xref"
            ),
        )

    # -- subreports ---------------------------------------------------

    def subreport(
        self,
        node: kdl.Node,
        order: int,
        basedir: Path,
        scopes: tuple[Scope, ...],
        layouts: dict[tuple[str, ...], Embedded],
    ) -> Subreport:
        """Build a ``subreport`` node, reading what it names.

        Args:
            node: The node to read.
            order: Its position among its band's subreports.
            basedir: What ``template=`` resolves against.
            scopes: The lexical chain for an ``embedded`` reference.
            layouts: Where every embedded layout is indexed.

        """
        node.known_properties(
            "template", "embedded", "seq", "data", "when", "inline", "ownpageno"
        )
        node.known_children("arg")
        template = node.string("template")
        embedded = node.string("embedded")
        file = None if template is None else (basedir / template).resolve()
        return Subreport(
            template=template,
            file=file,
            report=None if file is None else self.referenced(node, file),
            embedded=embedded,
            scope=None if embedded is None else self.resolve(node, embedded, scopes),
            seq=node.integer("seq", default=0, required=True),
            data=self.expression(node, "data", required=True),
            when=self.expression(node, "when"),
            inline=node.boolean("inline", default=False),
            ownpageno=node.boolean("ownpageno", default=False),
            args=tuple(self.arg(child) for child in node.each("arg")),
            order=order,
            path=node.path,
        )

    def resolve(
        self, node: kdl.Node, name: str, scopes: tuple[Scope, ...]
    ) -> tuple[str, ...] | None:
        """Return the qualified name an ``embedded=`` resolves to.

        The chain is searched from the inside out, so the layouts
        declared beside the one the node is written in win over those
        of an enclosing layout.  ``scopes`` is outermost first,
        which is why this walks it backwards.

        Args:
            node: The ``subreport`` node.
            name: What it named.
            scopes: The lexical chain, outermost first.

        """
        for scope in reversed(scopes):
            if name in scope.names:
                return (*scope.owner, name)
        node.error(f"no embedded layout named {name!r} is in scope here", "embedded")
        return None

    def arg(self, node: kdl.Node) -> Arg:
        """Build an ``arg`` node.

        Args:
            node: The node to read.

        """
        node.known_properties("value")
        node.known_children()
        return Arg(
            name=self.identity(node),
            value=self.expression(node, "value", required=True),
            path=node.path,
        )

    def referenced(self, node: kdl.Node, file: Path) -> Report | None:
        """Read the template a ``subreport template=`` names.

        Args:
            node: The ``subreport`` node, for the diagnostic.
            file: The resolved path of the template it names.

        """
        if self.cycles(file):
            node.error(
                f"the subreport template {file} reaches the template it came from",
                "template",
            )
            return None
        if file in self.cache:
            return self.cache[file]
        return self.reading(file, file)

    def reading(self, path: Path, file: Path) -> Report | None:
        """Read one template file, on the stack and into the cache.

        Args:
            path: The file to open, as the caller spelled it,
                which is also what a diagnostic about it names.
            file: The same file resolved, which is the key
                the cycle check and the cache go by.

        """
        self.stack.append(file)
        try:
            report = self.document(path)
        finally:
            self.stack.pop()
        self.cache[file] = report
        self.reaches[file] = frozenset({file, *self.reached(report)})
        return report

    def cycles(self, file: Path) -> bool:
        """Report whether reading a template would close a cycle.

        Args:
            file: The resolved path of the template to read.

        """
        if file in self.stack:
            return True
        reachable = self.reaches.get(file)
        return reachable is not None and bool(reachable.intersection(self.stack))

    def reached(self, report: Report | None) -> set[Path]:
        """Return every template a loaded report reaches, transitively.

        Args:
            report: What was loaded, or ``None`` where nothing was.

        """
        found: set[Path] = set()
        if report is None:
            return found
        for subreport in subreports_of(report):
            if subreport.file is None:
                continue
            found.add(subreport.file)
            found.update(self.reaches.get(subreport.file, frozenset()))
        return found


# -- walking a built report -------------------------------------------


def sections_of(report: Report) -> tuple[Section, ...]:
    """Return every band in a report, its embedded layouts included.

    Args:
        report: The template to walk.

    """
    found: list[Section] = []
    if report.layout is not None:
        found.extend(bands_of(report.layout))
    for layout in report.layouts.values():
        found.extend(bands_of(layout))
    return tuple(found)


def bands_of(level: Nesting) -> tuple[Section, ...]:
    """Return every band a nesting level and the groups under it hold.

    An ``embedded`` layout's own bands are not included:
    it is indexed separately on the report, and walking into it
    from the layout it is written in would count every band in it twice.

    Args:
        level: A ``layout``, ``group`` or ``embedded``.

    """
    found: list[Section | None] = [level.title, level.summary, level.detail]
    if isinstance(level, Layout | Embedded):
        found.extend([level.header, level.footer])
    if level.columns is not None:
        found.extend([level.columns.header, level.columns.footer])
    bands = [band for band in found if band is not None]
    if level.group is not None:
        bands.extend(bands_of(level.group))
    return tuple(bands)


def levels_of(level: Nesting) -> tuple[Nesting, ...]:
    """Return a nesting level and every group under it.

    Args:
        level: A ``layout``, ``group`` or ``embedded``.

    """
    found: list[Nesting] = [level]
    if level.group is not None:
        found.extend(levels_of(level.group))
    return tuple(found)


def subreports_of(report: Report) -> tuple[Subreport, ...]:
    """Return every ``subreport`` node in a report.

    Args:
        report: The template to walk.

    """
    found: list[Subreport] = []
    for section in sections_of(report):
        found.extend(section.subreports)
    return tuple(found)


def elements_of(section: Section) -> tuple[Element, ...]:
    """Return every body element a band holds, ``xref`` children included.

    Args:
        section: The band to walk.

    """
    found: list[Element] = []
    pending = list(section.elements)
    while pending:
        one = pending.pop(0)
        if isinstance(one, Xref):
            pending[:0] = list(one.elements)
        else:
            found.append(one)
    return tuple(found)


def xrefs_of(section: Section) -> tuple[Xref, ...]:
    """Return every ``xref`` a band holds, nested ones included.

    Args:
        section: The band to walk.

    """
    found: list[Xref] = []
    pending = list(section.elements)
    while pending:
        one = pending.pop(0)
        if isinstance(one, Xref):
            found.append(one)
            pending[:0] = list(one.elements)
    return tuple(found)
