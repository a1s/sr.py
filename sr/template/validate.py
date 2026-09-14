"""The rules of doc/template.md#validation, checked over a built model.

What is here is everything a single node cannot answer on its own.
The loader has already refused a value of the wrong type and a node
in the wrong place; these are the checks that need the document around
the node -- a `style` naming a font declared forty lines up, an `arg`
naming a parameter in another file, a `group` whose derived `X_COUNT`
collides with a name the engine already has.

Two structures organise it.

A **space** is one namespace: the report's own declarations together
with the bands that can see them, or an `embedded` layout's, which
doc/template.md#embedded makes separate for parameters, records,
variables and groups.  Every check about a name being declared is made
within one space, so the bands of an embedded layout are held to that
layout's names rather than to the report's.

A **visit** is one report, and a load can hold several: a `subreport
template=` pulls in a document of its own, which "validates on its own"
and whose faults are reported against its own file.  So each report
gets its own collector, and the cross-file checks -- an `arg` against
the parameters of the layout it feeds -- are made at the `subreport` node,
in the file that wrote it.

Two rules are deliberately not checked here.  Name resolution inside
an expression is not, because an undeclared record field is reached
dynamically and there is no list of what the data will carry.  And a
`barcode` is not held to what its symbology can encode, nor its `ink`
against its `paper`: both are load errors, and both wait for the
encoders of M10, which are what knows the answers.

"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from sr.errors import Diagnostic, Diagnostics, NodePath
from sr.expr import GLOBALS, GROUP_SUFFIXES, PREDEFINED, Expression
from sr.template.load import (
    bands_of,
    elements_of,
    levels_of,
    sections_of,
    xrefs_of,
)
from sr.template.model import (
    EVALTIME_SCOPES,
    Barcode,
    Element,
    Embedded,
    Field,
    Group,
    Image,
    Layout,
    Nesting,
    Parameter,
    Report,
    Section,
    Style,
    Subreport,
    Variable,
    Xref,
)

__all__ = ["Space", "Visit", "final_names", "literal", "validate"]

# The bands a `subreport` may sit on, and the property that disqualifies each.
# A swapped band sits beyond the frame's own reservation and a subreport
# takes frame space of its own, so the two cannot both be true.
SUBREPORT_BANDS = {"detail": None, "title": "swapheader", "summary": "swapfooter"}


@dataclass(frozen=True)
class Space:
    """One namespace, and the bands that resolve names in it.

    Attributes:
        root: The ``layout`` or ``embedded`` node it belongs to.
        parameters: The parameters declared in it.
        variables: The accumulators declared in it.
        groups: The groups nested under it, outermost first.

    """

    root: Nesting
    parameters: tuple[Parameter, ...]
    variables: tuple[Variable, ...]
    groups: tuple[Group, ...]

    @property
    def bands(self) -> tuple[Section, ...]:
        """Every band that resolves names in this space."""
        return bands_of(self.root)

    @property
    def group_names(self) -> frozenset[str]:
        """The names of the groups nested under this layout."""
        return frozenset(one.name for one in self.groups)

    @property
    def final_scope(self) -> frozenset[str]:
        """Every name ``FINAL.`` may be followed by.

        doc/expressions.md#final holds only what changes as the report
        is built: the predefined variables, the names a group derives
        from its own, and the accumulators.  A parameter is constant
        and a record field belongs to a record, so neither is in it.

        """
        derived = {
            name + suffix for name in self.group_names for suffix in GROUP_SUFFIXES
        }
        return frozenset(PREDEFINED) | derived | {one.name for one in self.variables}


@dataclass
class Visit:
    """One report being validated, and what has been found in it.

    Attributes:
        report: The document.
        outlines: The outline names the whole load offers, or ``None``
            where one of them is computed and the set is unknowable.
        diagnostics: Its errors, naming its own file.
        warnings: Its warnings, likewise.

    """

    report: Report
    outlines: frozenset[str] | None = None
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    warnings: Diagnostics = field(default_factory=Diagnostics)

    def __post_init__(self) -> None:
        """Point both collectors at the document's own file."""
        self.diagnostics.file = self.report.file
        self.warnings.file = self.report.file


