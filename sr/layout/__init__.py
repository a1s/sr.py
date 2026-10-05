"""Layout: a template and records become pages of marks.

Eight modules, layered downward and importing in that order:

=============  ============================================================
``frame``      the frame tree, and the regions bands fill from the top down
``context``    the names an expression in a band can see
``variables``  accumulators, and the scopes that fold and clear them
``defer``      deferred elements, what they read, and the register of them
``measure``    one band, measured: marks at band-relative coordinates
``place``      where a measured band may be cut, and the halves a cut makes
``columns``    balancing a page's bands over a frame's columns
``loop``       the record loop, the eject sequence, and the pages
=============  ============================================================

doc/layout.md#measure-decide-commit is the shape of it: measuring is
pure and mutates nothing, deciding compares a height against a frame,
and committing translates the marks onto a page.  Band splitting,
keep-together, measured header reservation and deferred evaluation
are all consequences of that separation.

What is here is the whole of pagination: frames and columns, the four
branches of placing a band, splitting, `eject` nodes and the eject
sequence, groups, keep-together, and balancing, and deferred evaluation
on top of it, and every body element but `image`.  Subreports are not,
nor are images and outline entries inside a band, and each raises
:class:`~sr.errors.Unsupported` naming the milestone that brings it.

"""

from __future__ import annotations

from sr.layout.context import Context
from sr.layout.frame import Frame, Window
from sr.layout.loop import Build, Builder
from sr.layout.measure import Measurement, Measurer

__all__ = [
    "Build",
    "Builder",
    "Context",
    "Frame",
    "Measurement",
    "Measurer",
    "Window",
]
