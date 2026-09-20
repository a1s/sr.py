"""The printout: the document a renderer consumes.

Three modules, layered downward and importing in that order:

=============  ============================================================
``model``      the header, the pages, and the marks on them
``write``      the NDJSON encoding, byte for byte
``inspect``    a printout read back and dumped as text
=============  ============================================================

doc/printout.md calls the in-memory structure the primary artifact:
the engine hands it to a renderer directly and serializes only when
asked.  So nothing here knows about templates, bands or records --
a printout has been through all of that already -- and nothing
above it needs to know how a length is spelled.

"""

from __future__ import annotations

from sr.printout.model import (
    VERSION,
    Box,
    FontEntry,
    Line,
    Mark,
    Page,
    Paper,
    Printout,
    Rectangle,
    Report,
    Text,
)
from sr.printout.write import dumps, number, quoted, write_jsonl

__all__ = [
    "VERSION",
    "Box",
    "FontEntry",
    "Line",
    "Mark",
    "Page",
    "Paper",
    "Printout",
    "Rectangle",
    "Report",
    "Text",
    "dumps",
    "number",
    "quoted",
    "write_jsonl",
]
