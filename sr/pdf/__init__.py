"""The PDF renderer: a printout in, a PDF file out.

doc/render.md is the contract.  A printout's boxes are absolute,
its strings wrapped, its barcodes encoded, and its fonts resolved,
so rendering is a translation of marks into drawing operators,
and nothing here lays anything out.

Five modules, layered downward and importing in that order:

=============  ============================================================
``objects``    the object table, streams, and the cross-reference table
``subset``     one Type0 font per face, carrying a subset of it
``images``     a baseline JPEG as it stands, everything else re-encoded
``draw``       each mark as the operators that paint it
``document``   the pages, the outline, the links, and the file
=============  ============================================================

Nothing here knows about templates, records or bands.
The printout is the seam, and it is the only thing a renderer sees.

"""

from __future__ import annotations

from sr.pdf.document import render

__all__ = ["render"]