def validate(report: Report) -> tuple[tuple[Diagnostic, ...], tuple[Diagnostic, ...]]:
    """Check a template and every template it names.

    Args:
        report: The model to check.

    Returns:
        The errors and the warnings, each in the order they were found,
        the outermost document's first.

    """
    reachable = documents(report)
    names = outline_names(reachable)
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    for one in reachable:
        visit = Visit(one, outlines=names)
        check(visit)
        errors.extend(visit.diagnostics)
        warnings.extend(visit.warnings)
    return tuple(errors), tuple(warnings)


def check(visit: Visit) -> None:
    """Run every check over one report.

    Args:
        visit: The document and its collectors.

    """
    unique_names(visit)
    reserved_names(visit)
    declarations(visit)
    for space in spaces(visit.report):
        names_in_space(visit, space)
        columns_fit(visit, space)
        for band in space.bands:
            band_rules(visit, space, band)
    outline_targets(visit)


# -- what a load holds ------------------------------------------------


def documents(report: Report) -> tuple[Report, ...]:
    """Return a report and every template it names, each once.

    Args:
        report: The document the load started from.

    """
    found: list[Report] = []
    seen: set[int] = set()
    pending = [report]
    while pending:
        one = pending.pop(0)
        if id(one) in seen:
            continue
        seen.add(id(one))
        found.append(one)
        for subreport in subreports(one):
            if subreport.report is not None:
                pending.append(subreport.report)
    return tuple(found)


def subreports(report: Report) -> tuple[Subreport, ...]:
    """Return every ``subreport`` node in one report.

    Args:
        report: The document to walk.

    """
    found: list[Subreport] = []
    for section in sections_of(report):
        found.extend(section.subreports)
    return tuple(found)


def spaces(report: Report) -> tuple[Space, ...]:
    """Return the namespaces of one report, the report's own first.

    Args:
        report: The document to walk.

    """
    found: list[Space] = []
    if report.layout is not None:
        found.append(
            Space(
                root=report.layout,
                parameters=report.parameters,
                variables=report.variables,
                groups=groups_of(report.layout),
            )
        )
    for layout in report.layouts.values():
        found.append(
            Space(
                root=layout,
                parameters=layout.parameters,
                variables=layout.variables,
                groups=groups_of(layout),
            )
        )
    return tuple(found)


def groups_of(root: Nesting) -> tuple[Group, ...]:
    """Return the groups nested under a layout, outermost first.

    Args:
        root: A ``layout`` or an ``embedded`` node.

    """
    return tuple(one for one in levels_of(root) if isinstance(one, Group))


def styles_of(report: Report) -> tuple[Style, ...]:
    """Return every ``style`` node in a report, wherever it is written.

    Args:
        report: The document to walk.

    """
    found: list[Style] = []
    roots: list[Nesting] = []
    if report.layout is not None:
        roots.append(report.layout)
    roots.extend(report.layouts.values())
    for root in roots:
        for level in levels_of(root):
            found.extend(level.styles)
            if level.columns is not None:
                found.extend(level.columns.styles)
    for band in sections_of(report):
        found.extend(band.styles)
        for element in elements_of(band):
            found.extend(element.styles)
    return tuple(found)


# -- declarations -----------------------------------------------------


