"""The record loop, and the pages it fills.

doc/layout.md#report-structure is the shape:

```
title
  [page header, first page]
  for each record: group titles / detail / group summaries
  [page footer, last page]
summary
```

and doc/layout.md#the-record-loop is what happens per record:

1. ``THIS`` and ``ITEM_NUMBER`` advance, and every group's key is
   evaluated against the new record, outermost first.  The outermost
   that changed breaks, and every group inside it breaks too.
2. Each breaking group's summary is placed, innermost first, against
   the **previous** record: ``THIS`` and ``ITEM_NUMBER`` are put back
   for the length of it, so a group summary prints its own total.
3. Variables whose group scope ended are reset, and every breaking
   group's ``X_COUNT`` and ``X_PAGE_NUMBER`` start again.
4. Each breaking group opens, outermost first: its group-scoped
   variables fold, and its title is placed.
5. The `item`-scoped variables fold, then, where the detail prints,
   the `detail`-scoped ones, and the detail band is placed.

An eject at a break, before anything of the new record is on the page,
ends the page of the runs that ended: its footers are built against
the context the summaries were built in, and its headers against
the new record, per doc/layout.md#what-a-header-or-footer-sees.

Every band goes through doc/layout.md#measure-decide-commit, and deciding
is the five branches of doc/layout.md#placing-a-band: commit it, split it
at a legal split point, eject and measure it again, or -- for a band too
tall for any frame -- cut it wherever it can be cut at all, and failing
that try the next page once.  An eject the band causes is a column
eject, but for that last one, and doc/layout.md#sequence is what one
does: footers innermost first, the scopes that ended, the advance,
then headers outermost first.

A band that ejects has its fold rolled back first and applied again
after, so that no value is counted twice and none is lost to a reset
the eject fired: doc/layout.md#rollback.  A group title's fold is a band's
fold like a detail's, which is why a group opening at the top of a page
it was pushed onto counts its variables on that page and not the one before.

A deferred field reaches the page as its placeholder, and is
registered as its band is placed: committed, or drawn as a header,
a footer, or a swapped band.  doc/layout.md#when-a-scope-ends says
when its value is set.  A column or page deferral resolves after
the footers of the eject that ends its scope, against the context
those footers were built in.  A group deferral resolves after the
group's summary.  Whatever is still waiting resolves after the
last page's footers.

Two judgements wait for more of the document than a band.
A mark outside its page's printable area is an overflow,
doc/layout.md#errors, and it is judged as the page ends, where
balancing has put every band where it stays.  And the outline is
settled once every page is made: an xref to an outline entry may
name one a later page holds, an entry keeps its `name` only where
some xref targets it, and each entry's level is written as its depth
in the tree its declared levels describe, per doc/printout.md#outline.

Three orders are settled here and each was read off the reference.

* **The marks of a page come out in the order their bands were built.**
  A footer is last because it is built last: it is built against the
  outgoing context, per doc/layout.md#what-a-header-or-footer-sees.
* **The counters count what has been printed**, so the first detail band
  is built with ``REPORT_COUNT`` at 0 and the second at 1.
* **Variables iterate before the band that reads them**, per
  doc/expressions.md#ordering-against-section-printing.

A [subreport](doc/layout.md#subreports) is another :class:`Builder`
with its own context, run from the middle of placing its host band:
its `eject` nodes first, then the subreports of negative ``seq``, the
band, and those of non-negative ``seq``; a report's own title tests its
`eject` nodes last, after both.  A subreport with **pages of its own**
gets a :class:`Sheet` of its own: the host's page is ended first, the
child's pages follow it into the one list of pages every builder of the
run shares, and the host resumes on a fresh page.  An **inline** one
prints on its host's sheet, in the frame its host band belongs to, and
an eject in it is an eject of every builder printing on that sheet: each
frame's footer and header is built by the builder that owns the frame,
in that builder's context, and each builder's scopes end with the page
or column its bands were in.  The host band's own fold rolls back around
such an eject as it does around one the band causes, since the band is
not placed yet: a page-scoped total neither counts it on the page it
leaves nor loses it to the reset.

"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from sr import meta
from sr.errors import BuildError, BuildWarning, Location, NodePath
from sr.expr import Expression, Record, Time
from sr.expr.builtins import GROUP_SUFFIXES, PREDEFINED
from sr.expr.golayout import RFC3339
from sr.fonts.resolve import Resolution, Resolver
from sr.fonts.text import Metrics
from sr.layout.blobs import DataTable
from sr.layout.columns import Fragment, balance
from sr.layout.context import Context, condition, evaluate
from sr.layout.defer import (
    COLUMN,
    PAGE,
    Register,
    Scope,
    final,
    find,
    swap,
    waits_for,
)
from sr.layout.frame import Frame, Window
from sr.layout.images import Pictures
from sr.layout.measure import Measurement, Measurer, Origin, Shared
from sr.layout.place import choose, split
from sr.layout.subreport import DEPTH, Walk, arguments, records_of, target_of
from sr.layout.variables import Variables
from sr.printout.model import Image, Mark, Outline, Page, Paper, Printout, Xref
from sr.printout.model import Report as ReportMeta
from sr.printout.verify import Area, Crossing, check, crossing, described
from sr.printout.write import number
from sr.settle import blob_bytes, resolve_fonts
from sr.template.load import subreports_of
from sr.template.model import (
    Eject,
    Embedded,
    Group,
    Layout,
    Nesting,
    Report,
    Section,
    Subreport,
    Variable,
)
from sr.template.model import Paper as Geometry
from sr.units import fits, round_points

__all__ = ["Build", "Builder"]

# The predefined names whose values do not move while a report is built:
# each is fixed by the record it is asked about, or by the run.
# A group key that reads nothing else is a function of its record alone.
FIXED_NAMES = frozenset({"THIS", "ITEM_NUMBER", "DATA_COUNT", "BUILD_TIME"})


@dataclass(frozen=True)
class Build:
    """What one run of the engine was asked for.

    Attributes:
        report: The template, loaded and validated.
        records: The data, coerced by the template's `records`.
        parameters: The parameter values, by name.
        fonts: What resolving the template's `font` nodes produced.
        blobs: The contents of its `data` nodes, by name.
        built: When the run started.
        strict_fonts: Whether font guessing was disabled.
        allow_overflow: Whether an oversized band is a warning
            rather than an error.
        warnings: What loading had to say.  What resolving each font
            had to say stays on its resolution in ``fonts``.
        resolver: The chain the template's fonts were resolved with,
            which resolves a subreport's when it first runs.

    """

    report: Report
    records: tuple[Record, ...]
    parameters: dict[str, Any]
    fonts: tuple[Resolution, ...]
    blobs: dict[str, bytes]
    built: Time
    strict_fonts: bool = False
    allow_overflow: bool = False
    warnings: tuple[BuildWarning, ...] = ()
    resolver: Resolver | None = None


@dataclass
class Level:
    """One level of the band tree: the layout, or one group.

    Attributes:
        nesting: The node: the `layout`, an `embedded` layout, or a `group`.
        outer: The frame its title and summary belong to,
            which is the one that contains its columns.
        inner: The frame what is inside it fills: its own columns'
            frame where it opened some, and ``outer`` otherwise.
        outer_walk: The `style` walk of a band in ``outer``,
            after the band's own nodes.
        inner_walk: The walk of a band in ``inner``.

    """

    nesting: Nesting
    outer: Frame
    inner: Frame
    outer_walk: Walk
    inner_walk: Walk

    @property
    def group(self) -> Group | None:
        """Return the level's group, or ``None`` for the layout."""
        return self.nesting if isinstance(self.nesting, Group) else None


@dataclass
class Keys:
    """The distinct keys a group has seen, in the order it saw them.

    A key that hashes is looked up rather than compared with every one
    seen, which keeps a group of many runs linear.  The hashable ones
    are a dict's keys rather than a set's, since a lookahead takes back
    what it added and needs to know which came last.  A key that does
    not hash, a list say, is compared with every one.

    Attributes:
        hashed: The ones that hash.
        unhashed: The ones that do not.

    """

    hashed: dict[Any, None] = field(default_factory=dict)
    unhashed: list[Any] = field(default_factory=list)

    def __len__(self) -> int:
        """Return how many distinct keys there are."""
        return len(self.hashed) + len(self.unhashed)

    def add(self, key: Any) -> None:
        """Note a key, unless one equal to it has been seen.

        Args:
            key: The key.

        """
        try:
            hash(key)
        except TypeError:
            seen = (*self.hashed, *self.unhashed)
            if not any(one == key for one in seen):
                self.unhashed.append(key)
            return
        if key not in self.hashed and not any(one == key for one in self.unhashed):
            self.hashed[key] = None

    def mark(self) -> tuple[int, int]:
        """Return where the keys stand, for :meth:`rewind`."""
        return len(self.hashed), len(self.unhashed)

    def rewind(self, mark: tuple[int, int]) -> None:
        """Forget every key added since :meth:`mark` returned ``mark``.

        Args:
            mark: What it returned.

        """
        hashed, unhashed = mark
        while len(self.hashed) > hashed:
            self.hashed.popitem()
        del self.unhashed[unhashed:]


@dataclass
class Tally:
    """How often a group name has opened, and on how many keys.

    doc/printout.md#header-line counts by name, for the document: a host
    and a subreport that both call a group `region` are counted together,
    and so are the runs of every invocation of one subreport.

    Attributes:
        runs: How many times a group of the name has opened.
        keys: The distinct keys groups of the name have seen.

    """

    runs: int = 0
    keys: Keys = field(default_factory=Keys)


@dataclass
class Run:
    """A group as the record loop stands: its key and its counts.

    Attributes:
        level: The level the group is.
        tally: What its name has counted, every report's.
        key: Its key for the current run, once it has opened.
        opening: Whether the run has begun with nothing of it placed:
            from the break until a band of it is committed, its title or
            a band inside it.  An eject then does not advance its page
            number, since the run begins on the page that band lands on.
        tailed: Whether the current run has had its ``mintailrows`` test.

    """

    level: Level
    tally: Tally
    key: Any = None
    opening: bool = False
    tailed: bool = False


@dataclass
class Fold:
    """A band's variable folds, which an eject it causes rolls back.

    Attributes:
        before: The accumulators before the fold.
        apply: What folds them, run again after the eject.
        variables: The accumulators it folds, which are its own report's.

    """

    before: dict[str, Any]
    apply: Callable[[], None]
    variables: Variables

    def undo(self) -> None:
        """Roll the fold back."""
        self.variables.restore(self.before)

    def redo(self) -> None:
        """Apply the fold again, evaluated afresh."""
        self.before = self.variables.snapshot()
        self.apply()


class Enough(Exception):
    """A lookahead has measured as much as its question needs."""


