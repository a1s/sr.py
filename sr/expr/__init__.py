"""The expression engine: the dialect of doc/expressions.md, compiled.

Six modules, layered downward and importing in that order:

===============  ==========================================================
``golayout``     Go reference-time layouts, for ``.format`` and for parsing
``values``       the value model: decimal, time, duration, record, set
``fmt``          the two percent formatters
``builtins``     the names in scope, the method tables, the resolvers
``compile``      Python's parser, restricted and rewritten into a callable
``calc``         the twelve accumulator modes a `variable` folds with
===============  ==========================================================

Nothing here knows about templates, bands or pages.  An expression is
compiled from text and evaluated against a mapping of names, and what
fills that mapping -- the predefined variables, the parameters, the
record fields -- is the caller's business.

"""

from __future__ import annotations

from sr.expr.builtins import GLOBALS, GROUP_SUFFIXES, PREDEFINED
from sr.expr.calc import Accumulator, make
from sr.expr.compile import Expression, compile_expression, evaluate
from sr.expr.fmt import apply_format, format_value, interpolate
from sr.expr.values import (
    Decimal,
    Duration,
    FrozenDict,
    FrozenList,
    Namespace,
    Record,
    Set,
    Time,
    truthy,
    type_name,
)

__all__ = [
    "GLOBALS",
    "GROUP_SUFFIXES",
    "PREDEFINED",
    "Accumulator",
    "Decimal",
    "Duration",
    "Expression",
    "FrozenDict",
    "FrozenList",
    "Namespace",
    "Record",
    "Set",
    "Time",
    "apply_format",
    "compile_expression",
    "evaluate",
    "format_value",
    "interpolate",
    "make",
    "truthy",
    "type_name",
]
