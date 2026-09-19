"""Fonts: finding a face, describing it, and measuring text with it.

Four modules, layered downward and importing in that order:

=============  ============================================================
``face``       one face: its names, its style bits, its advances
``hostenum``   every face the machine has, in one family and style table
``resolve``    the four-step chain of doc/template.md#font-resolution
``text``       measurement and the line breaking of doc/layout.md
=============  ============================================================

Nothing here knows about bands or pages.  A face is opened from a path or
from bytes, a `font` node is resolved to one, and a string is measured or
wrapped against it at a size.  What the wrapped lines are then used for --
a stretch field's height, a band's height, a page break -- is layout's.

The seam with the template model is deliberately thin: :mod:`resolve` takes
a :class:`sr.template.model.Font` and gives back a :class:`Resolution`, and
everything above it reads the resolution rather than the node.

"""

from __future__ import annotations

from sr.fonts.face import (
    Face,
    Origin,
    UnsupportedFont,
    faces_in,
    open_bytes,
    open_face,
    read_faces,
    sniff,
)
from sr.fonts.hostenum import Catalog, Entry, Source, enumerate_faces, host_sources
from sr.fonts.resolve import ALIASES, Resolution, Resolver, aliases_for
from sr.fonts.text import (
    LEADING,
    Metrics,
    Wrapped,
    missing_glyph,
    quote_char,
    wrap,
)

__all__ = [
    "ALIASES",
    "LEADING",
    "Catalog",
    "Entry",
    "Face",
    "Metrics",
    "Origin",
    "Resolution",
    "Resolver",
    "Source",
    "UnsupportedFont",
    "Wrapped",
    "aliases_for",
    "enumerate_faces",
    "faces_in",
    "host_sources",
    "missing_glyph",
    "open_bytes",
    "open_face",
    "quote_char",
    "read_faces",
    "sniff",
    "wrap",
]