@dataclass
class Ahead:
    """What a lookahead has measured so far.

    Attributes:
        total: The heights of the bands it would place, summed.
        cap: Where it stops adding up: an empty frame's height.
        rows: How many printed detail rows it counted.
        wanted: The rows after which it stops, where it counts rows.
        measuring: Whether it measures the bands,
            rather than only counting the rows.

    """

    total: float = 0.0
    cap: float = 0.0
    rows: int = 0
    wanted: int | None = None
    measuring: bool = True

    def add(self, height: float) -> None:
        """Add one band, stopping once the total passes the cap.

        Args:
            height: The band's measured height.

        Raises:
            Enough: The total no longer fits an empty frame.

        """
        self.total = round_points(self.total + height)
        if not fits(self.total, self.cap):
            raise Enough

    def row(self) -> None:
        """Count one printed detail row.

        Raises:
            Enough: That was the last row the question asks about.

        """
        self.rows += 1
        if self.wanted is not None and self.rows >= self.wanted:
            raise Enough


@dataclass(frozen=True)
class Emitted:
    """A band on the page being built, as the margins will judge it.

    Every band that reaches the page has one, a header and a footer
    as much as a band the fill commits.  A balanced frame's fragment
    notes the bands in that frame apart from this, with where the fill
    put each, which is what balancing moves and the margins do not need.

    Attributes:
        start: The index of its first mark among the page's.
        origins: The element each of its marks came from, in order,
            its outline entry's being the band's.
        record: The record it was built for.
        file: The template it is written in.
        overflowed: Whether it has been reported as an overflow already,
            for not fitting any frame, so that it is not reported twice.

    """

    start: int
    origins: tuple[Origin | None, ...]
    record: int | None
    file: str | None
    overflowed: bool


@dataclass(frozen=True)
class Target:
    """The first xref to name an outline entry, held until the document ends.

    Attributes:
        path: The xref's node.
        record: The record its band was built for.
        file: The template it is written in.

    """

    path: NodePath
    record: int | None
    file: str | None


@dataclass
class Template:
    """One template document, as the run measures against it.

    An `embedded` layout is measured against the document it is
    written in, and a layout named by ``template=`` against its own.

    Attributes:
        report: The document.
        measurer: What measures its bands, against its fonts and blobs.

    """

    report: Report
    measurer: Measurer


class Document:
    """What every builder of one run shares: the pages, and the tables.

    doc/layout.md#subreports: whatever templates take part, the printout
    carries one font table, one data table and one sequence of pages.

    Attributes:
        build: What the run was asked for.
        pages: The finished pages in output order, every builder's.
        shared: What every template's measurer shares, the font table
            and the `glyph` warnings among it.
        table: The printout's `data` table.
        warnings: What laying out had to say, in the order found.
        tallies: Each group name's openings and keys.
        paper: The document's own page geometry, against which
            a page of a subreport's carries an override.
        templates: Each document measured so far, by its `report`.
        resolver: The font resolution chain, for a subreport's document.
        targets: The first xref on a page to name each outline entry,
            by that name, in the order the names were first placed.
            The end of the document holds each name to account,
            and one xref is all its error needs to name.

    """

    def __init__(self, build: Build, paper: Geometry) -> None:
        """Prepare what a run shares, and the report's own template.

        Every document a ``template=`` reaches has its declared `data`
        names reserved now, before anything is built, so that which
        names are free does not depend on which subreport runs first.

        Args:
            build: What the run was asked for.
            paper: The report's own page geometry.

        """
        self.build = build
        self.pages: list[Page] = []
        self.shared = Shared()
        self.table = DataTable(build.blobs)
        for report in referenced(build.report):
            self.table.reserve(
                (one.name for one in report.data),
                {
                    one.name: one.content
                    for one in report.data
                    if one.content is not None
                },
            )
        self.warnings: list[BuildWarning] = []
        self.targets: dict[str, Target] = {}
        self.tallies: dict[str, Tally] = {}
        self.paper = paper
        self.resolver = build.resolver
        self.templates: dict[int, Template] = {}
        self.templates[id(build.report)] = self.template(
            build.report, build.fonts, build.blobs
        )

    def template(
        self,
        report: Report,
        fonts: tuple[Resolution, ...],
        blobs: dict[str, bytes],
    ) -> Template:
        """Return a document ready to measure against.

        Args:
            report: The document.
            fonts: Its fonts, resolved.
            blobs: Its `data` nodes' contents, by name.

        """
        measurer = Measurer(
            {one.font.name: Metrics(one.face, one.font.size) for one in fonts},
            text_blobs(blobs),
            Pictures(report.basedir, blobs, self.table, report.file),
            {one.font.name: one for one in fonts},
            report.file,
            self.shared,
        )
        return Template(report, measurer)

    def template_of(self, report: Report, parameters: dict[str, Any]) -> Template:
        """Return a subreport's document, settled the first time it runs.

        Its `data` nodes are produced and its fonts resolved then,
        against the parameters of that first invocation, and kept
        for the rest of the run: a document that never runs
        resolves nothing.

        Args:
            report: The document.
            parameters: The parameters its first invocation was given.

        Raises:
            BuildError: A blob would not evaluate, or a font not resolve.

        """
        found = self.templates.get(id(report))
        if found is None:
            blobs = blob_bytes(report, parameters)
            fonts, self.resolver = resolve_fonts(
                report,
                blobs,
                strict=self.build.strict_fonts,
                resolver=self.resolver,
            )
            found = self.templates[id(report)] = self.template(report, fonts, blobs)
        return found

    def tally(self, name: str) -> Tally:
        """Return what a group name has counted.

        Args:
            name: The group's name.

        """
        return self.tallies.setdefault(name, Tally())


@dataclass(eq=False)
class Sheet:
    """The page being built, and what every builder printing on it shares.

    A report has one, and so has a subreport with pages of its own.
    An inline subreport prints on its host's.

    Attributes:
        page: The page frame, the root of the frame tree.
        paper: The page geometry.
        owners: The builder each frame belongs to, which builds its header
            and its footer in its own context.
        marks: The marks so far, in paint order.
        emitted: The bands those marks came from, in the order placed.
        fragments: What each balanced frame was given since the page opened.
        placed_on: The page the last band was committed to, the first
            before any has been: a band that fits nowhere is carried from it once.
        column_number: ``COLUMN_NUMBER``: 1 on a new page,
            and one more at each column break on it.

    """

    page: Frame
    paper: Geometry
    owners: dict[Frame, Builder] = field(default_factory=dict)
    marks: list[Mark] = field(default_factory=list)
    emitted: list[Emitted] = field(default_factory=list)
    fragments: dict[Frame, Fragment] = field(default_factory=dict)
    placed_on: int = 1
    column_number: int = 1


@dataclass(frozen=True)
class Nested:
    """What a subreport's builder runs, settled in its host's context.

    Attributes:
        document: What every builder of the run shares.
        template: The document its layout is written in.
        nesting: Its layout.
        outward: The `style` scopes outward of its layout.
        records: Its records, coerced.
        parameters: Its parameter values, by name.
        variables: Its accumulators.
        paper: The page geometry it paginates at.
        host: The builder it prints inline with, where it is inline.
        base: The frame its host band belongs to, where it is inline.
        page_number: Its first ``PAGE_NUMBER``.
        depth: How deep it is nested, from 1.

    """

    document: Document
    template: Template
    nesting: Layout | Embedded
    outward: Walk
    records: tuple[Record, ...]
    parameters: dict[str, Any]
    variables: tuple[Variable, ...]
    paper: Geometry
    host: Builder | None
    base: Frame | None
    page_number: int
    depth: int


