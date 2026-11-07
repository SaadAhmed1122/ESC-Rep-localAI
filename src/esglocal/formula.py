"""Safe arithmetic formula evaluation.

KPI formulas are parsed with :mod:`ast` and only a tiny whitelist of node types
is accepted: numbers, input names, + - * /, unary minus and the functions
``min``, ``max`` and ``abs``. Attribute access, subscripts, comprehensions,
lambdas, imports and arbitrary calls are rejected *at parse time*, so a
malicious catalog or user-supplied formula can never execute code.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"min": min, "max": max, "abs": abs}
_MAX_LEN = 500


class FormulaError(ValueError):
    """Formula is invalid or cannot be evaluated with the given values."""


class Formula:
    def __init__(self, expression: str, allowed_names: set[str] | None = None):
        if not expression or len(expression) > _MAX_LEN:
            raise FormulaError("Formula is empty or too long")
        self.expression = expression
        try:
            self._tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"Invalid formula syntax: {expression!r}") from exc
        self.names = self._check(self._tree.body)
        if allowed_names is not None:
            unknown = self.names - allowed_names
            if unknown:
                raise FormulaError(f"Formula uses undeclared inputs: {sorted(unknown)}")

    def _check(self, node: ast.AST) -> set[str]:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError("Only numeric constants are allowed")
            try:
                ok = math.isfinite(float(node.value))
            except OverflowError:
                ok = False
            if not ok:
                raise FormulaError("Numeric constant is too large")
            return set()
        if isinstance(node, ast.Name):
            if node.id in _FUNCS:
                raise FormulaError(f"{node.id} must be called")
            return {node.id}
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return self._check(node.left) | self._check(node.right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return self._check(node.operand)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise FormulaError("Only min(), max() and abs() calls are allowed")
            if node.keywords or not node.args:
                raise FormulaError("Function calls take positional arguments only")
            names: set[str] = set()
            for arg in node.args:
                names |= self._check(arg)
            return names
        raise FormulaError(f"Disallowed expression element: {type(node).__name__}")

    def evaluate(self, values: Mapping[str, float]) -> float:
        missing = self.names - set(values)
        if missing:
            raise FormulaError(f"Missing values for: {sorted(missing)}")
        try:
            result = self._eval(self._tree.body, values)
        except (OverflowError, RecursionError, ArithmeticError) as exc:
            raise FormulaError(f"Numeric error: {type(exc).__name__}") from exc
        if not math.isfinite(result):
            raise FormulaError("Result is not a finite number")
        return float(result)

    def _eval(self, node: ast.AST, values: Mapping[str, float]) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            v = values[node.id]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                raise FormulaError(f"Value for {node.id} is missing")
            return float(v)
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, values)
            right = self._eval(node.right, values)
            if isinstance(node.op, ast.Div) and right == 0:
                raise FormulaError("Division by zero")
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPS[type(node.op)](self._eval(node.operand, values))
        if isinstance(node, ast.Call):
            args = [self._eval(a, values) for a in node.args]
            return float(_FUNCS[node.func.id](*args))
        raise FormulaError("Unsupported expression")  # pragma: no cover - guarded by _check

    def __repr__(self) -> str:
        return f"Formula({self.expression!r})"