def unique_names(visit: Visit) -> None:
    """Report a name declared twice in one namespace.

    Args:
        visit: The document and its collectors.

    """
    report = visit.report
    named(visit, "font", [(one.name, one.path) for one in report.fonts])
    named(visit, "data", [(one.name, one.path) for one in report.data])
    named(visit, "parameter", [(one.name, one.path) for one in report.parameters])
    named(visit, "variable", [(one.name, one.path) for one in report.variables])
    for layout in report.layouts.values():
        named(visit, "parameter", [(one.name, one.path) for one in layout.parameters])
        named(visit, "variable", [(one.name, one.path) for one in layout.variables])
    for space in spaces(report):
        named(visit, "group", [(one.name, one.path) for one in space.groups])
    embedded_names(visit)


def named(visit: Visit, kind: str, declared: list[tuple[str, NodePath]]) -> None:
    """Report every repeat in one list of declared names.

    Args:
        visit: The document and its collectors.
        kind: What the nodes are, for the diagnostic.
        declared: Each name with the path of the node that declared it.

    """
    seen: set[str] = set()
    for name, path in declared:
        if name and name in seen:
            visit.diagnostics.error(
                f"a {kind} named {name!r} is declared twice", path=path
            )
        seen.add(name)


def embedded_names(visit: Visit) -> None:
    """Report two ``embedded`` layouts that share a name in one chain.

    Two siblings may not share a name, and a nested layout may not
    repeat one an enclosing layout already has, because the search
    runs outward and the second would shadow the first.  Two unrelated
    layouts may each nest a private one of the same name, which is why
    this walks the tree rather than gathering one set over the report.

    Args:
        visit: The document and its collectors.

    """
    if visit.report.layout is not None:
        walk_embedded(visit, visit.report.layout.embedded, frozenset())


def walk_embedded(
    visit: Visit, layouts: tuple[Embedded, ...], outer: frozenset[str]
) -> None:
    """Check one level of embedded layouts, then each of their own.

    Args:
        visit: The document and its collectors.
        layouts: The layouts declared at this level, in document order.
        outer: The names every enclosing level already has.

    """
    here: set[str] = set()
    for layout in layouts:
        if layout.name in here:
            visit.diagnostics.error(
                f"an embedded layout named {layout.name!r} is declared twice here",
                path=layout.path,
            )
        elif layout.name in outer:
            visit.diagnostics.error(
                f"an embedded layout named {layout.name!r} is declared in an "
                "enclosing layout, and the search outward would find this one "
                "first",
                path=layout.path,
            )
        here.add(layout.name)
    for layout in layouts:
        walk_embedded(visit, layout.embedded, outer | here)


def reserved_names(visit: Visit) -> None:
    """Report a declaration the engine's own names would hide.

    Args:
        visit: The document and its collectors.

    """
    taken = frozenset(PREDEFINED) | frozenset(GLOBALS)
    for space in spaces(visit.report):
        for parameter in space.parameters:
            if parameter.name in taken:
                visit.diagnostics.error(
                    f"{parameter.name!r} is a predefined name, so a parameter "
                    "called that is unreachable",
                    path=parameter.path,
                )
        for variable in space.variables:
            if variable.name in taken:
                visit.diagnostics.error(
                    f"{variable.name!r} is a predefined name, so a variable "
                    "called that is unreachable",
                    path=variable.path,
                )
        for group in space.groups:
            for suffix in GROUP_SUFFIXES:
                if group.name + suffix in taken:
                    visit.diagnostics.error(
                        f"a group named {group.name!r} derives "
                        f"{group.name + suffix!r}, which is a predefined name",
                        path=group.path,
                    )


