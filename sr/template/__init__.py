"""The template: its model, how it is read, and what makes it valid.

Three modules, layered downward and importing in that order:

==============  ===========================================================
``model``       one dataclass per node, and the value types they hold
``load``        a KDL document becomes a model
``validate``    the rules of doc/template.md#validation over a built model
==============  ===========================================================

``load`` and ``validate`` are one cycle -- the loader calls validation
at the end of a load, and validation reads the loader's tree walkers --
and the import that closes it sits inside a function in ``load``.

Nothing here knows about fonts, measurement or pages.  A template is
a document with a shape and a set of rules, and what a band is worth
in points is layout's question.

"""

from __future__ import annotations

from sr.template.load import Loaded, Options, load, load_text, read
from sr.template.model import Report
from sr.template.validate import validate

__all__ = [
    "Loaded",
    "Options",
    "Report",
    "load",
    "load_text",
    "read",
    "validate",
]
