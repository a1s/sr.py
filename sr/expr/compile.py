"""Starlark on Python's parser: from source text to a callable.

doc/expressions.md#compilation says what to build -- "a function whose
parameters are the names it references" -- and this is the four passes
that build it.

1. **Parse** with ``ast.parse(source, mode="eval")``.  Python's
   expression grammar is a superset of Starlark's, so precedence,
   associativity and comprehension scoping are correct for free.
2. **Reject** every node kind the dialect does not have.  This is what
   makes the superset a subset again: no ``**``, no lambda, no f-string,
   no walrus, no chained comparison, no starred argument, no ``is``.
3. **Rewrite** ``a.b`` into ``getattr_(a, "b")``, ``a[i]`` into
   ``getitem_(a, i)``, ``a[i:j]`` into ``getslice_(...)``, and ``a % b``
   into ``mod_(a, b)``.  Attribute and index resolution then run through
   the engine's own tables rather than through Python's, which is what
   keeps ``__class__`` out of the language and what makes ``%`` on a
   string Starlark's formatter instead of Python's.
4. **Compile** a function of the free names and hand back the callable.

What runs afterwards is CPython bytecode with no interpreter loop above
it, which is the whole reason this is written rather than an existing
Starlark being embedded: a tree-walking evaluator costs about 15 µs per
expression and this costs about 0.05 µs.

The sandbox is the rejection pass plus the empty ``__builtins__`` the
function is given.  Nothing reaches a dunder, because no expression
that mentions one compiles: an attribute is a call into a whitelist,
and a name that is not a global and not a parameter is a compile error
rather than a lookup that might find something.

"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from sr.errors import ExpressionError
from sr.expr.builtins import GLOBALS, getattr_, getitem_, getslice_, mod_

__all__ = ["Expression", "compile_expression", "evaluate"]

# The name the compiled function is given, and the prefix its helpers
# share.  An expression may not use a name beginning with this, which is
# what keeps a template from reaching the machinery it is compiled into.
RESERVED: Final = "_sr_"
FUNCTION_NAME: Final = "_sr_expr"

# What the compiled code runs against: the language's names, the four
# resolvers, and an empty `__builtins__` where Python's would be.  One
# dict shared by every expression, because no expression can write to it.
RUNTIME: Final[dict[str, Any]] = {
    "__builtins__": {},
    **GLOBALS,
    "_sr_getattr": getattr_,
    "_sr_getitem": getitem_,
    "_sr_getslice": getslice_,
    "_sr_mod": mod_,
}

# Node kinds the dialect does not have, and what to say about each.
# The message names the Starlark spelling wherever there is one, because
# a template author reading it wants the alternative rather than the rule.
REFUSED: Final[dict[type[ast.AST], str]] = {
    ast.Lambda: "there are no lambdas; the language has no function definitions",
    ast.NamedExpr: "there is no := operator",
    ast.JoinedStr: "there are no f-strings; use the format() builtin",
    ast.FormattedValue: "there are no f-strings; use the format() builtin",
    ast.Starred: "an argument may not be starred",
    ast.GeneratorExp: "there are no generator expressions; write a list comprehension",
    ast.SetComp: "there are no set comprehensions; write set([...])",
    ast.Set: "there is no set literal; write set([...])",
    ast.Await: "there is nothing to await",
    ast.Yield: "there is no yield",
    ast.YieldFrom: "there is no yield",
    ast.Slice: "a slice is only valid inside a subscript",
}

# Operators the dialect does not have.  `**` is the interesting one: it
# is free here, because Python's parser already takes it, and expensive
# in the reference, where it would mean forking the Starlark parser --
# which is exactly the asymmetry to refuse.
REFUSED_OPERATORS: Final[dict[type[ast.AST], str]] = {
    ast.Pow: "there is no ** operator; write math.pow(x, y)",
    ast.MatMult: "there is no @ operator",
    ast.Is: "there is no `is`; write ==",
    ast.IsNot: "there is no `is not`; write !=",
}


def offset_of(node: ast.AST, source: str) -> int | None:
    """Return where a node begins, as an offset into the whole expression.

    An expression is usually one line, but a KDL property may hold
    a newline, so the line is folded in rather than assumed away.

    Args:
        node: The node to locate.
        source: The expression it came from.

    """
    line = getattr(node, "lineno", None)
    column = getattr(node, "col_offset", None)
    if line is None or column is None:
        return None
    lines = source.splitlines(keepends=True)
    return int(sum(len(one) for one in lines[: line - 1]) + column)


class Rejector(ast.NodeVisitor):
    """The pass that turns Python's grammar back into Starlark's.

    Attributes:
        source: The expression, for locating a diagnostic in it.

    """

    def __init__(self, source: str) -> None:
        """Prepare to check one expression.

        Args:
            source: The expression's text.

        """
        self.source = source

    def refuse(self, node: ast.AST, message: str) -> ExpressionError:
        """Return the error for a construct the dialect does not have.

        Args:
            node: Where the construct is.
            message: What is wrong with it.

        """
        return ExpressionError(message, offset=offset_of(node, self.source))

    def generic_visit(self, node: ast.AST) -> None:
        """Check one node against the refusals, then its children."""
        message = REFUSED.get(type(node))
        if message is not None:
            raise self.refuse(node, message)
        super().generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Allow a slice where it belongs, which is inside a subscript."""
        self.visit(node.value)
        if isinstance(node.slice, ast.Slice):
            for part in (node.slice.lower, node.slice.upper, node.slice.step):
                if part is not None:
                    self.visit(part)
        else:
            self.visit(node.slice)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        """Check a binary operator, and then its operands."""
        message = REFUSED_OPERATORS.get(type(node.op))
        if message is not None:
            raise self.refuse(node, message)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        """Refuse a chained comparison, and the two identity operators."""
        if len(node.ops) > 1:
            raise self.refuse(
                node,
                "there are no chained comparisons; write a < b and b < c",
            )
        message = REFUSED_OPERATORS.get(type(node.ops[0]))
        if message is not None:
            raise self.refuse(node, message)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Refuse a call that spreads a dict over its keywords."""
        for keyword in node.keywords:
            if keyword.arg is None:
                raise self.refuse(node, "an argument may not be starred")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """Refuse the literal kinds the language has no values for."""
        if isinstance(node.value, complex):
            raise self.refuse(node, "there are no complex numbers")
        if node.value is Ellipsis:
            raise self.refuse(node, "there is no ...")

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Refuse an asynchronous comprehension, and check the rest."""
        if node.is_async:
            raise self.refuse(node.iter, "there is nothing to await")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Refuse a name reserved for the compiler's own machinery."""
        if node.id.startswith(RESERVED):
            raise self.refuse(
                node, f"a name may not begin with {RESERVED!r}: {node.id}"
            )