def declarations(visit: Visit) -> None:
    """Check the nodes above ``layout``: parameters, records, fonts, blobs.

    Args:
        visit: The document and its collectors.

    """
    report = visit.report
    declared = [report.parameters, *[one.parameters for one in report.layouts.values()]]
    for group in declared:
        for parameter in group:
            parameter_rules(visit, parameter)
    for records in [report.records, *[one.records for one in report.layouts.values()]]:
        if records is None:
            continue
        for member in records.members:
            if member.format is not None and member.kind not in ("date", "datetime"):
                visit.diagnostics.error(
                    f"format reads a date, and this member is a {member.kind}",
                    path=member.path,
                    prop="format",
                )
    blobs = {one.name for one in report.data}
    for font in report.fonts:
        font_rules(visit, font.path, font.typeface, font.file, font.data, blobs)
    for blob in report.data:
        if (blob.expr is None) == (not blob.has_content):
            visit.diagnostics.error(
                "a data node holds exactly one of expr or a content child",
                path=blob.path,
            )
    fonts = {one.name for one in report.fonts}
    for style in styles_of(report):
        if style.font is not None and style.font not in fonts:
            visit.diagnostics.error(
                f"no font named {style.font!r}", path=style.path, prop="font"
            )


def font_rules(
    visit: Visit,
    path: NodePath,
    typeface: str | None,
    file: str | None,
    data: str | None,
    blobs: set[str],
) -> None:
    """Check where one ``font`` node takes its face from.

    Args:
        visit: The document and its collectors.
        path: The node's path, for the diagnostic.
        typeface: The family it names, where it names one.
        file: The path it names, where it names one.
        data: The blob it names, where it names one.
        blobs: The names the report's ``data`` nodes declare.

    """
    sources = [
        name
        for name, given in (("typeface", typeface), ("file", file), ("data", data))
        if given is not None
    ]
    if len(sources) != 1:
        visit.diagnostics.error(
            "a font names exactly one of typeface, file or data; "
            f"this one names {len(sources)}",
            path=path,
        )
    if data is not None and data not in blobs:
        visit.diagnostics.error(f"no data node named {data!r}", path=path, prop="data")


def parameter_rules(visit: Visit, parameter: Parameter) -> None:
    """Check one ``parameter`` node against the whole of its declaration.

    Args:
        visit: The document and its collectors.
        parameter: The node to check.

    """
    if parameter.default is not None and parameter.defaultexpr is not None:
        visit.diagnostics.error(
            "default and defaultexpr are alternatives", path=parameter.path
        )
    if parameter.format is not None and parameter.kind not in ("date", "datetime"):
        visit.diagnostics.error(
            f"format reads a date, and this parameter is a {parameter.kind}",
            path=parameter.path,
            prop="format",
        )
    if parameter.defaultexpr is not None and parameter.defaultexpr.uses_final:
        visit.diagnostics.error(
            "FINAL belongs in the expr of a deferred field or barcode, "
            "and nowhere else",
            path=parameter.path,
            prop="defaultexpr",
        )


# -- names within one space -------------------------------------------


def names_in_space(visit: Visit, space: Space) -> None:
    """Check the declarations of a space that name other declarations.

    Args:
        visit: The document and its collectors.
        space: The namespace to check within.

    """
    groups = space.group_names
    for variable in space.variables:
        for prop, target, mode in (
            ("itergrp", variable.itergrp, variable.iterate),
            ("resetgrp", variable.resetgrp, variable.reset),
        ):
            if target is not None and target not in groups:
                visit.diagnostics.error(
                    f"no group named {target!r}", path=variable.path, prop=prop
                )
            if mode == "group" and target is None:
                visit.diagnostics.error(
                    f'{prop[:-3]}="group" names the group with a {prop}',
                    path=variable.path,
                    prop=prop[:-3],
                )
        for expression, prop in ((variable.expr, "expr"), (variable.init, "init")):
            forbid_final(visit, expression, variable.path, prop)
    for group in space.groups:
        forbid_final(visit, group.expr, group.path, "expr")


def forbid_final(
    visit: Visit, expression: Expression | None, path: NodePath, prop: str
) -> None:
    """Report an expression naming ``FINAL`` where it may not.

    Args:
        visit: The document and its collectors.
        expression: The compiled expression, where there is one.
        path: The node it is written on.
        prop: The property it is written as.

    """
    if expression is not None and expression.uses_final:
        visit.diagnostics.error(
            "FINAL belongs in the expr of a field or barcode with an "
            "evaltime, and nowhere else",
            path=path,
            prop=prop,
        )


