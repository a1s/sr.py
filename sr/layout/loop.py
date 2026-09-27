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
that try the next page once.  An eject the band causes is a column eject,
and doc/layout.md#sequence is what one does: footers innermost first,
the scopes that ended, the advance, then headers outermost first.

A band that ejects has its fold rolled back first and applied again
after, so that no value is counted twice and none is lost to a reset
the eject fired: doc/layout.md#rollback.  A group title's fold is a band's
fold like a detail's, which is why a group opening at the top of a page
it was pushed onto counts its variables on that page and not the one before.

Three orders are settled here and each was read off the reference.

* **The marks of a page come out in the order their bands were built.**
  A footer is last because it is built last: it is built against the
  outgoing context, per doc/layout.md#what-a-header-or-footer-sees.
* **The counters count what has been printed**, so the first detail band
  is built with ``REPORT_COUNT`` at 0 and the second at 1.
* **Variables iterate before the band that reads them**, per
  doc/expressions.md#ordering-against-section-printing.

"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from sr import meta
from sr.errors import BuildError, BuildWarning, Location
from sr.expr import Record, Time
from sr.expr.golayout import RFC3339
from sr.fonts.resolve import Resolution
from sr.fonts.text import Metrics
from sr.layout.columns import Fragment, balance
from sr.layout.context import Context, condition, evaluate
from sr.layout.frame import Frame, Window
from sr.layout.measure import Measurement, Measurer
from sr.layout.place import choose, split
from sr.layout.variables import Variables
from sr.printout.model import FontEntry, Mark, Page, Paper, Printout
from sr.printout.model import Report as ReportMeta
from sr.printout.write import number
from sr.template.model import Eject, Group, Layout, Nesting, Report, Section, Style
from sr.units import fits, round_points

__all__ = ["Build", "Builder"]

# A `style` walk: one tuple of nodes per scope, innermost first.
Walk = tuple[tuple[Style, ...], ...]


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