class Builder:
    """Applies one template to one sequence of records.

    Attributes:
        build: What the run was asked for.
        document: What every builder of the run shares.
        nesting: The root of its band tree: the template's `layout`,
            or a subreport's layout.
        context: The names in scope, as the report stands.
        measurer: What turns a band into marks.
        variables: The accumulators.
        sheet: The page it prints on.
        levels: The layout and its groups, outermost first.
        runs: The groups' state, outermost first.
        host: The builder it prints inline with, for an inline subreport.
        base: The host's frame it prints in, for an inline subreport.
        grafted: The first frame of its own an inline subreport opens,
            which is grafted onto ``base`` while it runs.
        pending: The fold of a band whose negative-seq subreports are
            running, which rolls back around an eject one of them causes.
        keyed: Each record's group keys, by its 0-based position, where
            every key is a function of its record alone; ``None`` where
            one is not, and each is evaluated wherever it is asked for.
            Only the records from the one the record loop is at are
            kept, since nothing looks back.
        counted: Whether the rows left in a group can be counted from
            the keys alone, with no lookahead: the keys are kept, and
            every record prints its detail.

    """

    def __init__(self, build: Build, nested: Nested | None = None) -> None:
        """Prepare a run, or a subreport's invocation.

        Args:
            build: What the run was asked for.
            nested: What a subreport runs; ``None`` for the report.

        Raises:
            BuildError: The template has no layout.

        """
        self.build = build
        if nested is None:
            report = build.report
            if report.layout is None:
                raise BuildError(
                    "the template has no layout", Location(file=report.file)
                )
            self.document = Document(build, report.layout.paper)
            self.template = self.document.templates[id(report)]
            self.nesting: Layout | Embedded = report.layout
            self.outward: Walk = ()
            self.paper: Geometry = report.layout.paper
            self.records = build.records
            parameters = build.parameters
            declared = report.variables
            self.host: Builder | None = None
            self.base: Frame | None = None
            self.depth = 0
            first = 1
        else:
            self.document = nested.document
            self.template = nested.template
            self.nesting = nested.nesting
            self.outward = nested.outward
            self.paper = nested.paper
            self.records = nested.records
            parameters = nested.parameters
            declared = nested.variables
            self.host = nested.host
            self.base = nested.base
            self.depth = nested.depth
            first = nested.page_number
        self.file = self.template.report.file
        self.measurer = self.template.measurer
        self.context = Context(
            parameters=dict(parameters),
            data_count=len(self.records),
            build_time=build.built,
            file=self.file,
            page_number=first,
        )
        self.variables = Variables(declared, self.context)
        if self.host is None:
            page = Frame(
                header=self.nesting.header,
                footer=self.nesting.footer,
                path=self.nesting.path,
            )
            self.sheet = Sheet(page, self.paper, placed_on=first)
            self.sheet.owners[page] = self
        else:
            self.sheet = self.host.sheet
            self.context.column_number = self.sheet.column_number
        self.grafted: Frame | None = None
        self.own: list[Frame] = []
        self.levels = self.tree()
        self.runs = [
            Run(level, self.document.tally(level.group.name))
            for level in self.levels[1:]
            if level.group is not None
        ]
        for run in self.runs:
            name = self.name(run)
            self.context.group_counts[name] = 0
            self.context.group_pages[name] = 1
        variables = frozenset(one.name for one in declared)
        fixed = all(self.fixed(run, variables) for run in self.runs)
        self.keyed: dict[int, list[Any]] | None = {} if fixed else None
        detail = self.levels[-1].nesting.detail
        self.counted = fixed and detail is not None and detail.printwhen is None
        self.deferred = Register()
        self.previous: tuple[Record, int] | None = None
        self.ahead: Ahead | None = None
        # What a footer at a group break is built against, until a band
        # of the new record is committed, and the runs such a band begins.
        self.outgoing: tuple[Any, ...] | None = None
        self.beginning: list[Run] = []
        self.pending: Fold | None = None
        # Where the last band measured for placing was measured, as its
        # `VERTICAL_POSITION` and `VERTICAL_SPACE`.
        self.position = (0.0, 0.0)

    @property
    def pages(self) -> list[Page]:
        """Return the finished pages, every builder's."""
        return self.document.pages

    @property
    def marks(self) -> list[Mark]:
        """Return the marks of the page being built."""
        return self.sheet.marks

    @property
    def warnings(self) -> list[BuildWarning]:
        """Return what laying out has had to say, every builder's."""
        return self.document.warnings

    # -- the tree ---------------------------------------------------------

    def tree(self) -> list[Level]:
        """Build the frame tree and the levels that fill it.

        doc/layout.md#construction: the page frame, then a column frame
        for each `columns` node from the layout inward.  A level's title
        and summary belong to the frame that contains its columns.

        An inline subreport's tree begins at its host's frame instead
        of a page, and the first frame it opens there is grafted onto
        the host's when the invocation begins, not now.

        """
        levels: list[Level] = []
        nesting: Nesting | None = self.nesting
        frame = self.sheet.page if self.base is None else self.base
        walk: Walk = self.outward
        while nesting is not None:
            outer = frame
            outer_walk = (nesting.styles, *walk)
            inner_walk = outer_walk
            columns = nesting.columns
            if columns is not None:
                frame = Frame(
                    count=columns.count,
                    gap=columns.gap,
                    balance=columns.balance,
                    header=columns.header,
                    footer=columns.footer,
                    styles=columns.styles,
                    path=columns.path,
                    parent=outer,
                )
                self.own.append(frame)
                self.sheet.owners[frame] = self
                if outer is self.base:
                    frame.grafted = True
                    self.grafted = frame
                else:
                    outer.graft(frame)
                inner_walk = (columns.styles, *outer_walk)
            levels.append(Level(nesting, outer, frame, outer_walk, inner_walk))
            walk = inner_walk
            nesting = nesting.group
        return levels

    def frames(self) -> list[Frame]:
        """Return every frame on the sheet, outermost first."""
        return [self.sheet.page, *self.sheet.page.descendants()]

    def owner(self, frame: Frame) -> Builder:
        """Return the builder a frame on the sheet belongs to.

        Args:
            frame: The frame.

        """
        return self.sheet.owners[frame]

    def chain(self) -> list[Builder]:
        """Return the builders printing on the sheet, its owner first.

        The last is this one: an inline subreport's hosts are each
        in the middle of placing the band that invoked the next.

        """
        found: list[Builder] = [self]
        while found[-1].host is not None:
            host = found[-1].host
            assert host is not None
            found.append(host)
        return found[::-1]

    @staticmethod
    def name(run: Run) -> str:
        """Return a group's name."""
        group = run.level.group
        assert group is not None
        return group.name

    def fixed(self, run: Run, variables: frozenset[str]) -> bool:
        """Report whether a group's key is a function of its record alone.

        It is where every name its ``expr`` reads is a record field,
        a parameter, or one of :data:`FIXED_NAMES`, as the order of
        doc/expressions.md#names-in-scope resolves it: a field shadowed
        by a variable is the variable, and a variable or a counter
        moves as the report is built, in a lookahead as well.

        Args:
            run: The group.
            variables: The names of the report's variables.

        """
        group = run.level.group
        assert group is not None and group.expr is not None
        expression: Expression = group.expr
        counters = {
            f"{name}{suffix}"
            for name in self.context.group_counts
            for suffix in GROUP_SUFFIXES
        }
        for name in expression.names:
            if name in FIXED_NAMES:
                continue
            if name in PREDEFINED or name in counters:
                return False
            if name not in self.context.parameters and name in variables:
                return False
        return True

    def walk_of(self, frame: Frame) -> Walk:
        """Return the `style` walk of a header or footer of this frame.

        Args:
            frame: The frame whose band it is, which is this builder's.

        """
        if frame is self.sheet.page:
            return self.levels[0].outer_walk
        for level in self.levels:
            if level.inner is frame:
                return level.inner_walk
        raise AssertionError("a frame no level made")

    # -- the run ----------------------------------------------------------

    def run(self) -> Printout:
        """Build the document, and return it.

        The document is checked against its own
        [invariants](doc/printout.md#invariants) before it is returned.
        One it breaks is a fault in this engine rather than in the template,
        and doc/cli.md#exit-codes makes it a failed run rather than
        a document handed on with a defect nobody was told about.

        Raises:
            BuildError: A band would not fit, an expression failed,
                an xref names no outline entry, or the document
                breaks an invariant.

        """
        self.invocation()
        self.outlined()
        printout = self.printout()
        broken = check(printout)
        if broken:
            raise BuildError(
                "the printout breaks its own invariants: " + "; ".join(broken),
                Location(file=self.file),
            )
        return printout

    def invocation(self) -> None:
        """Format every record: the report, or one subreport's invocation.

        Raises:
            BuildError: A band would not fit, or an expression failed.

        """
        # doc/expressions.md#the-report-boundary fires the report scope
        # "once, before the title band is built", and seeds every variable,
        # which reserving a header needs: a header may read one.
        self.variables.iterate("report")
        self.begin()
        for index, record in enumerate(self.records):
            self.row(index, record)
            if self.keyed is not None:
                # The record loop and every lookahead only move on.
                self.keyed.pop(index, None)
        self.end()

    def begin(self) -> None:
        """Open the first page, and place the report's title on it.

        An inline subreport opens no page: it grafts its columns,
        where it has some, onto its host's frame where the host is filled.

        """
        title = self.nesting.title
        root = self.levels[0]
        if self.host is not None:
            self.graft()
            if title is not None:
                self.place(title, root.outer, self.walk(title, root), last=True)
            return
        self.new_page()
        if title is not None and title.swapheader:
            self.swapped_title(title)
            return
        self.open_frames()
        if title is not None:
            self.place(title, root.outer, self.walk(title, root), last=True)

    def end(self) -> None:
        """Close the open groups, place the summary, and finish the page.

        Every deferral still waiting resolves after the last page's
        footers, whatever its scope: the last page, the last column and
        the last run of each group end here without an eject or a break,
        and the report ends with them.  An inline subreport's end is
        not its host's page's: its deferrals resolve as its summary is
        placed, and its columns come off the host's frame without the
        last one's footer, since that column does not end.
        So its summary may take the strip that footer would have,
        and the columns come off as soon as any of it is on the page.

        """
        self.close(0)
        self.settle_columns(self.own if self.host is not None else None)
        summary = self.nesting.summary
        root = self.levels[0]
        if summary is not None:
            if summary.swapfooter:
                self.swapped_summary(summary)
            else:
                self.place(
                    summary,
                    root.outer,
                    self.walk(summary, root),
                    fitting=self.ending,
                )
        if self.host is not None:
            self.resolve(None)
            self.ungraft()
            return
        self.footers(self.frames(), None, self.chain())
        self.close_page()

    def graft(self) -> None:
        """Put an inline subreport's columns onto its host's frame, and open them.

        doc/layout.md#pages: the frames begin where the host is filled,
        and stay there for the rest of that page.  Where too little
        of the host's frame is left there for their headers and footers,
        the host ejects first, as for a band that does not fit, and they
        begin where that leaves it.  Where no eject could give them more
        room, :meth:`open_column` refuses them.

        """
        frame = self.grafted
        if frame is None:
            return
        base = self.base
        assert base is not None
        frame.begins = base.fill
        while not base.empty and not self.room_for(frame):
            self.eject(base, "column", None)
            frame.begins = base.fill
        base.graft(frame)
        self.open_columns([frame, *frame.descendants()])

    def ungraft(self) -> None:
        """Take an inline subreport's columns off its host's frame.

        Whatever the host's own frame holds below it continues below
        the invocation, as it continues below any band placed across it.

        """
        frame = self.grafted
        if frame is None or self.base is None or frame not in self.base.children:
            return
        self.base.prune(frame)
        for one in self.own:
            self.sheet.owners.pop(one, None)
            self.sheet.fragments.pop(one, None)
        frame.begins = None
        frame.ending = False
        self.base.advance(self.base.fill)

    def ending(self) -> None:
        """Leave an inline subreport's last column footer strip to its summary.

        doc/layout.md#pages: that footer is not drawn, since
        the columns come off the host's frame as soon as any
        of the summary is on the page.  A page break before then
        ends the column all the same, and its footer is drawn.

        """
        if self.grafted is not None:
            self.grafted.ending = True

    # -- the record loop --------------------------------------------------

    def row(
        self,
        index: int,
        record: Record,
        keyed: tuple[int | None, list[Any]] | None = None,
    ) -> None:
        """Format one record.

        Args:
            index: Its 0-based position in the data.
            record: The record.
            keyed: What :meth:`breaks` returned for it,
                where the caller has asked already.

        """
        breaking, keys = self.breaks(index, record) if keyed is None else keyed
        if breaking is not None and self.previous is not None:
            self.close(breaking)
        for run, key in zip(self.runs, keys, strict=True):
            run.key = key
        if breaking is not None and self.ahead is None:
            self.outgoing = self.state()
        self.context.record = record
        self.context.item_number = index + 1
        if breaking is not None:
            for run in reversed(self.runs[breaking:]):
                self.variables.clear("group", self.name(run))
            for run in self.runs[breaking:]:
                self.start(run)
            self.open(breaking, index)
        self.detail(index)
        if self.ahead is None:
            self.begun(self.runs)
        self.previous = (record, index + 1)

    def breaks(self, index: int, record: Record) -> tuple[int | None, list[Any]]:
        """Return the outermost group the record breaks, and every group's key.

        Every group's key is taken for the new record, outermost first,
        by :meth:`keys`, and :func:`first_change` finds the outermost
        that changed, which breaks every group inside it.  No group's key
        changes here: :meth:`row` keeps every key for the next comparison,
        since a group nested in one that broke has its key taken
        all the same, and a lookahead asks first where a run ends.

        Args:
            index: The record's 0-based position.
            record: The record.

        """
        if not self.runs:
            return None, []
        keys = self.keys(index, record)
        before = None if self.previous is None else [run.key for run in self.runs]
        return first_change(keys, before), keys

    def keys(self, index: int, record: Record) -> list[Any]:
        """Return every group's key for a record, outermost first.

        Each is evaluated against the record, and where :attr:`keyed`
        keeps them, only the first time the record is asked about:
        the record loop asks once, and each lookahead that reaches
        the record asks again.  Nothing else changes, since a kept
        key is one no state of the report could have changed.

        Args:
            index: The record's 0-based position.
            record: The record.

        """
        if self.keyed is not None:
            found = self.keyed.get(index)
            if found is not None:
                return found
        kept = (self.context.record, self.context.item_number)
        self.context.record = record
        self.context.item_number = index + 1
        names = self.context.environment(0.0, 0.0)
        keys: list[Any] = []
        for run in self.runs:
            group = run.level.group
            assert group is not None and group.expr is not None
            keys.append(evaluate(group.expr, names, group.path, self.context, "expr"))
        self.context.record, self.context.item_number = kept
        if self.keyed is not None:
            self.keyed[index] = keys
        return keys

    def close(self, level: int) -> None:
        """Place the summaries of the groups from ``level`` in, innermost first.

        Each is built against the previous record's context, which is
        the one still in place: the record loop advances ``THIS`` after.

        A group's deferrals resolve once its summary is committed, so
        both read the same final group totals.  A lookahead resolves
        nothing, since what it reaches is not where the report stands.

        Args:
            level: The position, among the groups, of the outermost to close.

        """
        if self.previous is None:
            return
        record, item = self.previous
        kept = (self.context.record, self.context.item_number)
        self.context.record, self.context.item_number = record, item
        for run in reversed(self.runs[level:]):
            summary = run.level.nesting.summary
            if summary is not None:
                self.place(summary, run.level.outer, self.walk(summary, run.level))
            if self.ahead is None:
                self.resolve([("group", self.name(run))])
        self.context.record, self.context.item_number = kept

    def start(self, run: Run) -> None:
        """Begin a new run of a group at its break: its counts start again.

        Args:
            run: The group.

        """
        name = self.name(run)
        run.opening = True
        run.tailed = False
        run.tally.runs += 1
        run.tally.keys.add(run.key)
        self.context.group_counts[name] = 0
        self.context.group_pages[name] = 1

    def begun(self, runs: list[Run]) -> None:
        """Note that a band of the new record is on the page.

        An eject is built as any other from here on, and the runs
        the band belongs to have begun on this page.

        Args:
            runs: The runs it begins.

        """
        self.outgoing = None
        for run in runs:
            run.opening = False

    def open(self, level: int, index: int) -> None:
        """Open the groups from ``level`` in, outermost first.

        Args:
            level: The position, among the groups, of the outermost to open.
            index: The current record's 0-based position.

        """
        for position in range(level, len(self.runs)):
            self.open_group(self.runs[position], position, index)

    def open_group(self, run: Run, position: int, index: int) -> None:
        """Open one group: fold its variables, and place its title.

        Args:
            run: The group.
            position: Its position among the groups.
            index: The current record's 0-based position.

        """
        name = self.name(run)
        group = run.level.group
        assert group is not None
        fold = self.fold(lambda: self.variables.iterate("group", name))
        title = group.title
        frame = run.level.outer
        printing = title is not None and self.measurer.prints(
            title, frame.window(), self.context
        )
        if self.ahead is None:
            self.keep_together(run, position, index, printing, fold)
        if title is not None:
            self.beginning = self.runs[: position + 1]
            self.place(
                title,
                frame,
                self.walk(title, run.level),
                printing=printing,
                fold=fold,
                eject=False,
            )
            self.beginning = []

    def detail(self, index: int) -> None:
        """Fold the record's variables and place its detail band.

        Args:
            index: The record's 0-based position.

        """
        level = self.levels[-1]
        section = level.nesting.detail
        printing = section is not None and self.measurer.prints(
            section, level.inner.window(), self.context
        )
        if printing and self.ahead is None:
            self.keep_tail(index)
        fold = self.fold(lambda: self.fold_detail(printing))
        if section is None or not printing:
            return
        self.beginning = self.runs
        self.place(
            section,
            level.inner,
            (section.styles, *level.inner_walk),
            printing=True,
            fold=fold,
            committed=self.count_detail,
        )
        self.beginning = []
        if self.ahead is not None:
            self.ahead.row()

    def count_detail(self) -> None:
        """Count a detail band that is on the page.

        The counters count what has been printed, so this
        is done once the band is committed, and before its
        subreports of non-negative ``seq`` run: they print after it,
        and a footer an eject of theirs builds counts it.

        """
        self.context.report_count += 1
        self.context.page_count += 1
        self.context.column_count += 1
        for run in self.runs:
            self.context.group_counts[self.name(run)] += 1

    def fold_detail(self, printing: bool) -> None:
        """Fold the `item`-scoped variables, and the `detail`-scoped ones.

        Args:
            printing: Whether the detail band prints, without which
                doc/expressions.md#iter-and-reset folds only `item`.

        """
        self.variables.clear("item")
        self.variables.iterate("item")
        if printing:
            self.variables.clear("detail")
            self.variables.iterate("detail")

    def fold(self, apply: Callable[[], None]) -> Fold:
        """Apply a band's folds, and return what rolls them back.

        Args:
            apply: What folds them.

        """
        before = self.variables.snapshot()
        apply()
        return Fold(before, apply, self.variables)

    def walk(self, section: Section, level: Level) -> Walk:
        """Return the `style` walk of a level's title or summary.

        Args:
            section: The band.
            level: The level it belongs to.

        """
        return (section.styles, *level.outer_walk)

    # -- keeping content together -----------------------------------------

    def keep_together(
        self, run: Run, position: int, index: int, printing: bool, fold: Fold
    ) -> None:
        """Eject before a group's title where what must follow it would not fit.

        doc/layout.md#how-they-combine: every mechanism that applies
        says how much room it wants, the largest is compared against
        what is left, and at most one eject results.  ``keeptogether``
        wants the whole group and ``minrows`` the title and that many
        printed rows, each capped at an empty frame; a selected `eject`
        node on the title wants its ``require``, or ejects outright
        without one. Only a selected node can ask for a page eject.

        Args:
            run: The group.
            position: Its position among the groups.
            index: The current record's 0-based position.
            printing: Whether its title prints.
            fold: The title's fold.

        """
        group = run.level.group
        assert group is not None
        frame = run.level.outer
        title = group.title
        chosen = self.selected(title, frame) if title is not None and printing else None
        kind = chosen.kind if chosen is not None else "column"
        if chosen is not None and chosen.require is None:
            self.eject(frame, kind, fold, deliberate=True, room=False)
            return
        wanted = 0.0 if chosen is None else (chosen.require or 0.0)
        if group.keeptogether:
            wanted = max(wanted, self.extent(run, position, index, printing, None))
        if group.minrows > 0:
            wanted = max(
                wanted, self.extent(run, position, index, printing, group.minrows)
            )
        if not frame.empty and not fits(wanted, frame.available):
            self.eject(frame, kind, fold, deliberate=True)

    def extent(
        self,
        run: Run,
        position: int,
        index: int,
        printing: bool,
        rows: int | None,
    ) -> float:
        """Measure what follows a group's title, capped at an empty frame.

        The title, and then everything the record loop would place
        after it -- nested titles and summaries, the detail rows --
        up to ``rows`` printed rows, or to the end of the group and
        its summary.

        Args:
            run: The group.
            position: Its position among the groups.
            index: The current record's 0-based position.
            printing: Whether its title prints.
            rows: How many printed rows to measure, or ``None`` for all.

        """
        group = run.level.group
        assert group is not None
        frame = run.level.outer
        with self.lookahead(frame.height, rows) as ahead:
            if group.title is not None and printing:
                self.place(
                    group.title,
                    frame,
                    self.walk(group.title, run.level),
                    printing=True,
                )
            self.open(position + 1, index)
            self.detail(index)
            self.run_ahead(index, position, summary=rows is None)
        return min(ahead.total, frame.height)

    def keep_tail(self, index: int) -> None:
        """Eject before a detail where it and the rest would leave a summary short.

        doc/layout.md#group-minrows-and-mintailrows: once no more than
        ``mintailrows`` printed rows are left in a group, they are measured
        with everything that follows them up to the group's summary, and
        if that does not fit, the eject happens before the first of them.
        Each run of a group is tested once, at the first row it applies to.

        The rows left are counted first, without measuring a band: until
        the tail is reached, which is every row but the last few, that is
        all the test needs to know.

        Args:
            index: The current record's 0-based position.

        """
        for position, run in enumerate(self.runs):
            group = run.level.group
            assert group is not None
            if run.tailed or group.mintailrows < 1:
                continue
            frame = self.levels[-1].inner
            if self.rows_left(index, position, group.mintailrows + 1) > (
                group.mintailrows
            ):
                continue
            run.tailed = True
            with self.lookahead(frame.height, None) as ahead:
                self.detail(index)
                self.run_ahead(index, position)
            wanted = min(ahead.total, frame.height)
            if not frame.empty and not fits(wanted, frame.available):
                self.eject(frame, "column", None, deliberate=True)
                return

    def rows_left(self, index: int, position: int, most: int) -> int:
        """Count the printed rows left in a group's run, up to ``most``.

        The current record's row is the first.  Where :attr:`counted`
        allows, the rows are the records up to the next break of this
        group or of one around it, which :func:`first_change` finds
        from the kept keys as it does for :meth:`breaks`.  Otherwise
        a lookahead runs the record loop on, counting the rows that print.

        Args:
            index: The current record's 0-based position.
            position: The group's position among the groups.
            most: The count at which to stop counting.

        """
        if not self.counted:
            # Uncapped: what decides whether the test applies is how many rows
            # are left, and the row count bounds how far it looks.
            with self.lookahead(math.inf, most, measuring=False) as ahead:
                self.detail(index)
                self.run_ahead(index, position)
            return ahead.rows
        records = self.records
        before = self.keys(index, records[index])
        rows = 1
        for later in range(index + 1, len(records)):
            if rows >= most:
                break
            keys = self.keys(later, records[later])
            breaking = first_change(keys, before)
            if breaking is not None and breaking <= position:
                break
            before = keys
            rows += 1
        return rows

    def run_ahead(self, index: int, position: int, summary: bool = True) -> None:
        """Carry a lookahead on to the end of a group's run.

        Each record goes through the record loop as it would, the breaks
        of the groups inside this one included.  The keys the lookahead
        keeps on the way overwrite the groups' own, which is harmless:
        :meth:`lookahead` puts them back.

        Args:
            index: The record the lookahead has reached.
            position: The group whose run it measures, among the groups.
            summary: Whether the summaries that close the run are measured.

        """
        records = self.records
        assert self.context.record is not None
        self.previous = (self.context.record, self.context.item_number)
        for later in range(index + 1, len(records)):
            keyed = self.breaks(later, records[later])
            breaking = keyed[0]
            if breaking is not None and breaking <= position:
                break
            self.row(later, records[later], keyed)
        if summary:
            self.close(position)

    @contextmanager
    def lookahead(
        self, cap: float, rows: int | None, *, measuring: bool = True
    ) -> Iterator[Ahead]:
        """Measure what the record loop would place, and then forget it.

        Everything a band's measurement reads is put back afterwards --
        the accumulators, the counters, the record, the groups' keys --
        and nothing is committed while it runs: :meth:`place` measures
        and adds up instead.  A subreport is not run: its bands are not
        measured in advance, per doc/layout.md#what-the-lookahead-does-not-see.

        Args:
            cap: The height it stops at.
            rows: The printed rows it stops after, where it counts rows.
            measuring: Whether it measures the bands,
                or only counts the rows that print.

        """
        kept = self.capture()
        ahead = Ahead(cap=cap, wanted=rows, measuring=measuring)
        outer = self.ahead
        self.ahead = ahead
        try:
            yield ahead
        except Enough:
            pass
        finally:
            self.ahead = outer
            self.restore(kept)

    def capture(self) -> tuple[Any, ...]:
        """Return everything a lookahead may change, to put back."""
        context = self.context
        return (
            self.variables.snapshot(),
            replace(
                context,
                group_counts=dict(context.group_counts),
                group_pages=dict(context.group_pages),
            ),
            [
                (
                    run.key,
                    run.opening,
                    run.tailed,
                    run.tally.runs,
                    run.tally.keys.mark(),
                )
                for run in self.runs
            ],
            self.previous,
        )

    def restore(self, kept: tuple[Any, ...]) -> None:
        """Put back what :meth:`capture` returned.

        Args:
            kept: What it returned.

        """
        accumulators, context, runs, previous = kept
        for name in (
            "record",
            "item_number",
            "report_count",
            "page_count",
            "column_count",
            "page_number",
            "column_number",
            "group_counts",
            "group_pages",
        ):
            setattr(self.context, name, getattr(context, name))
        self.variables.restore(accumulators)
        for run, (key, opening, tailed, count, mark) in zip(
            self.runs, runs, strict=True
        ):
            run.key, run.opening, run.tailed = key, opening, tailed
            run.tally.runs = count
            run.tally.keys.rewind(mark)
        self.previous = previous

    # -- placing a band ---------------------------------------------------

    def place(
        self,
        section: Section,
        frame: Frame,
        walk: Walk,
        *,
        printing: bool | None = None,
        fold: Fold | None = None,
        eject: bool = True,
        last: bool = False,
        fitting: Callable[[], None] | None = None,
        committed: Callable[[], None] | None = None,
    ) -> bool:
        """Measure a band, decide where it goes, and commit it.

        Its `eject` nodes are tested first, and then its subreports
        of negative ``seq`` run, so that it follows them; those of
        non-negative ``seq`` run once it is committed.  A report's
        own title tests its `eject` nodes after all of that, giving
        the title and its subreports a page of their own.
        The ``printwhen`` is answered once, before any of it, and that
        answer stands for the band and for every subreport it carries.

        Args:
            section: The band.
            frame: The frame it belongs to.
            walk: Its `style` walk, its own nodes first.
            printing: What its ``printwhen`` already answered,
                which holds for every retry of this one placement.
            fold: Its variable folds, which an eject it causes rolls back.
            eject: Whether its `eject` nodes are tested before it.
            last: Whether they are tested after it instead,
                as a report's own title's are.
            fitting: What to do once nothing is left to run before it,
                as it is measured to be placed.
            committed: What to do once it is on the page.

        Returns:
            Whether the band printed.

        """
        if printing is None:
            printing = self.measurer.prints(section, frame.window(), self.context)
        if not printing:
            return False
        if self.ahead is not None:
            if self.ahead.measuring:
                measured = self.measure(section, frame, walk)
                self.ahead.add(measured.height)
            if committed is not None:
                committed()
            return True
        if eject and not last:
            self.ejects(section, frame, fold)
        before = [one for one in section.subreports if one.seq < 0]
        if before:
            self.pending = fold
            try:
                for subreport in before:
                    self.invoke(subreport, frame)
            finally:
                self.pending = None
        if fitting is not None:
            fitting()
        self.fit(section, frame, walk, fold)
        if committed is not None:
            committed()
        position = self.position
        for subreport in section.subreports:
            if subreport.seq >= 0:
                self.invoke(subreport, frame, position)
        if last:
            self.ejects(section, frame)
        return True

    def measure(self, section: Section, frame: Frame, walk: Walk) -> Measurement:
        """Measure a band that prints against its frame.

        Args:
            section: The band.
            frame: The frame it belongs to.
            walk: Its `style` walk.

        """
        window = frame.window()
        self.position = (round_points(window.fill - window.top), window.available)
        measured = self.measurer.band(
            section, window, self.context, walk, printing=True
        )
        assert measured is not None
        return measured

    def fit(
        self, section: Section, frame: Frame, walk: Walk, fold: Fold | None
    ) -> None:
        """Decide where a measured band goes, and commit it.

        The five branches of doc/layout.md#placing-a-band, in their order:

        1. It fits what is left: commit it.
        2. It may split and a legal split point fits: commit the head,
           eject, and carry the tail on without measuring it again.
        3. It fits an empty frame: eject, and measure it again there.
        4. It may split and some cut point fits: cut it there, having given
           up every split preference, because progress beats preference.
        5. A band has been placed on this page, or it is the first:
           eject to the next page, and try it again from the first
           branch, since a later page may offer more room than this
           one where its headers take less.  A column eject would not do:
           from a column with bands in it, it goes to the next column
           on this page, which offers no more than an empty column here.
           A page an eject has begun, with nothing on it yet, has been
           tried already, and would be left blank: whatever made that
           eject, the band is judged there.

        Anything else overflows, and is placed at the top of an empty column.
        That may take more than one eject, since a column that begins at
        its floor is not empty, and the next one on the page begins there
        too.  The third branch needs no such test: a band that fits an
        empty frame fits an empty column, so it only gets that far in one
        that is not.

        Args:
            section: The band.
            frame: The frame it belongs to.
            walk: Its `style` walk.
            fold: Its variable folds.

        """
        measured = self.measure(section, frame, walk)
        whole = True
        while True:
            if frame.accepts(measured.height):
                self.commit(measured, frame)
                return
            if section.split:
                cut = choose(measured, frame.available, section.orphans, section.widows)
                if cut is not None:
                    measured = self.cut(measured, cut, frame)
                    whole = False
                    continue
            if fits(measured.height, frame.height):
                self.eject(frame, "column", fold if whole else None)
                if whole:
                    measured = self.measure(section, frame, walk)
                continue
            if section.split:
                cut = choose(measured, frame.available, legal=False)
                if cut is not None:
                    measured = self.cut(measured, cut, frame)
                    whole = False
                    continue
            if self.context.page_number == self.sheet.placed_on:
                # Only another page's headers can make more room: every
                # column left on this one begins no higher than this one.
                self.eject(frame, "page", fold if whole else None)
                if whole:
                    measured = self.measure(section, frame, walk)
                continue
            self.overflow(section, measured, frame.height)
            while not frame.empty:
                self.eject(frame, "column", fold if whole else None)
                if whole:
                    measured = self.measure(section, frame, walk)
            self.commit(measured, frame, overflowed=True)
            return

    def cut(self, measured: Measurement, cut: float, frame: Frame) -> Measurement:
        """Commit a band's head, eject, and return its tail.

        doc/layout.md#splitting: the tail is carried over the eject
        rather than measured again, since measuring it again would
        ask its expressions a second question.

        Args:
            measured: The band.
            cut: Where it is cut, band-relative.
            frame: The frame it belongs to.

        """
        head, tail = split(measured, cut)
        self.commit(head, frame)
        self.left_alone(frame)
        self.eject(frame, "column", None)
        return tail

    def overflow(self, section: Section, measured: Measurement, room: float) -> None:
        """Report a band that fits no frame and cannot be cut into one.

        The `overflow` of doc/layout.md#errors, which ``--allow-overflow``
        downgrades to a warning.  The band is then placed at the top of
        a frame all the same, and the warning travels in the printout
        header, so an overflowing document is identifiable from the artifact.

        Args:
            section: The band.
            measured: What measuring it produced.
            room: The most any frame offers it.

        Raises:
            BuildError: Nothing allowed it.

        """
        said = (
            f"the band measures {number(measured.height)} pt and the largest"
            f" frame offers {number(room)} pt, and it cannot be cut"
        )
        if not self.build.allow_overflow:
            raise BuildError(
                said,
                Location(
                    file=self.file,
                    path=section.path,
                    record=self.context.record_index,
                ),
            )
        self.warnings.append(
            BuildWarning(
                "overflow",
                said,
                node=str(section.path),
                record=self.context.record_index,
            )
        )

    def commit(
        self,
        measured: Measurement,
        frame: Frame,
        overflowed: bool = False,
    ) -> None:
        """Translate a measured band's marks onto the page.

        A mark that lands outside the page's printable area is the
        other [overflow](doc/layout.md#errors), and it is judged as
        the page ends, by :meth:`judge`, once nothing moves it again.

        A band an inline subreport commits is something of its host's
        band on the page too, so each host in the chain begins the runs
        its band begins, as it would on committing a band of its own.
        Its columns come off the host's frame once any of its summary
        is committed, so no eject after that draws their footer over it.

        Args:
            measured: What measuring the band produced.
            frame: The frame it goes in.
            overflowed: Whether it fit no frame, and was reported so.

        """
        down = frame.fill
        start = self.emit(measured, frame, frame.x, down, overflowed)
        self.record(frame, start, down, measured.height)
        frame.advance(round_points(down + measured.height))
        self.sheet.placed_on = self.context.page_number
        for builder in self.chain():
            builder.begun(builder.beginning)
        if self.grafted is not None and self.grafted.ending:
            self.ungraft()

    def emit(
        self,
        measured: Measurement,
        frame: Frame,
        across: float,
        down: float,
        overflowed: bool = False,
    ) -> int:
        """Put a measured band's marks on the page, and register its deferrals.

        Every band that reaches the page comes through here: a
        committed one, a header, a footer, and a swapped title or summary.
        A band that is only measured -- a reservation, a lookahead,
        a measurement an eject throws away -- never does, so
        it registers nothing.

        Its outline entry follows its marks, and its xrefs to outline
        entries are noted for the end of the document, which is when
        whether an entry claims each of them is known.

        A `column` deferral leaves the page's columns as the fill
        put them, in the frame it was placed in and every frame
        around it: it is resolved as its column ends, against that
        column, and balancing would move what it counted.

        Args:
            measured: The band.
            frame: The frame it belongs to.
            across: Where its left edge goes.
            down: Where its top edge goes.
            overflowed: Whether it fit no frame, and was reported so.

        Returns:
            Where its first mark is among the page's.

        """
        start = len(self.marks)
        self.marks.extend(mark.moved(across, down) for mark in measured.marks)
        origins = [extent.origin for extent in measured.extents]
        if measured.outline is not None:
            self.marks.append(measured.outline.moved(across, down))
            origins.append(Origin(measured.band))
        record = self.context.record_index
        self.sheet.emitted.append(
            Emitted(start, tuple(origins), record, self.file, overflowed)
        )
        for link in measured.links:
            self.document.targets.setdefault(
                link.target, Target(link.path, record, self.file)
            )
        for deferral in measured.deferred:
            self.deferred.add(deferral, len(self.pages), start)
            if deferral.scope == COLUMN:
                self.left_alone(frame)
        return start

    def resolve(self, scopes: list[Scope] | None) -> None:
        """Set the values of the deferrals whose scopes have just ended.

        ``FINAL`` is bound once, to the report as it stands,
        and each deferral is resolved in the order it was placed.
        Its placeholder's mark is wherever the page now has it,
        on the page being built or on one already made, and
        the resolved mark takes its place.

        Args:
            scopes: The scopes that ended, or ``None`` for every scope,
                at the end of the report.

        """
        due = self.deferred.due(scopes)
        if not due:
            return
        ending = final(self.context)
        made: dict[int, list[Mark]] = {}
        for waiting in due:
            if waiting.page == len(self.pages):
                marks = self.marks
            elif waiting.page in made:
                marks = made[waiting.page]
            else:
                marks = made[waiting.page] = list(self.pages[waiting.page].marks)
            placeholder = find(marks, waiting.path)
            resolved = self.measurer.resolve(waiting.deferral, ending, placeholder)
            swap(marks, waiting.path, resolved)
        for index, marks in made.items():
            self.pages[index] = replace(self.pages[index], marks=tuple(marks))

    def ejects(self, section: Section, frame: Frame, fold: Fold | None = None) -> None:
        """Test a band's `eject` nodes, and eject where the selected one says.

        doc/template.md#eject: the first node whose ``when`` holds is
        selected and the search stops there.  Without a ``require``
        it ejects; with one, only where less than that remains, and not
        from an empty column, to which no eject could give more room.

        Args:
            section: The band.
            frame: The frame it belongs to.
            fold: Its variable folds.

        """
        chosen = self.selected(section, frame)
        if chosen is None:
            return
        if chosen.require is None:
            self.eject(frame, chosen.kind, fold, deliberate=True, room=False)
        elif not frame.empty and not fits(chosen.require, frame.available):
            self.eject(frame, chosen.kind, fold, deliberate=True)

    def selected(self, section: Section, frame: Frame) -> Eject | None:
        """Return the band's selected `eject` node, where one is.

        Args:
            section: The band.
            frame: The frame it belongs to.

        """
        if not section.ejects:
            return None
        names = self.measurer.names(frame.window(), self.context)
        for node in section.ejects:
            if condition(node.when, names, node.path, self.context, "when"):
                return node
        return None

    # -- subreports -------------------------------------------------------

    def invoke(
        self,
        subreport: Subreport,
        frame: Frame,
        position: tuple[float, float] | None = None,
    ) -> None:
        """Run one subreport of the band being placed.

        Everything it is given is evaluated in this builder's context
        as it stands, per doc/layout.md#names-and-values: its ``when``,
        its ``data``, and its `arg` nodes.  ``VERTICAL_POSITION`` and
        ``VERTICAL_SPACE`` there are the host band's: as it was measured,
        once it is committed, and where it would begin, before.

        Args:
            subreport: The node.
            frame: The frame the host band belongs to.
            position: ``VERTICAL_POSITION`` and ``VERTICAL_SPACE`` as the
                host band was measured, once it is; the frame's otherwise.

        Raises:
            BuildError: Its data is not a sequence of records, an `arg`
                is not of its parameter's type, or it is nested too deep.

        """
        if position is None:
            names = self.measurer.names(frame.window(), self.context)
        else:
            names = self.context.environment(*position)
        if not condition(subreport.when, names, subreport.path, self.context, "when"):
            return
        if self.depth >= DEPTH:
            raise BuildError(
                f"subreports are nested {DEPTH} deep,"
                " which is as far as this engine goes",
                Location(
                    file=self.file,
                    path=subreport.path,
                    record=self.context.record_index,
                ),
            )
        where = Location(
            file=self.file,
            path=subreport.path,
            prop="data",
            record=self.context.record_index,
        )
        assert subreport.data is not None
        target = target_of(self.template.report, subreport)
        data = evaluate(subreport.data, names, subreport.path, self.context, "data")
        records = records_of(data, target.records, where)
        parameters = arguments(subreport, target.parameters, names, self.context)
        template = (
            self.template
            if subreport.report is None
            else self.document.template_of(target.report, parameters)
        )
        assert target.report.layout is not None

        def nested(host: Builder | None, base: Frame | None, first: int) -> Nested:
            assert target.report.layout is not None
            return Nested(
                document=self.document,
                template=template,
                nesting=target.nesting,
                outward=target.outward,
                records=records,
                parameters=parameters,
                variables=target.variables,
                paper=target.report.layout.paper,
                host=host,
                base=base,
                page_number=first,
                depth=self.depth + 1,
            )

        if subreport.inline:
            child = Builder(self.build, nested(self, frame, self.context.page_number))
            child.invocation()
            return
        folds = [one.pending for one in self.chain() if one.pending is not None]
        for one in folds:
            one.undo()
        self.left_alone(frame)
        self.end_page()
        first = 1 if subreport.ownpageno else self.context.page_number + 1
        child = Builder(self.build, nested(None, None, first))
        child.invocation()
        resumed = self.context.page_number + 1
        if not subreport.ownpageno:
            resumed = child.context.page_number + 1
        self.start_page(resumed)
        for one in folds:
            one.redo()

    # -- the report's own title and summary, swapped ----------------------

    def swapped_title(self, section: Section) -> None:
        """Place a ``swapheader`` title above the first page's header.

        doc/layout.md#swapheader-and-swapfooter: it goes at the top
        of the page frame, and that page's header is reserved below it.
        Every frame on the page begins that much lower than it will on
        the next, which is what keeps the columns below it from counting
        as empty.  The page's frames are opened here, below it, and its
        `eject` nodes are tested after it, where it prints.

        A title taller than the page frame is an overflow, and where
        that is allowed it is placed at the top of the page frame instead,
        below the page's header, as a title that is not swapped would be.

        Args:
            section: The title.

        """
        paper = self.paper
        page = self.sheet.page
        window = Window(page.x, page.width, paper.top, page.outer_bottom)
        if not self.measurer.prints(section, window, self.context):
            self.open_frames()
            return
        measured = self.measurer.band(
            section, window, self.context, self.walk(section, self.levels[0])
        )
        assert measured is not None
        if fits(measured.height, window.height):
            self.emit(measured, page, page.x, paper.top)
            page.outer_top = round_points(paper.top + measured.height)
            for frame in self.frames():
                frame.lead = measured.height
            self.open_frames()
        else:
            self.overflow(section, measured, window.height)
            self.open_frames()
            self.commit(measured, page, overflowed=True)
        self.ejects(section, page)

    def swapped_summary(self, section: Section) -> None:
        """Place a ``swapfooter`` summary below the last page's footer.

        It is placed like any other band -- a page eject first where what
        remains above the enlarged bottom reservation is already filled --
        but at the bottom of the page frame, and the page's footer and
        every column footer go immediately above it.  So what it needs
        is room above the columns' footers as well as the page's.

        A summary that fits no page is an overflow, and where that is
        allowed it is placed at the top of the page frame instead, as
        a summary that is not swapped would be.  Its own page eject is
        the one carry a band that fits nowhere gets, so it is not made
        from a page an eject has begun with nothing on it yet, as a page
        the summary's `eject` nodes have taken it to is.

        Args:
            section: The summary.

        """
        page = self.sheet.page
        walk = self.walk(section, self.levels[0])
        if not self.measurer.prints(section, page.window(), self.context):
            return
        self.ejects(section, page)
        measured = self.measure(section, page, walk)
        placed = self.context.page_number == self.sheet.placed_on
        if not page.accepts(measured.height) and placed:
            self.eject(page, "page", None)
            measured = self.measure(section, page, walk)
        if not page.accepts(measured.height):
            self.overflow(section, measured, page.height)
            self.commit(measured, page, overflowed=True)
            return
        down = round_points(page.outer_bottom - measured.height)
        self.emit(measured, page, page.x, down)
        page.advance(page.outer_bottom)
        page.outer_bottom = down
        page.bottom = round_points(down - page.reserved_footer)
        for frame in page.descendants():
            assert frame.parent is not None
            frame.outer_bottom = frame.parent.bottom
            frame.bottom = round_points(frame.outer_bottom - frame.reserved_footer)

    # -- ejects -----------------------------------------------------------

    def eject(
        self,
        frame: Frame,
        kind: str,
        fold: Fold | None,
        *,
        deliberate: bool = False,
        room: bool = True,
    ) -> None:
        """End the current column or page, rolling a band's fold back around it.

        doc/layout.md#which-frames-participate: a column eject goes to the
        nearest frame with a column left, and one looking for room passes
        over a frame whose next column would offer the band none.  In that
        column the band begins at the frame's floor, or where it began in
        this one, whichever is lower, so it gains nothing where its column
        is filled no lower than that.

        The walk goes on into the host's frames from an inline subreport's,
        and the host bands whose negative-seq subreports are running
        roll back with the band, since none of them is placed yet.

        Args:
            frame: The frame of the band that triggered it.
            kind: ``column`` or ``page``.
            fold: The band's folds, undone before and applied again after.
            deliberate: Whether an `eject` node or a keep-together rule
                asked for it, rather than a band that did not fit.
            room: Whether it is looking for room, which every eject is
                except an `eject` node's without a ``require``.

        """
        folds = [one.pending for one in self.chain() if one.pending is not None]
        if fold is not None:
            folds.append(fold)
        for one in folds:
            one.undo()
        ejecting: Frame | None = frame
        if kind == "column":
            while ejecting is not None and not (
                ejecting.spare
                and (not room or frame.fill > max(ejecting.floor, frame.start))
            ):
                ejecting = ejecting.parent
        if kind == "page" or ejecting is None:
            self.page_eject()
        else:
            if deliberate:
                self.left_alone(frame)
            self.column_eject(ejecting)
        for one in folds:
            one.redo()

    def column_eject(self, ejecting: Frame) -> None:
        """Advance a frame to its next column.

        doc/layout.md#sequence: the footers of the frame and every frame
        inside it, innermost first; the column's scopes end; the frame
        moves one column across and every frame inside it starts again
        in its first; then the headers, outermost first.

        The scopes that end are those of the frame's owner and of
        every inline subreport printing inside it, which is each
        builder in the chain from that owner on: a subreport's
        own columns move on without its host's.

        Args:
            ejecting: The frame that has a column left.

        """
        participants = [ejecting, *ejecting.descendants()]
        chain = self.chain()
        ended = chain[chain.index(self.owner(ejecting)) :]
        self.footers(participants, [COLUMN], ended)
        for builder in ended:
            builder.context.column_count = 0
            builder.variables.clear("column")
            builder.variables.iterate("column")
        ejecting.column += 1
        for frame in ejecting.descendants():
            frame.column = 0
            frame.floor = ejecting.floor
        self.sheet.column_number += 1
        self.set_column_number()
        self.open_columns(participants)

    def page_eject(self) -> None:
        """End the page and start the next.

        doc/layout.md#sequence: a page eject ends a column as well,
        so the column's scopes end first, then the page's.  A group
        that is still opening does not count the page it is leaving:
        its run begins on the page its first band lands on.

        """
        self.end_page()
        self.start_page(self.context.page_number + 1)

    def end_page(self) -> None:
        """End the page: its footers, its scopes, and its marks.

        The first half of a page eject, and all of it that comes before
        a subreport with pages of its own, which the page ends for.

        """
        chain = self.chain()
        self.settle_columns()
        self.footers(self.frames(), [COLUMN, PAGE], chain)
        for builder in chain:
            builder.context.column_count = 0
            builder.variables.clear("column")
            builder.variables.iterate("column")
        self.close_page()

    def start_page(self, page_number: int) -> None:
        """Start the next page: its number, its scopes, and its headers.

        The second half of a page eject, and the page a
        host resumes on after a subreport with pages of its own.

        Args:
            page_number: Its ``PAGE_NUMBER``.

        """
        for builder in self.chain():
            context = builder.context
            context.page_number = page_number
            context.page_count = 0
            builder.variables.clear("page")
            builder.variables.iterate("page")
            for run in builder.runs:
                if not run.opening:
                    context.group_pages[builder.name(run)] += 1
        self.new_page()
        self.open_frames()

    def close_page(self) -> None:
        """Add the page being built to the document.

        A page carries the geometry it runs at where that is not
        the document's own, as a subreport's with pages of its own can.
        Its marks are judged against its margins first.

        """
        self.judge()
        paper = self.sheet.paper
        override = None
        if paper != self.document.paper:
            override = Paper(
                paper.width,
                paper.height,
                paper.left,
                paper.right,
                paper.top,
                paper.bottom,
            )
        self.pages.append(Page(self.context.page_number, tuple(self.marks), override))

    def judge(self) -> None:
        """Hold the page's marks to its printable area.

        doc/layout.md#errors: a mark that lands outside the page's
        printable area is an overflow, which ``--allow-overflow``
        downgrades to a warning.  It is judged on the page coordinates
        the mark ends up at, so as the page ends, once balancing
        has moved what it moves; a deferred value set later stays
        inside the room its placeholder took, so nothing after this
        moves a mark outward.

        A band is reported once, for the first of its marks in paint
        order that lies outside, against the node that mark came from
        and the record the band was built for.  A mark inside an xref
        came from one of the xref's children, and names that child.
        One already reported for fitting no frame is not reported again:
        it was placed knowing that it runs past its frame, and its
        warning says so.

        Raises:
            BuildError: A mark lies outside, and nothing allowed it.

        """
        sheet = self.sheet
        paper = sheet.paper
        area = Area.of(
            paper.width, paper.height, paper.left, paper.right, paper.top, paper.bottom
        )
        for band in sheet.emitted:
            if band.overflowed:
                continue
            for offset, origin in enumerate(band.origins):
                found = outside(sheet.marks[band.start + offset], origin, area)
                if found is None:
                    continue
                kind, crossed, node = found
                said = described(kind, crossed)
                if not self.build.allow_overflow:
                    raise BuildError(
                        said, Location(file=band.file, path=node, record=band.record)
                    )
                self.warnings.append(
                    BuildWarning(
                        "overflow",
                        said,
                        node=str(node) if node is not None else None,
                        record=band.record,
                    )
                )
                break

    def outlined(self) -> None:
        """Settle the document's outline, once every page is made.

        doc/printout.md#xref: every xref to an outline entry finds an
        entry of that name somewhere in the document, and one that
        finds none is an error naming the xref, since a renderer would
        refuse it.  doc/printout.md#outline: an entry keeps its `name`
        only where some xref targets it, and its level is written as
        its depth in the tree.  An entry's parent is the nearest entry
        before it whose declared level is lower, and its depth is one more
        than its parent's, or 1 where it has none.  That leaves every level
        a template declares without a gap as it is, and turns one with
        a gap into the depth a reader would give it, so no level is more
        than one deeper than the level before it.

        Raises:
            BuildError: An xref names no outline entry.

        """
        pages = self.pages
        named = {
            mark.name
            for page in pages
            for mark in page.marks
            if isinstance(mark, Outline) and mark.name is not None
        }
        targets = self.document.targets
        for name, target in targets.items():
            if name not in named:
                raise BuildError(
                    f"no outline is named {name!r}",
                    Location(
                        file=target.file,
                        path=target.path,
                        prop="target",
                        record=target.record,
                    ),
                )
        # The entries a later one may still be the child of, as each one's
        # declared level and the depth it was written at, outermost first.
        ancestors: list[tuple[int, int]] = []
        for index, page in enumerate(pages):
            marks = list(page.marks)
            changed = False
            for at, mark in enumerate(marks):
                if not isinstance(mark, Outline):
                    continue
                while ancestors and ancestors[-1][0] >= mark.level:
                    ancestors.pop()
                level = ancestors[-1][1] + 1 if ancestors else 1
                ancestors.append((mark.level, level))
                kept = mark.name if mark.name in targets else None
                if level != mark.level or kept != mark.name:
                    marks[at] = replace(mark, level=level, name=kept)
                    changed = True
            if changed:
                pages[index] = replace(page, marks=tuple(marks))

    def new_page(self) -> None:
        """Start an empty page, every frame back at its first column."""
        sheet = self.sheet
        sheet.marks = []
        sheet.emitted = []
        sheet.fragments = {}
        paper = sheet.paper
        page = sheet.page
        page.x = paper.left
        page.width = round_points(paper.width - paper.left - paper.right)
        page.outer_top = paper.top
        page.outer_bottom = round_points(paper.height - paper.bottom)
        for frame in self.frames():
            frame.column = 0
            frame.floor = 0.0
            frame.lead = 0.0
            frame.begins = None
        sheet.column_number = 1
        self.set_column_number()

    def open_frames(self) -> None:
        """Open every frame's first column, outermost first."""
        self.open_columns(self.frames())

    def open_columns(self, frames: list[Frame]) -> None:
        """Open these frames' next columns, and note where each begins.

        A band in an empty column of one of them begins below the headers
        the columns inside it drew, which is lower than their reservations
        where a header measured taller when it was built than when it was
        reserved: doc/layout.md#extent-and-fill.

        Each is opened by the builder it belongs to, in that one's context.

        Args:
            frames: A frame and every frame inside it, outermost first.

        """
        drawn = {}
        for frame in frames:
            drawn[frame] = self.owner(frame).open_column(frame)
        for frame in frames:
            inside = [drawn[one] for one in frame.descendants() if one in drawn]
            frame.start = max([frame.top, *inside])

    def open_column(self, frame: Frame) -> float:
        """Reserve a column's header and footer, and place its header.

        doc/layout.md#headerfooter-reservation measures both bands against
        the context as it stands when the column begins, which is when
        they are reserved.  The header is then built again and placed.

        Args:
            frame: The frame whose current column opens.

        Returns:
            How far down the header was drawn: its bottom edge,
            or the column's top edge where it drew nothing.

        Raises:
            BuildError: The two reservations together exceed the column.

        """
        whole = self.column_window(frame)
        walk = self.walk_of(frame)
        header = self.reserve(frame.header, whole, walk)
        footer = self.reserve(frame.footer, whole, walk)
        if round_points(header + footer) > whole.height:
            raise BuildError(
                "the header and footer reservations together exceed the frame",
                Location(file=self.file, path=frame.path),
            )
        frame.settle(header, footer)
        frame.drawn = frame.outer_top
        if frame.header is None:
            return frame.outer_top
        above = Window(frame.x, frame.width, frame.outer_top, frame.bottom)
        measured = self.measurer.band(
            frame.header, above, self.context, (frame.header.styles, *walk)
        )
        if measured is None:
            return frame.outer_top
        self.emit(measured, frame, frame.x, frame.outer_top)
        drawn = round_points(frame.outer_top + measured.height)
        frame.reach(drawn)
        frame.drawn = drawn
        return drawn

    @staticmethod
    def column_window(frame: Frame) -> Window:
        """Place a frame's current column, and return it whole.

        A grafted frame is placed where a band of its parent would be:
        below whatever else its parent holds has drawn above it,
        or where its parent was filled when the invocation began,
        and above whatever else its parent holds draws below.

        Args:
            frame: The frame whose current column opens.

        Returns:
            The column before its header and footer are taken out of it.

        """
        parent = frame.parent
        if parent is not None:
            frame.place_across()
            frame.outer_top = parent.top
            frame.outer_bottom = parent.bottom
            if frame.grafted:
                beside = [
                    inner
                    for one in parent.children
                    if one is not frame
                    for inner in (one, *one.descendants())
                ]
                frame.outer_top = (
                    frame.begins
                    if frame.begins is not None
                    else max([parent.top, *(one.drawn for one in beside)])
                )
                frame.outer_bottom = min(
                    [parent.bottom, *(one.bottom for one in beside)]
                )
        return Window(frame.x, frame.width, frame.outer_top, frame.outer_bottom)

    def room_for(self, frame: Frame) -> bool:
        """Report whether a grafted frame's columns would open where it begins.

        Their reservations are measured as :meth:`open_column`
        measures them, each frame inside the grafted one
        in the room its parent leaves, and nothing is drawn.

        Args:
            frame: The grafted frame, not on the host's frame yet.

        """
        for one in [frame, *frame.descendants()]:
            whole = self.column_window(one)
            walk = self.walk_of(one)
            header = self.reserve(one.header, whole, walk)
            footer = self.reserve(one.footer, whole, walk)
            if round_points(header + footer) > whole.height:
                return False
            one.settle(header, footer)
        return True

    def reserve(self, section: Section | None, window: Window, walk: Walk) -> float:
        """Return the height a header or footer reserves.

        Args:
            section: The band, where there is one.
            window: The column before either has been taken out of it.
            walk: The `style` walk of the frame's bands.

        """
        if section is None:
            return 0.0
        measured = self.measurer.band(
            section, window, self.context, (section.styles, *walk)
        )
        return 0.0 if measured is None else measured.height

    def footers(
        self,
        frames: list[Frame],
        ended: list[Scope] | None,
        builders: list[Builder],
    ) -> None:
        """Build the footers of these frames and place them, innermost first.

        A footer's space is settled when its column opens and its content
        last, against the **outgoing** context, which is what lets a page
        footer report the page it sits on.  It is flush with the frame's
        bottom edge rather than with its reservation: the two are the
        same place whenever the two measurements agree, and a footer that
        measured taller than it reserved grows upward, into the content,
        rather than off the paper.

        ``VERTICAL_POSITION`` is how far the column was filled, and
        ``VERTICAL_SPACE`` the strip reserved for the footer.

        At a group break, with nothing of the new record on the page yet,
        the outgoing context is the one the break's summaries were built
        in: the page ends the runs that ended.

        The scopes the eject ends resolve after the footers,
        since each footer is placed in the scope it closes,
        and against the same context: ``FINAL`` reads
        what the footers read.

        Each footer is built by the builder its frame belongs to,
        and each builder whose scopes end resolves its own deferrals,
        innermost first as the footers are.

        Args:
            frames: The participating frames, outermost first.
            ended: The scopes the eject ends, or ``None`` for every
                scope, at the end of the report.
            builders: Those whose scopes end, outermost first.

        """
        for frame in reversed(frames):
            owner = self.owner(frame)
            with owner.before_the_break():
                owner.build_footer(frame)
        for builder in reversed(builders):
            with builder.before_the_break():
                builder.resolve(ended)

    def build_footer(self, frame: Frame) -> None:
        """Build one frame's footer and place it.

        Args:
            frame: The frame, which is this builder's.

        """
        section = frame.footer
        if section is None:
            return
        below = Window(
            frame.x,
            frame.width,
            frame.top,
            round_points(frame.fill + frame.reserved_footer),
        )
        below.fill = frame.fill
        measured = self.measurer.band(
            section,
            below,
            self.context,
            (section.styles, *self.walk_of(frame)),
        )
        if measured is None:
            return
        down = round_points(frame.outer_bottom - measured.height)
        self.emit(measured, frame, frame.x, down)
        frame.reach(frame.outer_bottom)

    def state(self) -> tuple[Any, ...]:
        """Return the context a footer at a group break is built against.

        Taken once the break's summaries have been placed and before
        anything starts again: the previous record, every group's counts
        as its ended run left them, and the accumulators as they stood.

        """
        context = self.context
        return (
            context.record,
            context.item_number,
            dict(context.group_counts),
            dict(context.group_pages),
            self.variables.snapshot(),
        )

    def put(self, state: tuple[Any, ...]) -> None:
        """Put back a context :meth:`state` returned.

        Args:
            state: What it returned.

        """
        record, item, counts, pages, accumulators = state
        context = self.context
        context.record, context.item_number = record, item
        context.group_counts, context.group_pages = dict(counts), dict(pages)
        self.variables.restore(accumulators)

    @contextmanager
    def before_the_break(self) -> Iterator[None]:
        """Build what is inside against the ended runs, at a group break."""
        if self.outgoing is None:
            yield
            return
        now = self.state()
        self.put(self.outgoing)
        try:
            yield
        finally:
            self.put(now)

    def set_column_number(self) -> None:
        """Set ``COLUMN_NUMBER`` for every builder printing on the sheet.

        doc/expressions.md#predefined-variables counts the columns the
        page has opened: it is 1 on a new page and goes up by one at each
        column break, whichever frame breaks.  So a block inside another
        block's columns numbers on across them, and a subreport's grafted
        columns go on from the column the host was in.  Every builder on
        the sheet reads the same, the host as well as the subreport,
        and the count does not go back when the grafted columns come off.

        """
        for builder in self.chain():
            builder.context.column_number = self.sheet.column_number

    # -- balancing --------------------------------------------------------

    def record(self, frame: Frame, start: int, down: float, height: float) -> None:
        """Note a committed band against every balanced frame it concerns.

        doc/layout.md#balanced-columns balances what a frame was given since
        the page opened.  A band in a frame inside a balanced one belongs to
        the innermost balanced frame above it; one placed in a frame outside
        it, once the fragment has begun, leaves the fragment where it is.

        A band of an inline subreport leaves every balanced frame of its
        host's that holds it where it is, since the host has no way to carry
        it along: it is the subreport's band and not one of the host's.

        Args:
            frame: The frame the band went in.
            start: The index of its first mark on the page.
            down: The Y it was placed at.
            height: Its height.

        """
        if self.base is not None:
            self.left_alone(self.base)
        fragments = self.sheet.fragments
        for balanced in self.frames():
            if not (balanced.balance and balanced.count > 1):
                continue
            fragment = fragments.setdefault(balanced, Fragment(balanced))
            if frame is balanced or balanced in frame.ancestors():
                if any(between.count > 1 for between in frame_path(balanced, frame)):
                    fragment.alone = True
                    continue
                fragment.add(balanced.column, start, len(self.marks), down, height)
            elif fragment.bands:
                fragment.alone = True

    def left_alone(self, frame: Frame) -> None:
        """Leave the fragment of every balanced frame at or above ``frame`` as it is.

        A band split, and an eject an `eject` node or a keep-together
        rule caused that stays on the page, each decide something about
        where a band goes that packing by height would not reproduce,
        and so does a subreport.

        Args:
            frame: The frame it happened in.

        """
        fragments = self.sheet.fragments
        for balanced in (frame, *frame.ancestors()):
            fragment = fragments.get(balanced)
            if fragment is not None:
                fragment.alone = True
            elif balanced.balance and balanced.count > 1:
                fragments[balanced] = Fragment(balanced, alone=True)

    def settle_columns(self, frames: list[Frame] | None = None) -> None:
        """Balance every balanced frame's fragment on the page as it ends.

        A fragment is balanced once.  The report's summary can still
        eject after the pass at the end of the report, and the page
        it leaves is not balanced a second time: the fragment's record of
        where its bands went is the fill's, and they are no longer there.

        A frame whose columns' footers hold a `column` deferral is
        never balanced.  The last column's footer is placed after
        the pass, so its deferral would count the rows the fill put
        in that column and sit under the rows the pass put there.

        Args:
            frames: The frames to balance, which at the end of an inline
                subreport are its own; every frame on the sheet otherwise.

        """
        for frame in reversed(self.frames() if frames is None else frames):
            fragment = self.sheet.fragments.get(frame)
            if fragment is None or fragment.alone or not fragment.bands:
                continue
            if footer_waits_for_column(frame):
                continue
            opened = not any(
                one.header is not None or one.footer is not None
                for one in (frame, *frame.descendants())
            )
            balance(fragment, self.marks, opened)
            fragment.alone = True

    # -- the document -----------------------------------------------------

    def paper_of(self) -> Paper:
        """Return the document's page geometry."""
        page = self.document.paper
        return Paper(
            page.width, page.height, page.left, page.right, page.top, page.bottom
        )

    def printout(self) -> Printout:
        """Return the finished document.

        The font table holds the fonts that were used rather than every
        one declared, per doc/printout.md#fonts: a font some measured
        element's style walk resolved to, in whichever template.
        Every declared font of the report was still resolved at load, so
        an unusable one is refused all the same.  Each used `font` node
        is its own entry, under its own name, even where another resolves
        to the same face at the same size.  What resolving a font had
        to say is carried only for a font the table lists, so no warning
        names a font the document does not have.

        The `data` table holds the entries something in the document
        refers to, per doc/printout.md#data: an image mark on a page,
        or a font in the font table.  A name an image took in a band
        that never reached the page stays taken, and is not written.

        """
        document = self.document
        shared = document.shared
        fonts = shared.fonts.listed()
        referenced = [one.data for one in fonts if one.data is not None]
        referenced.extend(blobs_in(mark for page in self.pages for mark in page.marks))
        report = self.build.report
        # A group is listed once it has opened, and not before: over no
        # records at all the header carries neither table.
        names = sorted(name for name, tally in document.tallies.items() if tally.runs)
        tallies = document.tallies
        return Printout(
            report=ReportMeta(
                report.name, report.description, report.version, report.author
            ),
            built=self.build.built.format(RFC3339),
            engine=meta.engine(),
            strict_fonts=self.build.strict_fonts,
            paper=self.paper_of(),
            fonts=fonts,
            data=document.table.written(referenced),
            pages=tuple(self.pages),
            warnings=(
                tuple(self.build.warnings)
                + shared.fonts.warnings()
                + tuple(shared.warnings)
                + tuple(document.warnings)
            ),
            group_runs={name: tallies[name].runs for name in names},
            group_keys={name: len(tallies[name].keys) for name in names},
        )