def columns_fit(visit: Visit, space: Space) -> None:
    """Report a ``columns`` block whose columns would have no width.

    Args:
        visit: The document and its collectors.
        space: The namespace whose levels hold the blocks.

    """
    layout = visit.report.layout
    if layout is None:
        return
    frame = layout.paper.width - layout.paper.left - layout.paper.right
    for level in levels_of(space.root):
        columns = level.columns
        if columns is None:
            continue
        if columns.count < 1:
            visit.diagnostics.error(
                "a columns block needs at least one column",
                path=columns.path,
                prop="count",
            )
            continue
        width = (frame - (columns.count - 1) * columns.gap) / columns.count
        if width <= 0:
            visit.diagnostics.error(
                f"{columns.count} columns with a gap of {columns.gap}"
                f" leave no width in a frame {frame} wide",
                path=columns.path,
            )


# -- bands ------------------------------------------------------------


def band_rules(visit: Visit, space: Space, band: Section) -> None:
    """Check one band, its elements and its subreports.

    Args:
        visit: The document and its collectors.
        space: The namespace the band resolves names in.
        band: The band to check.

    """
    plain_expressions(visit, band)
    for element in elements_of(band):
        if isinstance(element, Field | Barcode):
            content_source(visit, space, element)
        if isinstance(element, Image):
            image_source(visit, element)
        blob_reference(visit, element)
    for subreport in band.subreports:
        subreport_rules(visit, band, subreport)
    collapsing(visit, band)


def plain_expressions(visit: Visit, band: Section) -> None:
    """Check every expression on a band that is not a deferred ``expr``.

    ``FINAL`` lives in the `expr` of a deferred `field` or `barcode` and
    nowhere else, so every expression this walks is one that may not name
    it -- including the `printwhen` of the very element whose `expr` may.

    Args:
        visit: The document and its collectors.
        band: The band to check.

    """
    written: list[tuple[Expression | None, NodePath, str]] = [
        (band.printwhen, band.path, "printwhen")
    ]
    for style in band.styles:
        written.append((style.when, style.path, "when"))
    for eject in band.ejects:
        written.append((eject.when, eject.path, "when"))
    for entry in band.outlines:
        written.append((entry.title, entry.path, "title"))
        written.append((entry.name, entry.path, "name"))
        written.append((entry.when, entry.path, "when"))
    for element in elements_of(band):
        written.append((element.printwhen, element.path, "printwhen"))
        for style in element.styles:
            written.append((style.when, style.path, "when"))
        if isinstance(element, Field | Barcode) and element.evaltime is None:
            written.append((element.expr, element.path, "expr"))
    for xref in xrefs_of(band):
        written.append((xref.target, xref.path, "target"))
        written.append((xref.caption, xref.path, "caption"))
    for subreport in band.subreports:
        written.append((subreport.data, subreport.path, "data"))
        written.append((subreport.when, subreport.path, "when"))
        for argument in subreport.args:
            written.append((argument.value, argument.path, "value"))
    for expression, path, prop in written:
        forbid_final(visit, expression, path, prop)


