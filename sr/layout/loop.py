"""The record loop, and the report around it.

doc/layout.md#report-structure is the shape:

```
title
  [page header, first page]
  for each record: group titles / detail / group summaries
  [page footer, last page]
summary
```

and doc/layout.md#the-record-loop is what happens per record.
This is that loop with the parts a later milestone brings left out,
and left out **loudly**: a group, a column, a subreport or a band
that will not fit raises :class:`~sr.errors.Unsupported` naming
the milestone rather than being laid out approximately.

What is here, then, is one frame that never ejects.  The page frame
is the page box inset by the margins with the header and the footer
reserved out of it, the bands fill it from the top down, and a band
that does not fit is the error that pagination will turn into a page
break.

Three orders are settled here and each was read off the reference.

* **The marks of a page come out header, title, details, summary,
  footer.**  The footer is built last although it is drawn at the top
  of its reserved band, because doc/layout.md#what-a-header-or-a-footer-sees
  builds it against the outgoing context.
* **The counters count what has been printed**, so the first detail band
  is built with ``REPORT_COUNT`` at 0 and the second at 1.
  ``ITEM_NUMBER`` is the other way about: it is 1 while the first record
  is being formatted, and 0 in a title or a page header, which are
  built before any record.
* **Variables iterate before the band that reads them**, per
  doc/expressions.md#ordering-against-section-printing: report-scoped
  ones once, before the title, and detail-scoped ones per record,
  before the detail band.

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sr import meta
from sr.errors import (
    BuildError,
    BuildWarning,
    ExpressionError,
    Location,
    Unsupported,
)
from sr.expr import Accumulator, Record, Time, make
from sr.expr.golayout import RFC3339
from sr.fonts.resolve import Resolution
from sr.fonts.text import Metrics
from sr.layout.context import Context
from sr.layout.frame import Frame
from sr.layout.measure import Measurement, Measurer
from sr.printout.model import FontEntry, Mark, Page, Paper, Printout
from sr.printout.model import Report as ReportMeta
from sr.template.model import Layout, Report, Section, Style, Variable
from sr.units import round_points

__all__ = ["Build", "Builder"]

# The `iter` and `reset` scopes a single frame can honour.
# A `page` or a `column` scope needs a boundary to fire at, and
# a `group` scope needs a group; all three arrive with pagination.
SCOPES = ("report", "detail", "item")


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
        warnings: What loading and resolving had to say.

    """

    report: Report
    records: tuple[Record, ...]
    parameters: dict[str, Any]
    fonts: tuple[Resolution, ...]
    blobs: dict[str, bytes]
    built: Time
    strict_fonts: bool = False
    warnings: tuple[BuildWarning, ...] = ()