def referenced(report: Report) -> list[Report]:
    """Return every other document a report's subreports name, once each.

    Args:
        report: The report.

    """
    found: list[Report] = []
    seen = {id(report)}
    pending = [report]
    while pending:
        here = pending.pop(0)
        for subreport in subreports_of(here):
            named = subreport.report
            if named is not None and id(named) not in seen:
                seen.add(id(named))
                found.append(named)
                pending.append(named)
    return found


def text_blobs(blobs: dict[str, bytes]) -> dict[str, str]:
    """Return the `data` nodes whose content a `field` can hold.

    A blob reaches a field as text, so one whose bytes are not text
    is left out and a field naming it is the error that says so.

    Args:
        blobs: A document's `data` nodes' contents, by name.

    """
    found: dict[str, str] = {}
    for name, content in blobs.items():
        try:
            found[name] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return found


def footer_waits_for_column(frame: Frame) -> bool:
    """Return whether a footer of a frame's columns holds a `column` deferral.

    The footers of the frames inside it are its columns' footers too.

    Args:
        frame: The frame.

    """
    return any(
        one.footer is not None and waits_for(one.footer.elements, COLUMN)
        for one in (frame, *frame.descendants())
    )


def first_change(keys: list[Any], before: list[Any] | None) -> int | None:
    """Return the position of the outermost group whose key changed.

    doc/layout.md#the-record-loop: the outermost group whose key is not
    the previous record's breaks, and every group inside it with it.
    The first record, which has none before it, breaks them all,
    and ``None`` is no break at all.

    Args:
        keys: Every group's key for a record, outermost first.
        before: The previous record's; ``None`` for the first record.

    """
    if before is None:
        return 0 if keys else None
    for position, (key, previous) in enumerate(zip(keys, before, strict=True)):
        if key != previous:
            return position
    return None