def content_source(visit: Visit, space: Space, element: Field | Barcode) -> None:
    """Check where a `field` or a `barcode` takes its content from.

    Args:
        visit: The document and its collectors.
        space: The namespace the element resolves names in.
        element: The node to check.

    """
    kind = "field" if isinstance(element, Field) else "barcode"
    sources = [
        name
        for name, given in (
            ("expr", element.expr),
            ("text", element.text),
            ("data", element.data),
        )
        if given is not None
    ]
    if element.evaltime is None:
        if len(sources) != 1:
            visit.diagnostics.error(
                f"a {kind} without evaltime takes exactly one of expr,"
                f" text or data; this one has {len(sources)}",
                path=element.path,
            )
        return
    evaltime_scope(visit, space, element)
    if element.expr is None:
        visit.diagnostics.error(
            "evaltime defers the expr, so there has to be one",
            path=element.path,
            prop="evaltime",
        )
    elif not element.expr.uses_final:
        visit.diagnostics.error(
            "this expr never names FINAL, so deferring it would give"
            " the same answer in place",
            path=element.path,
            prop="expr",
        )
    else:
        final_scope(visit, space, element)
    placeholders = [name for name in sources if name != "expr"]
    if len(placeholders) > 1:
        visit.diagnostics.error(
            "a deferred element takes at most one placeholder, and this one"
            f" has {len(placeholders)}: {', '.join(placeholders)}",
            path=element.path,
        )
    grows = isinstance(element, Barcode) or element.stretch
    if grows and not placeholders:
        what = (
            "a barcode grows to its symbol"
            if isinstance(element, Barcode)
            else "a stretch field grows to its text"
        )
        visit.diagnostics.error(
            f"{what}, so a deferred one needs a text or data placeholder"
            " to reserve the space with",
            path=element.path,
        )


def evaltime_scope(visit: Visit, space: Space, element: Field | Barcode) -> None:
    """Report an ``evaltime`` that names no scope.

    Args:
        visit: The document and its collectors.
        space: The namespace the element resolves names in.
        element: The node to check.

    """
    if element.evaltime in EVALTIME_SCOPES or element.evaltime in space.group_names:
        return
    wanted = " ".join([*EVALTIME_SCOPES, *sorted(space.group_names)])
    visit.diagnostics.error(
        f"{element.evaltime!r} is not a scope; want one of: {wanted}",
        path=element.path,
        prop="evaltime",
    )


def final_scope(visit: Visit, space: Space, element: Field | Barcode) -> None:
    """Report a ``FINAL.`` name that no scope holds a value for.

    Args:
        visit: The document and its collectors.
        space: The namespace the element resolves names in.
        element: The node whose ``expr`` is deferred.

    """
    if element.expr is None:
        return
    for name in final_names(element.expr.source):
        if name not in space.final_scope:
            visit.diagnostics.error(
                "FINAL holds the predefined variables and the"
                f" declared variables, and {name!r} is neither",
                path=element.path,
                prop="expr",
            )


def image_source(visit: Visit, element: Image) -> None:
    """Check where an ``image`` takes its bytes from.

    Args:
        visit: The document and its collectors.
        element: The node to check.

    """
    sources = [
        name
        for name, given in (
            ("file", element.file),
            ("data", element.data),
            ("content", element.content),
        )
        if given is not None
    ]
    if len(sources) != 1:
        visit.diagnostics.error(
            "an image takes exactly one of file, data or a content child;"
            f" this one has {len(sources)}",
            path=element.path,
        )
    if not element.embed and element.file is None:
        visit.diagnostics.error(
            "embed=#false writes a file reference, and only file supplies a path",
            path=element.path,
            prop="embed",
        )


def blob_reference(visit: Visit, element: Element) -> None:
    """Report an element naming a ``data`` node that does not exist.

    Args:
        visit: The document and its collectors.
        element: The node to check.

    """
    named_blob = getattr(element, "data", None)
    if named_blob is None:
        return
    if named_blob not in {one.name for one in visit.report.data}:
        visit.diagnostics.error(
            f"no data node named {named_blob!r}", path=element.path, prop="data"
        )


def collapsing(visit: Visit, band: Section) -> None:
    """Warn about a band that declares no height and can have none.

    Args:
        visit: The document and its collectors.
        band: The band to check.

    """
    if band.height is not None or not band.elements:
        return
    if any(contributes(element) for element in band.elements):
        return
    visit.warnings.error(
        "this band declares no height and every element in it is anchored to "
        "the band's bottom edge, so it collapses to nothing",
        path=band.path,
    )


