"""Layout: a template and records become pages of marks.

Twelve modules, layered downward and importing in that order:

=============  ============================================================
``frame``      the frame tree, and the regions bands fill from the top down
``context``    the names an expression in a band can see
``variables``  accumulators, and the scopes that fold and clear them
``defer``      deferred elements, what they read, and the register of them
``blobs``      the printout's `data` table, named as the build meets blobs
``fonttable``  the printout's font table, named as the build uses fonts
``images``     an image's bytes, sniffed and sized, and its table entry
``measure``    one band, measured: marks at band-relative coordinates
``place``      where a measured band may be cut, and the halves a cut makes
``columns``    balancing a page's bands over a frame's columns
``subreport``  what a host settles for a subreport before it runs
``loop``       the record loop, the eject sequence, the pages,
               and the nested builders a subreport runs in
=============  ============================================================

doc/layout.md#measure-decide-commit is the shape of it: measuring is
pure and mutates nothing, deciding compares a height against a frame,
and committing translates the marks onto a page.  Band splitting,
keep-together, measured header reservation and deferred evaluation
are all consequences of that separation.

What is here is the whole of pagination: frames and columns, the four
branches of placing a band, splitting, `eject` nodes and the eject
sequence, groups, keep-together, and balancing, and deferred evaluation
on top of it, every body element, and subreports.  Outline entries
inside a band are not, and raise :class:`~sr.errors.Unsupported`
naming the milestone that brings them.

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