def frame_path(top: Frame, bottom: Frame) -> list[Frame]:
    """Return the frames strictly below ``top`` down to ``bottom``.

    Args:
        top: The upper frame.
        bottom: A frame at or below it.

    """
    found: list[Frame] = []
    here: Frame | None = bottom
    while here is not None and here is not top:
        found.append(here)
        here = here.parent
    return found


def outside(
    mark: Mark,
    origin: Origin | None,
    area: Area,
) -> tuple[str, Crossing, NodePath | None] | None:
    """Return the first mark, this one or one inside it, outside the area.

    An xref's box is judged as any mark's, and so is every mark it holds,
    in paint order after it.  An outline entry is judged at its point.
    The answer is the mark's kind, where it crosses, and the node
    it came from, which for a mark inside an xref is the xref's child.

    Args:
        mark: The mark, in page coordinates.
        origin: The element it came from, where that is known.
        area: The page's printable area.

    """
    box = mark.box
    found = crossing(area, box.x, box.y, box.width, box.height)
    if found is not None:
        return mark.kind, found, None if origin is None else origin.path
    if isinstance(mark, Xref):
        inside = (None,) * len(mark.marks) if origin is None else origin.inside
        for one, within in zip(mark.marks, inside, strict=True):
            inner = outside(one, within, area)
            if inner is not None:
                return inner
    return None


def blobs_in(marks: Iterator[Mark] | tuple[Mark, ...]) -> Iterator[str]:
    """Yield the `data` entries these marks name, the ones inside xrefs too.

    Args:
        marks: The marks, in any order.

    """
    for mark in marks:
        if isinstance(mark, Image) and mark.data is not None:
            yield mark.data
        elif isinstance(mark, Xref):
            yield from blobs_in(mark.marks)