def bound_by(target: ast.expr) -> set[str]:
    """Return the names a comprehension target binds.

    Args:
        target: The target, which may be a name or a tuple of them.

    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return (
            set().union(*(bound_by(item) for item in target.elts))
            if target.elts
            else set()
        )
    return set()


class Names(ast.NodeVisitor):
    """The pass that finds the names an expression reads from outside.

    Comprehension variables are local to their comprehension, as they
    are in Starlark, so ``[x for x in xs]`` reads ``xs`` and not ``x``.

    Attributes:
        bound: The names a comprehension has bound around the current node.
        found: The free names, in the order they first appear.

    """

    def __init__(self) -> None:
        """Start with nothing bound and nothing found."""
        self.bound: set[str] = set()
        self.found: list[str] = []

    def visit_Name(self, node: ast.Name) -> None:
        """Record a name that is neither bound nor already found."""
        if node.id not in self.bound and node.id not in self.found:
            self.found.append(node.id)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Visit a list comprehension with its variables bound."""
        self.comprehend(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Visit a dict comprehension with its variables bound."""
        self.comprehend(node.generators, node.key, node.value)

    def comprehend(
        self, generators: list[ast.comprehension], *bodies: ast.expr
    ) -> None:
        """Visit a comprehension, binding its targets for the duration.

        Args:
            generators: The ``for`` clauses.
            *bodies: The expressions evaluated once per iteration.

        """
        was = set(self.bound)
        for generator in generators:
            self.visit(generator.iter)
            self.bound |= bound_by(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for body in bodies:
            self.visit(body)
        self.bound = was


class Rewriter(ast.NodeTransformer):
    """The pass that routes attribute, index and ``%`` through the engine.

    Every rewrite is a call to one of four helpers, so what the compiled
    bytecode does with ``customer.last_name`` is call a function that
    consults a whitelist -- and what it does not do is anything Python
    would have done with a dot.

    """

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        """Rewrite ``a.b`` into a call to the attribute resolver."""
        self.generic_visit(node)
        return call("_sr_getattr", node, node.value, ast.Constant(node.attr))

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        """Rewrite ``a[i]`` and ``a[i:j]`` into calls to their resolvers."""
        self.generic_visit(node)
        if isinstance(node.slice, ast.Slice):
            bounds = [
                part if part is not None else ast.Constant(None)
                for part in (node.slice.lower, node.slice.upper, node.slice.step)
            ]
            return call("_sr_getslice", node, node.value, *bounds)
        return call("_sr_getitem", node, node.value, node.slice)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Rewrite ``a % b``, which on a string is not Python's operator."""
        self.generic_visit(node)
        if isinstance(node.op, ast.Mod):
            return call("_sr_mod", node, node.left, node.right)
        return node


def call(helper: str, at: ast.AST, *args: ast.expr) -> ast.Call:
    """Return a call to one of the runtime helpers, placed at a node.

    Args:
        helper: The helper's name in the runtime namespace.
        at: The node being replaced, whose position the call takes.
        *args: The call's arguments.

    """
    made = ast.Call(
        func=ast.Name(id=helper, ctx=ast.Load()), args=list(args), keywords=[]
    )
    return ast.copy_location(made, at)


@dataclass(frozen=True)
class Expression:
    """One compiled expression, and the names it needs to be evaluated.

    Attributes:
        source: The expression as the template wrote it.
        names: The free names, which are the function's parameters,
            in the order it takes them.
        function: The compiled callable.

    """

    source: str
    names: tuple[str, ...]
    function: Callable[..., Any]

    @property
    def uses_final(self) -> bool:
        """Report whether the expression reads an end-of-scope value.

        doc/expressions.md makes ``FINAL`` and ``evaltime`` require each
        other, and this is the half of that check the compiler can answer.

        """
        return "FINAL" in self.names

    def __call__(self, *values: Any) -> Any:
        """Evaluate with the values positionally, in :attr:`names` order.

        Args:
            *values: One value per name, which a caller that has already
                arranged them avoids re-arranging.

        """
        try:
            return self.function(*values)
        except ExpressionError:
            raise
        except Exception as error:
            raise ExpressionError(f"{type(error).__name__}: {error}") from error

    def evaluate(self, environment: Mapping[str, Any]) -> Any:
        """Evaluate against an environment, taking only what is needed.

        Args:
            environment: Every name in scope, of which this reads its own.

        Raises:
            ExpressionError: A name has no value, or evaluation failed.

        """
        values = []
        for name in self.names:
            try:
                values.append(environment[name])
            except KeyError:
                raise ExpressionError(f"undefined: {name}") from None
        return self(*values)


def compile_expression(
    source: str, *, known: frozenset[str] | set[str] | None = None
) -> Expression:
    """Return an expression compiled to a function of its free names.

    Args:
        source: The expression, as the template wrote it.
        known: The names that may appear besides the globals --
            the predefined variables, the parameters, the variables
            and the declared fields.  ``None`` accepts any name and
            leaves the check to evaluation, which is what a test
            harness wants and a template load does not.

    Raises:
        ExpressionError: The expression does not parse, uses a construct
            the dialect lacks, or names something not in scope.

    """
    tree = parse(source)
    Rejector(source).visit(tree)
    finder = Names()
    finder.visit(tree)
    free = tuple(name for name in finder.found if name not in RUNTIME)
    if known is not None:
        unknown = [name for name in free if name not in known]
        if unknown:
            raise ExpressionError(f"undefined: {unknown[0]}")
    rewritten = Rewriter().visit(tree)
    return Expression(source, free, build(rewritten, free, source))


def parse(source: str) -> ast.Expression:
    """Return the syntax tree of an expression.

    Args:
        source: The expression's text.

    Raises:
        ExpressionError: The text does not parse.

    """
    try:
        return ast.parse(source, mode="eval")
    except SyntaxError as error:
        lines = source.splitlines(keepends=True)
        offset = None
        if error.lineno is not None and error.offset is not None:
            offset = sum(len(one) for one in lines[: error.lineno - 1]) + (
                error.offset - 1
            )
        raise ExpressionError(f"{error.msg}", offset=offset) from None
    except ValueError as error:
        raise ExpressionError(str(error)) from None


def build(
    tree: ast.Expression, names: tuple[str, ...], source: str
) -> Callable[..., Any]:
    """Return the callable a rewritten tree compiles to.

    The wrapper is produced by parsing a ``def`` and grafting
    the expression into it, rather than by assembling nodes here.
    The AST's own node fields have changed twice in recent Python
    versions and the text of a function definition has not.

    Args:
        tree: The rewritten expression.
        names: The free names, which become the parameters.
        source: The original text, for the diagnostic and the code name.

    """
    shell = ast.parse(f"def {FUNCTION_NAME}({', '.join(names)}):\n    return None")
    definition = shell.body[0]
    assert isinstance(definition, ast.FunctionDef)
    body = definition.body[0]
    assert isinstance(body, ast.Return)
    body.value = tree.body
    ast.fix_missing_locations(shell)
    code = compile(shell, filename=f"<expr {source!r}>", mode="exec")
    made: dict[str, Any] = {}
    exec(code, RUNTIME, made)
    function: Callable[..., Any] = made[FUNCTION_NAME]
    return function


def evaluate(source: str, environment: Mapping[str, Any] | None = None) -> Any:
    """Compile an expression and evaluate it once.

    For a caller with one expression and no reason to keep it -- a test,
    or a `defaultexpr` read at load.  Everything on the layout path
    compiles once and evaluates many times instead.

    Args:
        source: The expression, as the template wrote it.
        environment: The names in scope.

    """
    return compile_expression(source).evaluate(environment or {})