class Builder:
    """Applies one template to one sequence of records.

    Attributes:
        build: What the run was asked for.
        layout: The template's `layout` node.
        context: The names in scope, as the report stands.
        measurer: What turns a band into marks.

    """

    def __init__(self, build: Build) -> None:
        """Prepare a run, refusing what this engine cannot lay out yet.

        Args:
            build: What the run was asked for.

        Raises:
            Unsupported: The template needs a later milestone.
            BuildError: A font did not resolve,
                or a variable would not initialize.

        """
        self.build = build
        report = build.report
        if report.layout is None:
            raise BuildError("the template has no layout", Location(file=report.file))
        self.layout: Layout = report.layout
        self.file = report.file
        self.refuse_unsupported()
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
        self.accumulators: dict[str, Accumulator] = {}
        self.pages: list[Page] = []
        self.marks: list[Mark] = []

    # -- what this milestone does not do ------------------------------

    def refuse_unsupported(self) -> None:
        """Refuse a template that needs work a later milestone brings.

        Raises:
            Unsupported: It uses one of them.

        """
        where = Location(file=self.file, path=self.layout.path)
        if self.layout.columns is not None:
            raise Unsupported("a columns node arrives in M8", where)
        if self.layout.group is not None:
            raise Unsupported("a group arrives in M8", where)
        for variable in self.build.report.variables:
            self.refuse_scope(variable)

    def refuse_scope(self, variable: Variable) -> None:
        """Refuse a variable whose scope needs a boundary to fire at.

        Args:
            variable: The node.

        Raises:
            Unsupported: Its `iter` or `reset` is one of those scopes.

        """
        for scope, prop in ((variable.iterate, "iter"), (variable.reset, "reset")):
            if scope not in SCOPES:
                raise Unsupported(
                    f"a {scope!r} {prop} scope needs pagination, which arrives in M8",
                    Location(file=self.file, path=variable.path, prop=prop),
                )

    # -- the run ------------------------------------------------------

    def run(self) -> Printout:
        """Build the document, and return it.

        Raises:
            BuildError: A band would not fit, or an expression failed.

        """
        paper = self.paper()
        whole, frame = self.frames(paper)
        self.iterate("report")
        self.band_at(self.layout.header, whole, frame.top, whole.top)
        self.place(self.layout.title, frame)
        for index, record in enumerate(self.build.records):
            self.row(index, record, frame)
        self.place(self.layout.summary, frame)
        self.band_at(self.layout.footer, whole, whole.bottom, frame.bottom)
        self.pages.append(Page(self.context.page_number, tuple(self.marks)))
        return self.printout(paper)

    def row(self, index: int, record: Record, frame: Frame) -> None:
        """Format one record.

        Args:
            index: Its 0-based position in the data.
            record: The record.
            frame: The frame its detail band goes in.

        """
        self.context.record = record
        self.context.item_number = index + 1
        self.iterate("item")
        detail = self.layout.detail
        if detail is None:
            return
        # The fold happens only for a band that prints, so whether
        # it prints is settled before the fold rather than after it --
        # and the answer is carried into the measurement, because
        # a `printwhen` that reads one of those variables would otherwise
        # be asked the same question twice and give two answers.
        printing = self.measurer.prints(detail, frame, self.context)
        if printing:
            self.iterate("detail")
        if self.place(detail, frame, printing=printing):
            self.context.report_count += 1
            self.context.page_count += 1
            self.context.column_count += 1

    def place(
        self, section: Section | None, frame: Frame, printing: bool | None = None
    ) -> bool:
        """Measure a band and commit it, reporting whether it printed.

        Args:
            section: The band, where the template has one.
            frame: The frame it goes in.
            printing: What its ``printwhen`` already answered,
                where the caller had to ask before measuring.

        Raises:
            BuildError: The band does not fit what is left of the frame.

        """
        if section is None:
            return False
        measured = self.measure(section, frame, printing)
        if measured is None:
            return False
        if not frame.accepts(measured.height):
            raise self.overflow(section, measured, frame)
        self.commit(measured, frame.x, frame.fill)
        frame.advance(measured.height)
        return True

    def measure(
        self, section: Section, frame: Frame, printing: bool | None = None
    ) -> Measurement | None:
        """Measure one band against a frame.

        Args:
            section: The band.
            frame: The frame it is being tried against.
            printing: What its ``printwhen`` already answered.

        """
        return self.measurer.band(
            section, frame, self.context, self.styles(section), printing
        )

    def commit(self, measured: Measurement, across: float, down: float) -> None:
        """Translate a measured band's marks onto the page.

        Args:
            measured: What measuring the band produced.
            across: The X of the frame's current column.
            down: The Y the band starts at.

        """
        self.marks.extend(mark.moved(across, down) for mark in measured.marks)

    def band_at(
        self, section: Section | None, whole: Frame, edge: float, start: float
    ) -> None:
        """Build a reserved band and place it in the space held for it.

        The header and the footer are the two: neither competes for the
        frame's fill position, because the frame was inset by the height
        each of them measured.  A footer is placed flush against the
        frame's reserved bottom, which is where the content stopped.

        Args:
            section: The band, where the template has one.
            whole: The page frame before either was reserved.
            edge: The far edge of the band's own strip.
            start: The Y the band is drawn from.

        """
        if section is None:
            return
        strip = Frame(whole.x, whole.width, start, max(start, edge))
        measured = self.measure(section, strip)
        if measured is not None:
            self.commit(measured, whole.x, start)

    # -- geometry -----------------------------------------------------

    def paper(self) -> Paper:
        """Return the document's page geometry."""
        page = self.layout.paper
        return Paper(
            page.width, page.height, page.left, page.right, page.top, page.bottom
        )

    def frames(self, paper: Paper) -> tuple[Frame, Frame]:
        """Return the page frame, with header and footer reserved.

        doc/layout.md#headerfooter-reservation measures both against
        the context as it stands when the frame begins, which is
        before any record has been read.

        Args:
            paper: The page geometry.

        Raises:
            BuildError: The two reservations together exceed the frame.

        """
        top = paper.top
        bottom = round_points(paper.height - paper.bottom)
        whole = Frame(
            paper.left,
            round_points(paper.width - paper.left - paper.right),
            top,
            bottom,
        )
        header = self.reserve(self.layout.header, whole)
        footer = self.reserve(self.layout.footer, whole)
        if round_points(header + footer) > whole.height:
            raise BuildError(
                "the header and footer reservations together exceed the frame",
                Location(file=self.file, path=self.layout.path),
            )
        return whole, Frame(
            whole.x,
            whole.width,
            round_points(top + header),
            round_points(bottom - footer),
        )

    def reserve(self, section: Section | None, frame: Frame) -> float:
        """Return the height a band reserves at the edge of a frame.

        Args:
            section: The header or the footer, where there is one.
            frame: The frame before either has been taken out of it.

        """
        if section is None:
            return 0.0
        measured = self.measure(section, frame)
        return 0.0 if measured is None else measured.height

    def styles(self, section: Section) -> tuple[tuple[Style, ...], ...]:
        """Return the `style` walk for a band, innermost scope first.

        doc/layout.md#building-a-band walks the band's own nodes, then
        its `columns`, then each enclosing `group`, then `layout`.  With
        neither columns nor groups yet, that is the band and the layout.

        Args:
            section: The band.

        """
        return (section.styles, self.layout.styles)

    def overflow(
        self, section: Section, measured: Measurement, frame: Frame
    ) -> BuildError:
        """Return the error a band that does not fit raises.

        With one frame and no ejects, a band that does not fit
        has nowhere to go.  Which of the two it is -- taller than the
        space left, or taller than an empty frame -- is the difference
        between a page break and an overflow, so the message says which.

        Args:
            section: The band.
            measured: What measuring it produced.
            frame: The frame it was tried against.

        """
        where = Location(
            file=self.file,
            path=section.path,
            record=self.context.record_index,
        )
        if measured.height > frame.height:
            return BuildError(
                f"the band is {measured.height:g} pt tall"
                f" and an empty frame is {frame.height:g} pt",
                where,
            )
        return Unsupported(
            f"the band is {measured.height:g} pt tall and "
            f"{frame.available:g} pt is left, so it needs a page eject, "
            "which arrives in M8",
            where,
        )

    # -- variables ----------------------------------------------------

    def iterate(self, scope: str) -> None:
        """Fold every variable whose `iter` names this scope.

        ``init`` seeds an accumulator at each reset and is folded in
        as its first value, which for a report-scoped variable is once.

        Args:
            scope: The scope whose boundary has just been crossed.

        Raises:
            BuildError: A variable's expression would not evaluate.

        """
        names = self.context.environment(0.0, 0.0)
        for variable in self.build.report.variables:
            if variable.name not in self.accumulators:
                self.accumulators[variable.name] = self.seeded(variable, names)
                self.publish()
                names = self.context.environment(0.0, 0.0)
            if variable.iterate != scope:
                continue
            accumulator = self.accumulators[variable.name]
            accumulator.fold(self.value(variable, "expr", names))
            self.publish()
            names = self.context.environment(0.0, 0.0)

    def seeded(self, variable: Variable, names: dict[str, Any]) -> Accumulator:
        """Return a variable's accumulator, seeded by its ``init``.

        Args:
            variable: The node.
            names: The environment its ``init`` is evaluated in.

        """
        accumulator = make(variable.calc)
        if variable.init is not None:
            accumulator.fold(self.value(variable, "init", names))
        return accumulator

    def value(self, variable: Variable, prop: str, names: dict[str, Any]) -> Any:
        """Return what one of a variable's expressions evaluates to.

        Args:
            variable: The node.
            prop: ``expr`` or ``init``.
            names: The environment to evaluate in.

        Raises:
            BuildError: The expression would not evaluate.

        """
        expression = variable.expr if prop == "expr" else variable.init
        assert expression is not None
        try:
            return expression.evaluate(names)
        except ExpressionError as failed:
            raise BuildError(
                f"variable {variable.name!r}: {failed}",
                Location(
                    file=self.file,
                    path=variable.path,
                    prop=prop,
                    record=self.context.record_index,
                ),
            ) from None

    def publish(self) -> None:
        """Copy the accumulators' values into the names in scope."""
        self.context.variables = {
            name: accumulator.value for name, accumulator in self.accumulators.items()
        }

    # -- the document -------------------------------------------------

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

    def printout(self, paper: Paper) -> Printout:
        """Return the finished document.

        Args:
            paper: The page geometry.

        """
        report = self.build.report
        return Printout(
            report=ReportMeta(
                report.name, report.description, report.version, report.author
            ),
            built=self.build.built.format(RFC3339),
            engine=meta.engine(),
            strict_fonts=self.build.strict_fonts,
            paper=paper,
            fonts=tuple(font_entry(one) for one in self.build.fonts),
            data={},
            pages=tuple(self.pages),
            warnings=tuple(self.build.warnings) + tuple(self.measurer.warnings),
        )


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