@dataclass
class Level:
    """One level of the band tree: the layout, or one group.

    Attributes:
        nesting: The node: the `layout`, or a `group`.
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
class Run:
    """A group as the record loop stands: its key and its counts.

    Attributes:
        level: The level the group is.
        key: Its key for the current run, once it has opened.
        opening: Whether the run has begun with nothing of it placed:
            from the break until a band of it is committed, its title or
            a band inside it.  An eject then does not advance its page
            number, since the run begins on the page that band lands on.
        tailed: Whether the current run has had its ``mintailrows`` test.
        runs: How many times it has opened.
        keys: The distinct keys it has seen.

    """

    level: Level
    key: Any = None
    opening: bool = False
    tailed: bool = False
    runs: int = 0
    keys: Keys = field(default_factory=Keys)


@dataclass
class Fold:
    """A band's variable folds, which an eject it causes rolls back.

    Attributes:
        before: The accumulators before the fold.
        apply: What folds them, run again after the eject.

    """

    before: dict[str, Any]
    apply: Callable[[], None]


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


class Builder:
    """Applies one template to one sequence of records.

    Attributes:
        build: What the run was asked for.
        layout: The template's `layout` node.
        context: The names in scope, as the report stands.
        measurer: What turns a band into marks.
        variables: The accumulators.
        page: The page frame, the root of the frame tree.
        levels: The layout and its groups, outermost first.
        runs: The groups' state, outermost first.

    """

    def __init__(self, build: Build) -> None:
        """Prepare a run.

        Args:
            build: What the run was asked for.

        Raises:
            BuildError: The template has no layout.

        """
        self.build = build
        report = build.report
        if report.layout is None:
            raise BuildError("the template has no layout", Location(file=report.file))
        self.layout: Layout = report.layout
        self.file = report.file
        self.metrics = {
            one.font.name: Metrics(one.face, one.font.size) for one in build.fonts
        }
        self.measurer = Measurer(self.metrics, self.text_blobs(), report.file)
        self.context = Context(
            parameters=dict(build.parameters),
            data_count=len(build.records),
            build_time=build.built,
            file=report.file,
        )
        self.variables = Variables(report.variables, self.context)
        self.page = Frame(
            header=self.layout.header,
            footer=self.layout.footer,
            path=self.layout.path,
        )
        self.levels = self.tree()
        self.runs = [Run(level) for level in self.levels[1:]]
        for run in self.runs:
            name = self.name(run)
            self.context.group_counts[name] = 0
            self.context.group_pages[name] = 1
        self.pages: list[Page] = []
        self.marks: list[Mark] = []
        self.warnings: list[BuildWarning] = []
        self.previous: tuple[Record, int] | None = None
        self.ahead: Ahead | None = None
        self.fragments: dict[Frame, Fragment] = {}
        # What a footer at a group break is built against, until a band
        # of the new record is committed, and the runs such a band begins.
        self.outgoing: tuple[Any, ...] | None = None
        self.beginning: list[Run] = []

    # -- the tree ---------------------------------------------------------

    def tree(self) -> list[Level]:
        """Build the frame tree and the levels that fill it.

        doc/layout.md#construction: the page frame, then a column frame
        for each `columns` node from the layout inward.  A level's title
        and summary belong to the frame that contains its columns.

        """
        levels: list[Level] = []
        nesting: Nesting | None = self.layout
        frame = self.page
        walk: Walk = ()
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
                outer.child = frame
                inner_walk = (columns.styles, *outer_walk)
            levels.append(Level(nesting, outer, frame, outer_walk, inner_walk))
            walk = inner_walk
            nesting = nesting.group
        return levels

    def frames(self) -> list[Frame]:
        """Return every frame, outermost first."""
        return [self.page, *self.page.descendants()]

    @staticmethod
    def name(run: Run) -> str:
        """Return a group's name."""
        group = run.level.group
        assert group is not None
        return group.name

    def walk_of(self, frame: Frame) -> Walk:
        """Return the `style` walk of a header or footer of this frame.

        Args:
            frame: The frame whose band it is.

        """
        if frame is self.page:
            return self.levels[0].outer_walk
        for level in self.levels:
            if level.inner is frame:
                return level.inner_walk
        raise AssertionError("a frame no level made")

    # -- the run ----------------------------------------------------------

    def run(self) -> Printout:
        """Build the document, and return it.

        Raises:
            BuildError: A band would not fit, or an expression failed.

        """
        # doc/expressions.md#the-report-boundary fires the report scope
        # "once, before the title band is built", and seeds every variable,
        # which reserving a header needs: a header may read one.
        self.variables.iterate("report")
        self.begin()
        for index, record in enumerate(self.build.records):
            self.row(index, record)
        self.end()
        return self.printout()

    def begin(self) -> None:
        """Open the first page, and place the report's title on it."""
        self.new_page()
        title = self.layout.title
        root = self.levels[0]
        if title is not None and title.swapheader:
            self.swapped_title(title)
            return
        self.open_frames()
        if title is not None:
            self.place(title, root.outer, self.walk(title, root), last=True)

    def end(self) -> None:
        """Close the open groups, place the summary, and finish the page."""
        self.close(0)
        self.settle_columns()
        summary = self.layout.summary
        root = self.levels[0]
        if summary is not None:
            if summary.swapfooter:
                self.swapped_summary(summary)
            else:
                self.place(summary, root.outer, self.walk(summary, root))
        self.footers(self.frames())
        self.pages.append(Page(self.context.page_number, tuple(self.marks)))

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

        Every group's key is evaluated against the new record, outermost
        first, and the outermost that changed breaks every group inside it.
        The first record breaks them all, and ``None`` is no break at all.
        Nothing is kept: :meth:`row` keeps every key for the next
        comparison, since a group nested in one that broke has its key
        taken all the same, and a lookahead asks first where a run ends.

        Args:
            index: The record's 0-based position.
            record: The record.

        """
        if not self.runs:
            return None, []
        kept = (self.context.record, self.context.item_number)
        self.context.record = record
        self.context.item_number = index + 1
        names = self.context.environment(0.0, 0.0)
        breaking: int | None = None
        keys: list[Any] = []
        for position, run in enumerate(self.runs):
            group = run.level.group
            assert group is not None and group.expr is not None
            key = evaluate(group.expr, names, group.path, self.context, "expr")
            if breaking is None and (self.previous is None or key != run.key):
                breaking = position
            keys.append(key)
        self.context.record, self.context.item_number = kept
        return breaking, keys

    def close(self, level: int) -> None:
        """Place the summaries of the groups from ``level`` in, innermost first.

        Each is built against the previous record's context, which is
        the one still in place: the record loop advances ``THIS`` after.

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
        self.context.record, self.context.item_number = kept

    def start(self, run: Run) -> None:
        """Begin a new run of a group at its break: its counts start again.

        Args:
            run: The group.

        """
        name = self.name(run)
        run.opening = True
        run.tailed = False
        run.runs += 1
        run.keys.add(run.key)
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
        )
        self.beginning = []
        self.context.report_count += 1
        self.context.page_count += 1
        self.context.column_count += 1
        for run in self.runs:
            self.context.group_counts[self.name(run)] += 1
        if self.ahead is not None:
            self.ahead.row()

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
        return Fold(before, apply)

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
            # Uncapped: what decides whether the test applies is how many rows
            # are left, and the row count bounds how far it looks.
            with self.lookahead(
                math.inf, group.mintailrows + 1, measuring=False
            ) as ahead:
                self.detail(index)
                self.run_ahead(index, position)
            if ahead.rows > group.mintailrows:
                continue
            run.tailed = True
            with self.lookahead(frame.height, None) as ahead:
                self.detail(index)
                self.run_ahead(index, position)
            wanted = min(ahead.total, frame.height)
            if not frame.empty and not fits(wanted, frame.available):
                self.eject(frame, "column", None, deliberate=True)
                return

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
        records = self.build.records
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
        and adds up instead.

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
                (run.key, run.opening, run.tailed, run.runs, run.keys.mark())
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
            run.key, run.opening, run.tailed, run.runs = key, opening, tailed, count
            run.keys.rewind(mark)
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
    ) -> bool:
        """Measure a band, decide where it goes, and commit it.

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
            return True
        if eject and not last:
            self.ejects(section, frame, fold)
        self.fit(section, frame, walk, fold)
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
        measured = self.measurer.band(
            section, frame.window(), self.context, walk, printing=True
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
        5. No eject has moved it yet: eject, and try it again from the
           first branch, since a later page may offer more room than
           this one where its headers take less.

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
        carried = False
        while True:
            if frame.accepts(measured.height):
                self.commit(measured, frame)
                return
            if section.split:
                cut = choose(measured, frame.available, section.orphans, section.widows)
                if cut is not None:
                    measured = self.cut(measured, cut, frame)
                    whole = False
                    carried = True
                    continue
            if fits(measured.height, frame.height):
                self.eject(frame, "column", fold if whole else None)
                carried = True
                if whole:
                    measured = self.measure(section, frame, walk)
                continue
            if section.split:
                cut = choose(measured, frame.available, legal=False)
                if cut is not None:
                    measured = self.cut(measured, cut, frame)
                    whole = False
                    carried = True
                    continue
            if not carried:
                self.eject(frame, "column", fold if whole else None)
                carried = True
                if whole:
                    measured = self.measure(section, frame, walk)
                continue
            self.overflow(section, measured, frame.height)
            while not frame.empty:
                self.eject(frame, "column", fold if whole else None)
                if whole:
                    measured = self.measure(section, frame, walk)
            self.commit(measured, frame)
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

    def commit(self, measured: Measurement, frame: Frame) -> None:
        """Translate a measured band's marks onto the page.

        A mark that lands outside the page's printable area is the
        other [overflow](doc/layout.md#errors), and it is not checked
        here yet: it is the same judgement the printout's seventh
        invariant makes, so it arrives with the invariant run rather
        than being written twice.

        Args:
            measured: What measuring the band produced.
            frame: The frame it goes in.

        """
        down = frame.fill
        start = len(self.marks)
        self.marks.extend(mark.moved(frame.x, down) for mark in measured.marks)
        self.record(frame, start, down, measured.height)
        frame.advance(round_points(down + measured.height))
        self.begun(self.beginning)

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
        paper = self.layout.paper
        page = self.page
        window = Window(page.x, page.width, paper.top, page.outer_bottom)
        if not self.measurer.prints(section, window, self.context):
            self.open_frames()
            return
        measured = self.measurer.band(
            section, window, self.context, self.walk(section, self.levels[0])
        )
        assert measured is not None
        if fits(measured.height, window.height):
            self.marks.extend(mark.moved(page.x, paper.top) for mark in measured.marks)
            page.outer_top = round_points(paper.top + measured.height)
            for frame in self.frames():
                frame.lead = measured.height
            self.open_frames()
        else:
            self.overflow(section, measured, window.height)
            self.open_frames()
            self.commit(measured, page)
        self.ejects(section, page)

    def swapped_summary(self, section: Section) -> None:
        """Place a ``swapfooter`` summary below the last page's footer.

        It is placed like any other band -- a page eject first where what
        remains above the enlarged bottom reservation is already filled --
        but at the bottom of the page frame, and the page's footer and
        every column footer go immediately above it.  So what it needs
        is room above the columns' footers as well as the page's.

        A summary that fits no page is an overflow, and where that is
        allowed it is placed at the top of the page frame instead,
        as a summary that is not swapped would be.

        Args:
            section: The summary.

        """
        page = self.page
        walk = self.walk(section, self.levels[0])
        if not self.measurer.prints(section, page.window(), self.context):
            return
        self.ejects(section, page)
        measured = self.measure(section, page, walk)
        if not page.accepts(measured.height):
            self.eject(page, "page", None)
            measured = self.measure(section, page, walk)
        if not page.accepts(measured.height):
            self.overflow(section, measured, page.height)
            self.commit(measured, page)
            return
        down = round_points(page.outer_bottom - measured.height)
        self.marks.extend(mark.moved(page.x, down) for mark in measured.marks)
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

        Args:
            frame: The frame of the band that triggered it.
            kind: ``column`` or ``page``.
            fold: The band's folds, undone before and applied again after.
            deliberate: Whether an `eject` node or a keep-together rule
                asked for it, rather than a band that did not fit.
            room: Whether it is looking for room, which every eject is
                except an `eject` node's without a ``require``.

        """
        if fold is not None:
            self.variables.restore(fold.before)
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
        if fold is not None:
            fold.before = self.variables.snapshot()
            fold.apply()

    def column_eject(self, ejecting: Frame) -> None:
        """Advance a frame to its next column.

        doc/layout.md#sequence: the footers of the frame and every frame
        inside it, innermost first; the column's scopes end; the frame
        moves one column across and every frame inside it starts again
        in its first; then the headers, outermost first.

        Args:
            ejecting: The frame that has a column left.

        """
        participants = [ejecting, *ejecting.descendants()]
        self.footers(participants)
        self.context.column_count = 0
        self.variables.clear("column")
        self.variables.iterate("column")
        ejecting.column += 1
        for frame in ejecting.descendants():
            frame.column = 0
            frame.floor = ejecting.floor
        self.set_column_number()
        self.open_columns(participants)

    def page_eject(self) -> None:
        """End the page and start the next.

        doc/layout.md#sequence: a page eject ends a column as well,
        so the column's scopes end first, then the page's.  A group
        that is still opening does not count the page it is leaving:
        its run begins on the page its first band lands on.

        """
        self.settle_columns()
        self.footers(self.frames())
        self.context.column_count = 0
        self.variables.clear("column")
        self.variables.iterate("column")
        self.pages.append(Page(self.context.page_number, tuple(self.marks)))
        self.context.page_number += 1
        self.context.page_count = 0
        self.variables.clear("page")
        self.variables.iterate("page")
        for run in self.runs:
            if not run.opening:
                self.context.group_pages[self.name(run)] += 1
        self.new_page()
        self.open_frames()

    def new_page(self) -> None:
        """Start an empty page, every frame back at its first column."""
        self.marks = []
        self.fragments = {}
        paper = self.layout.paper
        page = self.page
        page.x = paper.left
        page.width = round_points(paper.width - paper.left - paper.right)
        page.outer_top = paper.top
        page.outer_bottom = round_points(paper.height - paper.bottom)
        for frame in self.frames():
            frame.column = 0
            frame.floor = 0.0
            frame.lead = 0.0
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

        Args:
            frames: A frame and every frame inside it, outermost first.

        """
        drawn = [self.open_column(frame) for frame in frames]
        for position, frame in enumerate(frames):
            frame.start = max([frame.top, *drawn[position + 1 :]])

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
        if frame.parent is not None:
            frame.place_across()
            frame.outer_top = frame.parent.top
            frame.outer_bottom = frame.parent.bottom
        whole = Window(frame.x, frame.width, frame.outer_top, frame.outer_bottom)
        walk = self.walk_of(frame)
        header = self.reserve(frame.header, whole, walk)
        footer = self.reserve(frame.footer, whole, walk)
        if round_points(header + footer) > whole.height:
            raise BuildError(
                "the header and footer reservations together exceed the frame",
                Location(file=self.file, path=frame.path),
            )
        frame.settle(header, footer)
        if frame.header is None:
            return frame.outer_top
        above = Window(frame.x, frame.width, frame.outer_top, frame.bottom)
        measured = self.measurer.band(
            frame.header, above, self.context, (frame.header.styles, *walk)
        )
        if measured is None:
            return frame.outer_top
        self.marks.extend(
            mark.moved(frame.x, frame.outer_top) for mark in measured.marks
        )
        drawn = round_points(frame.outer_top + measured.height)
        frame.reach(drawn)
        return drawn

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

    def footers(self, frames: list[Frame]) -> None:
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

        Args:
            frames: The participating frames, outermost first.

        """
        with self.before_the_break():
            self.build_footers(frames)

    def build_footers(self, frames: list[Frame]) -> None:
        """Build the footers of these frames and place them, innermost first.

        Args:
            frames: The participating frames, outermost first.

        """
        for frame in reversed(frames):
            section = frame.footer
            if section is None:
                continue
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
                continue
            down = round_points(frame.outer_bottom - measured.height)
            self.marks.extend(mark.moved(frame.x, down) for mark in measured.marks)
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
        """Set ``COLUMN_NUMBER`` from the innermost frame with columns."""
        number = 1
        for frame in self.frames():
            if frame.count > 1:
                number = frame.column + 1
        self.context.column_number = number

    # -- balancing --------------------------------------------------------

    def record(self, frame: Frame, start: int, down: float, height: float) -> None:
        """Note a committed band against every balanced frame it concerns.

        doc/layout.md#balanced-columns balances what a frame was given since
        the page opened.  A band in a frame inside a balanced one belongs to
        the innermost balanced frame above it; one placed in a frame outside
        it, once the fragment has begun, leaves the fragment where it is.

        Args:
            frame: The frame the band went in.
            start: The index of its first mark on the page.
            down: The Y it was placed at.
            height: Its height.

        """
        for balanced in self.frames():
            if not (balanced.balance and balanced.count > 1):
                continue
            fragment = self.fragments.setdefault(balanced, Fragment(balanced))
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
        where a band goes that packing by height would not reproduce.

        Args:
            frame: The frame it happened in.

        """
        for balanced in (frame, *frame.ancestors()):
            fragment = self.fragments.get(balanced)
            if fragment is not None:
                fragment.alone = True
            elif balanced.balance and balanced.count > 1:
                self.fragments[balanced] = Fragment(balanced, alone=True)

    def settle_columns(self) -> None:
        """Balance every balanced frame's fragment on the page as it ends.

        A fragment is balanced once.  The report's summary can still
        eject after the pass at the end of the report, and the page
        it leaves is not balanced a second time: the fragment's record of
        where its bands went is the fill's, and they are no longer there.

        """
        for frame in reversed(self.frames()):
            fragment = self.fragments.get(frame)
            if fragment is None or fragment.alone or not fragment.bands:
                continue
            opened = not any(
                one.header is not None or one.footer is not None
                for one in (frame, *frame.descendants())
            )
            balance(fragment, self.marks, opened)
            fragment.alone = True

    # -- the document -----------------------------------------------------

    def text_blobs(self) -> dict[str, str]:
        """Return the `data` nodes whose content a `field` can hold.

        A blob reaches a field as text, so one whose bytes are not text
        is left out and a field naming it is the error that says so.

        """
        found: dict[str, str] = {}
        for name, content in self.build.blobs.items():
            try:
                found[name] = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
        return found

    def paper(self) -> Paper:
        """Return the document's page geometry."""
        page = self.layout.paper
        return Paper(
            page.width, page.height, page.left, page.right, page.top, page.bottom
        )

    def printout(self) -> Printout:
        """Return the finished document.

        The font table holds the fonts that were used rather than every
        one declared, per doc/printout.md#fonts: a font some measured
        element's style walk resolved to.  Every declared font was still
        resolved at load, so an unusable one is refused all the same.
        What resolving a font had to say is carried only for a font the
        table lists, so no warning names a font the document does not have.

        """
        used = tuple(
            one for one in self.build.fonts if one.font.name in self.measurer.used
        )
        report = self.build.report
        # A group is listed once it has opened, and not before: over no
        # records at all the header carries neither table.
        names = sorted(self.name(run) for run in self.runs if run.runs)
        by_name = {self.name(run): run for run in self.runs}
        return Printout(
            report=ReportMeta(
                report.name, report.description, report.version, report.author
            ),
            built=self.build.built.format(RFC3339),
            engine=meta.engine(),
            strict_fonts=self.build.strict_fonts,
            paper=self.paper(),
            fonts=tuple(font_entry(one) for one in used),
            data={},
            pages=tuple(self.pages),
            warnings=(
                tuple(self.build.warnings)
                + tuple(warning for one in used for warning in one.warnings)
                + tuple(self.measurer.warnings)
                + tuple(self.warnings)
            ),
            group_runs={name: by_name[name].runs for name in names},
            group_keys={name: len(by_name[name].keys) for name in names},
        )


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


def font_entry(resolution: Resolution) -> FontEntry:
    """Return one resolved font as the printout's header records it.

    Args:
        resolution: What the resolution chain produced.

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
        data=origin.data,
        index=origin.index,
    )
