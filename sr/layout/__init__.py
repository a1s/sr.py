"""Layout: a template and records become pages of marks.

Four modules, layered downward and importing in that order:

=============  ============================================================
``frame``      the regions bands fill, from the top down
``context``    the names an expression in a band can see
``measure``    one band, measured: marks at band-relative coordinates
``loop``       the record loop, and the report around it
=============  ============================================================

doc/layout.md#measure-decide-commit is the shape of it: measuring is
pure and mutates nothing, deciding compares a height against a frame,
and committing translates the marks onto a page.  Band splitting,
keep-together, measured header reservation and deferred evaluation
are all consequences of that separation, which is why the separation
is here before the features that need it.

This milestone is the walking skeleton: one frame that never ejects,
no groups, no columns, no subreports.  Everything left out raises
:class:`~sr.errors.Unsupported` naming the milestone that brings it.

"""

from __future__ import annotations

from sr.layout.context import Context
from sr.layout.frame import Frame
from sr.layout.loop import Build, Builder
from sr.layout.measure import Measurement, Measurer

__all__ = [
    "Build",
    "Builder",
    "Context",
    "Frame",
    "Measurement",
    "Measurer",
]
