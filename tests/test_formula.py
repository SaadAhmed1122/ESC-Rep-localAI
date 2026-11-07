import pytest

from esglocal.formula import Formula, FormulaError


def test_basic_arithmetic():
    f = Formula("(a + b) / c * 100", {"a", "b", "c"})
    assert f.evaluate({"a": 1, "b": 3, "c": 8}) == 50.0
    assert f.names == {"a", "b", "c"}


def test_functions_and_unary():
    f = Formula("max(a, b) - abs(-c) + min(1, 2)")
    assert f.evaluate({"a": 2, "b": 5, "c": 3}) == 3.0


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo pwned')",
        "a.__class__",
        "open('x')",
        "a[0]",
        "[x for x in a]",
        "lambda: 1",
        "a if b else c",
        "a ** 2",  # power disabled (DoS via huge exponents)
        "'text'",
        "True + 1",
        "min",
        "max(a, key=b)",
        "a == b",
    ],
)
def test_rejects_unsafe_or_unsupported(expr):
    with pytest.raises(FormulaError):
        Formula(expr)


def test_rejects_undeclared_names():
    with pytest.raises(FormulaError, match="undeclared"):
        Formula("a + secret", {"a"})


def test_division_by_zero_and_missing_values():
    f = Formula("a / b")
    with pytest.raises(FormulaError, match="Division by zero"):
        f.evaluate({"a": 1, "b": 0})
    with pytest.raises(FormulaError, match="Missing"):
        f.evaluate({"a": 1})
    with pytest.raises(FormulaError):
        f.evaluate({"a": float("nan"), "b": 1})


def test_too_long_expression():
    with pytest.raises(FormulaError):
        Formula("a+" * 400 + "a")