def contributes(element: Element | Xref) -> bool:
    """Report whether an element gives a band a height to be.

    doc/layout.md#building-a-band leaves out of the maximum every
    element anchored to the band's bottom edge -- which is what
    a set ``end`` on the vertical span means, written or derived --
    unless it has a height of its own from its content.
    An ``xref`` has no content height, but what is inside it may,
    so it is asked about its children.

    Args:
        element: The node to weigh.

    """
    if element.box.down.end is None:
        return True
    if isinstance(element, Xref):
        return any(contributes(child) for child in element.elements)
    if isinstance(element, Field):
        return element.stretch
    if isinstance(element, Barcode):
        return True
    return isinstance(element, Image) and element.scale == "grow"


# -- subreports -------------------------------------------------------


def subreport_rules(visit: Visit, band: Section, subreport: Subreport) -> None:
    """Check one ``subreport`` node against the layout it names.

    Args:
        visit: The document and its collectors.
        band: The band it sits on.
        subreport: The node to check.

    """
    names = [
        name
        for name, given in (
            ("template", subreport.template),
            ("embedded", subreport.embedded),
        )
        if given is not None
    ]
    if len(names) != 1:
        visit.diagnostics.error(
            "a subreport names exactly one of template or embedded;"
            f" this one names {len(names)}",
            path=subreport.path,
        )
    if subreport.inline and subreport.ownpageno:
        visit.diagnostics.error(
            "an inline subreport prints on the host's pages,"
            " so it cannot restart their numbering",
            path=subreport.path,
            prop="ownpageno",
        )
    where(visit, band, subreport)
    target = referenced_layout(visit.report, subreport)
    if target is None:
        return
    parameters = (
        subreport.report.parameters
        if subreport.report is not None
        else target.parameters
        if isinstance(target, Embedded)
        else ()
    )
    arguments(visit, subreport, parameters)
    if subreport.inline:
        inline_rules(visit, subreport, target)


def where(visit: Visit, band: Section, subreport: Subreport) -> None:
    """Report a ``subreport`` on a band that cannot carry one.

    Args:
        visit: The document and its collectors.
        band: The band it sits on.
        subreport: The node to check.

    """
    if band.kind not in SUBREPORT_BANDS:
        visit.diagnostics.error(
            "a subreport belongs on a detail, a title or a summary,"
            f" and this is a {band.kind}",
            path=subreport.path,
        )
        return
    swap = SUBREPORT_BANDS[band.kind]
    if swap is not None and getattr(band, swap):
        visit.diagnostics.error(
            f"a {band.kind} with {swap} sits beyond the frame's own "
            "reservation, so it cannot carry a subreport",
            path=subreport.path,
        )


def referenced_layout(report: Report, subreport: Subreport) -> Nesting | None:
    """Return the layout a ``subreport`` runs, where it resolved to one.

    Args:
        report: The document the node is written in.
        subreport: The node to resolve.

    """
    if subreport.report is not None:
        return subreport.report.layout
    if subreport.scope is not None:
        return report.layouts.get(subreport.scope)
    return None


def arguments(
    visit: Visit, subreport: Subreport, parameters: tuple[Parameter, ...]
) -> None:
    """Check a subreport's ``arg`` nodes against the parameters they feed.

    Args:
        visit: The document and its collectors.
        subreport: The node to check.
        parameters: What the layout it names declares.

    """
    declared = {one.name for one in parameters}
    supplied: set[str] = set()
    for argument in subreport.args:
        if argument.name not in declared:
            visit.diagnostics.error(
                f"the subreport declares no parameter named {argument.name!r}",
                path=argument.path,
            )
        elif argument.name in supplied:
            visit.diagnostics.error(
                f"{argument.name!r} is supplied twice", path=argument.path
            )
        supplied.add(argument.name)
    for parameter in parameters:
        if parameter.required and parameter.name not in supplied:
            visit.diagnostics.error(
                f"the subreport requires {parameter.name!r},"
                " and a subreport has no command line to take it from",
                path=subreport.path,
            )


