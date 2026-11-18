"""New constant support must not certify dynamic Java operands as exact.

These examples are valid Java methods. Their declarations make the missing
semantics explicit: char has no negative values, double admits NaN, and int
arithmetic can overflow. The legacy translator does not model those facts.
"""

from __future__ import annotations

import pytest
import z3

from patch_label.analyzer import _java_expression, _java_parser


def _return_expression(parameters: str, expression: str):
    source = (
        "class GuardProbe { boolean f(" + parameters + ") { return "
        + expression + "; } }"
    ).encode("utf-8")
    parser = _java_parser()
    assert parser is not None
    tree = parser.parse(source)
    assert not tree.root_node.has_error
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "return_statement":
            return node.named_children[0], source
        stack.extend(node.named_children)
    raise AssertionError("Missing return expression in test fixture")


def _translate(parameters: str, expression: str, *, prior=()):
    node, source = _return_expression(parameters, expression)
    unsupported = set(prior)
    variables = {}
    formula = _java_expression(
        node, source, {}, variables, 0,
        expected_sort="bool", unsupported=unsupported,
    )
    assert z3.is_bool(formula)
    return formula, variables, unsupported


@pytest.mark.parametrize(
    "parameters, expression",
    [
        ("char c", r"c < '\0'"),
        ("double d", "d != 'A' && !(d < 'A') && !(d > 'A')"),
        ("int x", "'A' == x + 2147483647"),
        ("int x", "x < (char) 65"),
        ("int x", "(byte) 1 == x"),
        ("long x", "x > (long) 0xffffffff"),
        ("char c", r"('A' == 65) && (c < '\0')"),
        ("char c", r"(c < '\0') && ('A' == 65)"),
        ("char c", r"true || c < '\0'"),
    ],
)
def test_dynamic_operands_of_new_integral_comparisons_remain_unsupported(
    parameters, expression,
):
    _, _, unsupported = _translate(parameters, expression)
    assert any(
        reason.startswith("integral_comparison_operand_not_modeled:")
        for reason in unsupported
    ), (parameters, expression, unsupported)


def test_char_range_counterexample_cannot_be_advertised_as_exact():
    # Java's char c can never satisfy c < '\0'. Until that range is modeled,
    # the comparison remains unknown, carrying its explicit limitation.
    formula, _, unsupported = _translate("char c", r"c < '\0'")
    solver = z3.Solver()
    solver.add(formula)
    assert solver.check() == z3.sat
    assert unsupported


def test_nan_case_cannot_be_advertised_as_exact():
    # With d = Double.NaN Java returns true. An unknown Boolean approximation
    # must allow that possibility, rather than wrongly discarding the path as
    # unsatisfiable under the legacy Int model. SAT does not prove a NaN model.
    formula, _, unsupported = _translate(
        "double d", "d != 'A' && !(d < 'A') && !(d > 'A')",
    )
    solver = z3.Solver()
    solver.add(formula)
    assert solver.check() == z3.sat
    assert unsupported


def test_dynamic_remainder_comparison_does_not_enter_legacy_integer_evaluator():
    formula, _, unsupported = _translate("int x", "'A' == (x % 2)")
    assert unsupported
    assert any(
        reason.startswith("integral_comparison_operand_not_modeled:")
        for reason in unsupported
    )
    # Unknown means either outcome is allowed; it is not an exact conclusion.
    for outcome in (formula, z3.Not(formula)):
        solver = z3.Solver()
        solver.add(outcome)
        assert solver.check() == z3.sat


def test_dynamic_comparison_fallback_preserves_prior_limitations():
    _, _, unsupported = _translate(
        "int x", "'A' == (x % 2)", prior=("unmodeled_earlier_state",),
    )
    assert "unmodeled_earlier_state" in unsupported
    assert any(
        reason.startswith("integral_comparison_operand_not_modeled:")
        for reason in unsupported
    )


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("'A' == 65", True),
        ("'A' == 0", False),
        ("'A' > 0xffffffff", True),
        ("(byte) 128 == -128", True),
        ("(char) 65 == 'A'", True),
        ("('A' == 65) && ((byte) 256 == 0)", True),
    ],
)
def test_closed_integral_comparisons_still_have_exact_results(expression, expected):
    formula, _, unsupported = _translate("", expression)
    assert not unsupported
    assert z3.is_true(z3.simplify(formula)) is expected


def test_exact_subexpression_does_not_clear_earlier_limitations():
    _, _, unsupported = _translate(
        "", "('A' == 65) && ((byte) 256 == 0)",
        prior=("unmodeled_earlier_state",),
    )
    assert unsupported == {"unmodeled_earlier_state"}


def test_pure_legacy_integer_comparison_is_not_reclassified():
    formula, variables, unsupported = _translate("int a", "a < 5")
    assert not unsupported
    assert set(variables) == {"a:int@0"}
    solver = z3.Solver()
    solver.add(formula, variables["a:int@0"] >= 5)
    assert solver.check() == z3.unsat


def test_two_unmodeled_calls_are_not_forced_to_return_the_same_character():
    # A stateful next() may return A on its first call and B on its second.
    formula, _, unsupported = _translate(
        "", "next() == 'A' && !(next() == 'A')",
    )
    solver = z3.Solver()
    solver.add(formula)
    assert solver.check() == z3.sat
    assert unsupported


def test_unknowns_do_not_alias_across_different_source_versions():
    atoms, variables, unsupported = {}, {}, set()
    values = []
    for declaration in ("int oldMarker", "int newMarker"):
        node, source = _return_expression(declaration, "next() == 'A'")
        values.append(_java_expression(
            node, source, atoms, variables, 0,
            expected_sort="bool", unsupported=unsupported,
        ))
    solver = z3.Solver()
    solver.add(values[0], z3.Not(values[1]))
    assert solver.check() == z3.sat
    assert unsupported