def inline_rules(visit: Visit, subreport: Subreport, target: Nesting) -> None:
    """Check what an ``inline`` subreport's layout may not have.

    Args:
        visit: The document and its collectors.
        subreport: The node to check.
        target: The layout it names.

    """
    if isinstance(target, Layout | Embedded):
        for band, name in ((target.header, "header"), (target.footer, "footer")):
            if band is not None:
                visit.diagnostics.error(
                    "an inline subreport does not own the pages it prints on,"
                    f" so its layout defines no {name}",
                    path=band.path,
                )
    if target.title is not None and target.title.swapheader:
        visit.diagnostics.error(
            "an inline subreport's title cannot swap with a page header"
            " it does not own",
            path=target.title.path,
        )
    if target.summary is not None and target.summary.swapfooter:
        visit.diagnostics.error(
            "an inline subreport's summary cannot swap with a page footer"
            " it does not own",
            path=target.summary.path,
        )
    host = visit.report.layout
    if not isinstance(target, Layout) or host is None:
        return
    page = (target.paper.width, target.paper.height)
    if page != (host.paper.width, host.paper.height):
        visit.diagnostics.error(
            f"an inline subreport prints on the host's pages, and {page[0]}"
            f" x {page[1]} is not {host.paper.width} x {host.paper.height}",
            path=subreport.path,
            prop="template",
        )


# -- outlines ---------------------------------------------------------


def outline_names(reports: tuple[Report, ...]) -> frozenset[str] | None:
    """Return every outline name a load offers, or ``None`` where unknown.

    Both sides of an outline reference are expressions, so only
    constant names can be gathered.  Where any of them is computed,
    no target can be called unreachable and nothing is gathered at all.

    Args:
        reports: Every document the load read.

    """
    names: set[str] = set()
    for report in reports:
        for band in sections_of(report):
            for entry in band.outlines:
                if entry.name is None:
                    continue
                value = literal(entry.name)
                if value is None:
                    return None
                names.add(value)
    return frozenset(names)


def outline_targets(visit: Visit) -> None:
    """Report an ``xref type="outline"`` that names no outline entry.

    Args:
        visit: The document and its collectors.

    """
    if visit.outlines is None:
        return
    for band in sections_of(visit.report):
        for xref in xrefs_of(band):
            if xref.kind != "outline" or xref.target is None:
                continue
            value = literal(xref.target)
            if value is not None and value not in visit.outlines:
                visit.diagnostics.error(
                    f"no outline is named {value!r}", path=xref.path, prop="target"
                )


def literal(expression: Expression) -> str | None:
    """Return the string an expression is, where it is a string literal.

    Args:
        expression: The compiled expression to read.

    """
    try:
        tree = ast.parse(expression.source, mode="eval")
    except SyntaxError:
        return None
    body = tree.body
    if isinstance(body, ast.Constant) and isinstance(body.value, str):
        return body.value
    return None


def final_names(source: str) -> tuple[str, ...]:
    """Return every name an expression reads out of ``FINAL``.

    Both spellings are read: ``FINAL.total`` and ``FINAL["total"]``.
    A subscript by anything but a literal string names something
    only the run knows, and is left out rather than reported.

    Args:
        source: The expression, as the template wrote it.

    """
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return ()
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_final(node.value):
            found.append(node.attr)
        elif (
            isinstance(node, ast.Subscript)
            and is_final(node.value)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.append(node.slice.value)
    return tuple(found)


def is_final(node: ast.expr) -> bool:
    """Report whether an expression node is the bare name ``FINAL``.

    Args:
        node: The node to test.

    """
    return isinstance(node, ast.Name) and node.id == "FINAL"
