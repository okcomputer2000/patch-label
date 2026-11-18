"""Integration checks for the bounded, typed first-batch method context.

The before/after formulas deliberately share one parameter identity. Adding a
char literal must not silently turn one input into two independent inputs, and
Java input ranges must not turn into false-branch or XOR-domain artefacts.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
import z3

import patch_label.analyzer as analyzer


def _document(
    root: Path,
    condition: str,
    *,
    parameters: str = "char c",
    descriptor: str = "C",
    prefix: tuple[str, ...] = (),
    method_name: str = "f",
):
    source = root / "sample" / "Foo.java"
    source.parent.mkdir(parents=True)
    lines = [
        "package sample;", "class Foo {",
        f"  boolean {method_name}({parameters}) {{",
        *("    " + line for line in prefix),
        f"    if ({condition}) {{", "      return true;", "    }",
        "    return false;", "  }", "  int helper() { return 65; }", "}",
    ]
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    branch_line = 4 + len(prefix)
    method = f"sample.Foo#{method_name}({descriptor})Z"
    entry, branch, taken, fallthrough, exit_node = (
        f"{method}:{name}" for name in ("ENTRY", "B0", "B1", "B2", "EXIT")
    )
    graph_nodes = []
    for node_id, line, virtual in (
        (entry, -1, True), (branch, branch_line, False),
        (taken, branch_line + 1, False), (fallthrough, branch_line + 3, False),
        (exit_node, -1, True),
    ):
        graph_nodes.append({
            "id": node_id, "class_name": "sample.Foo", "method_id": method,
            "virtual": virtual, "start_line": line, "end_line": line,
        })
    document = {
        "_analysis_source_root": str(root),
        "graph": {
            "nodes": graph_nodes,
            "edges": [
                {"source": entry, "target": branch, "kind": "entry"},
                {"source": branch, "target": taken, "kind": "jump"},
                {"source": branch, "target": fallthrough, "kind": "fallthrough"},
                {"source": taken, "target": exit_node, "kind": "exit"},
                {"source": fallthrough, "target": exit_node, "kind": "exit"},
            ],
        },
    }
    paths = {
        True: {"id": "path:true", "method_id": method, "nodes": [entry, branch, taken, exit_node]},
        False: {"id": "path:false", "method_id": method, "nodes": [entry, branch, fallthrough, exit_node]},
    }
    return document, paths, source


@contextmanager
def _activated(*documents):
    assert len(documents) == 2
    active = analyzer._batch1_methods_for_documents(*documents)
    token = analyzer._BATCH1_METHODS.set(frozenset(active))
    try:
        yield active
    finally:
        analyzer._BATCH1_METHODS.reset(token)


def _constraint(document, paths, *, taken=True, atoms=None, variables=None):
    return analyzer._path_constraint(
        document, paths[taken], Path(document["_analysis_source_root"]),
        {} if atoms is None else atoms, {} if variables is None else variables,
    )


def _result(source: Path, *, atoms=None, variables=None):
    data = source.read_bytes()
    parser = analyzer._java_parser()
    assert parser is not None
    tree = parser.parse(data)
    assert not tree.root_node.has_error
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "if_statement":
            condition = node.child_by_field_name("condition")
            assert condition is not None
            return analyzer._expression_result(
                condition, data, {} if atoms is None else atoms,
                {} if variables is None else variables, 0, expected_sort="bool",
            )
        stack.extend(reversed(node.named_children))
    raise AssertionError("Missing test condition")


def _assert_status(formula, expected):
    solver = z3.Solver()
    solver.add(formula)
    assert solver.check() == expected


def test_before_and_after_share_typed_parameter_when_only_new_side_has_char(tmp_path):
    old, old_paths, _ = _document(tmp_path / "old", "c > 0")
    new, new_paths, _ = _document(tmp_path / "new", r"c > '\0'")
    atoms, variables = {}, {}
    with _activated(old, new) as active:
        assert active
        a, a_evidence = _constraint(old, old_paths, atoms=atoms, variables=variables)
        b, b_evidence = _constraint(new, new_paths, atoms=atoms, variables=variables)
    _assert_status(z3.Xor(a, b), z3.unsat)
    for evidence in (a_evidence, b_evidence):
        constraints = [item for item in evidence if item.get("constraint_type")]
        assert constraints
        assert all(item["precision"] == "exact_subset" for item in constraints)


def test_before_and_after_false_branches_do_not_create_out_of_range_difference(tmp_path):
    old, old_paths, _ = _document(tmp_path / "old", "c > 0")
    new, new_paths, _ = _document(tmp_path / "new", r"c > '\0'")
    atoms, variables = {}, {}
    with _activated(old, new):
        a, _ = _constraint(old, old_paths, taken=False, atoms=atoms, variables=variables)
        b, _ = _constraint(new, new_paths, taken=False, atoms=atoms, variables=variables)
    _assert_status(a, z3.sat)
    _assert_status(z3.Xor(a, b), z3.unsat)
    assert variables
    for value in variables.values():
        numeric = z3.BV2Int(value, is_signed=False) if z3.is_bv(value) else value
        if z3.is_int(numeric):
            _assert_status(z3.And(a, numeric < 0), z3.unsat)
            _assert_status(z3.And(a, numeric > 65535), z3.unsat)


def test_char_range_is_in_the_value_without_non_total_expression_domain(tmp_path):
    document, paths, source = _document(tmp_path / "input", r"c < '\0'")
    with _activated(document, document):
        result = _result(source)
        formula, _ = _constraint(document, paths)
    assert not result.unsupported
    assert not result.domain_constraints
    assert z3.is_true(z3.simplify(result.normal_when))
    _assert_status(formula, z3.unsat)


@pytest.mark.parametrize("value, expected", [(256, z3.sat), (257, z3.unsat), (-256, z3.sat)])
def test_parameter_narrowing_and_other_condition_use_the_same_input(tmp_path, value, expected):
    cast_doc, cast_paths, _ = _document(
        tmp_path / "cast", "(byte) x == 0", parameters="int x", descriptor="I",
    )
    value_doc, value_paths, _ = _document(
        tmp_path / "value", f"x == {value}", parameters="int x", descriptor="I",
    )
    atoms, variables = {}, {}
    with _activated(cast_doc, value_doc):
        a, _ = _constraint(cast_doc, cast_paths, atoms=atoms, variables=variables)
        b, _ = _constraint(value_doc, value_paths, atoms=atoms, variables=variables)
    _assert_status(z3.And(a, b), expected)


def test_single_prefix_initializer_keeps_alias_and_cast_relationship(tmp_path):
    alias_doc, alias_paths, alias_source = _document(
        tmp_path / "alias", "y == 0", parameters="int x", descriptor="I",
        prefix=("byte y = (byte) x;",),
    )
    value_doc, value_paths, _ = _document(
        tmp_path / "value", "x == 257", parameters="int x", descriptor="I",
    )
    atoms, variables = {}, {}
    with _activated(alias_doc, value_doc):
        result = _result(alias_source)
        a, _ = _constraint(alias_doc, alias_paths, atoms=atoms, variables=variables)
        b, _ = _constraint(value_doc, value_paths, atoms=atoms, variables=variables)
    assert not result.unsupported
    _assert_status(z3.And(a, b), z3.unsat)


@pytest.mark.parametrize(
    "parameters, descriptor, prefix, condition",
    [
        ("double d", "D", (), "(long) d == d"),
        ("int x", "I", ("x++;",), "(byte) x == 0"),
        ("int x", "I", ("int y = x;", "y = 2;"), "(byte) y == 2"),
        ("int x", "I", ("for (int i = 0; i < 2; i++) { x++; }",), "(byte) x == 0"),
        ("int x", "I", ("int y;", "if (x > 0) y = x; else y = 0;"), "(byte) y == 0"),
    ],
)
def test_unsupported_source_state_remains_unknown(tmp_path, parameters, descriptor, prefix, condition):
    document, _, source = _document(
        tmp_path / "input", condition, parameters=parameters, descriptor=descriptor, prefix=prefix,
    )
    with _activated(document, document):
        # Select the final if from the concrete source, not an earlier branch
        # from the state-mutating prefix.
        data = source.read_bytes()
        tree = analyzer._java_parser().parse(data)
        stack, conditions = [tree.root_node], []
        while stack:
            node = stack.pop()
            if node.type == "if_statement":
                conditions.append(node.child_by_field_name("condition"))
            stack.extend(node.named_children)
        target = max(conditions, key=lambda node: node.start_byte)
        result = analyzer._expression_result(target, data, {}, {}, 0, expected_sort="bool")
    assert result.unsupported
    _assert_status(result.value, z3.sat)


def test_activated_scope_does_not_change_unrelated_method_identity(tmp_path):
    old, _, _ = _document(tmp_path / "old", "c > 0")
    new, _, _ = _document(tmp_path / "new", r"c > '\0'")
    _, _, unrelated = _document(tmp_path / "other", "c > 0", method_name="g")
    variables = {}
    with _activated(old, new):
        result = _result(unrelated, variables=variables)
    assert not result.unsupported
    assert set(variables) == {"c:int@0"}


def test_unmodeled_call_diagnostic_survives_typed_context_fallback(tmp_path):
    document, _, source = _document(tmp_path / "input", "helper() == 'A'")
    with _activated(document, document):
        result = _result(source)
    assert result.unsupported
    assert any("method_invocation" in reason for reason in result.unsupported)


def _patch_unit(index, old, new):
    line = 10 * index
    return analyzer.PatchUnit(
        index=index, header=f"@@ -{line},1 +{line},1 @@",
        old_start=line, old_count=1, new_start=line, new_count=1,
        old_lines=(old + "\n",), new_lines=(new + "\n",),
        applied_line=None, source_file="sample/Foo.java",
        old_changed_ranges=((line, line),), new_changed_ranges=((line, line),),
    )


def test_unrelated_output_unit_keeps_legacy_mode_when_another_unit_has_a_char():
    units = [
        _patch_unit(1, "return a + 0;", "return a;"),
        _patch_unit(2, "return 65;", "return 'A';"),
    ]
    token = analyzer._BATCH1_METHODS.set(frozenset({"unrelated.Active#f(char)"}))
    atoms, variables = {}, {}
    try:
        full = analyzer._variant_outputs(units, None, atoms, variables)
        reverted = analyzer._variant_outputs(units, 1, atoms, variables)
    finally:
        analyzer._BATCH1_METHODS.reset(token)
    assert len(full) == len(reverted) == 2
    assert not full[0]["unsupported"] and not reverted[0]["unsupported"]
    assert not full[0]["expression_model"] and not reverted[0]["expression_model"]
    assert full[1]["expression_model"]
    assert "a:int@0" in variables
    assert analyzer._compare_symbolic_outputs(full[:1], reverted[:1])["equivalent"] is True
    assert analyzer._compare_symbolic_outputs(full, reverted)["equivalent"] is None


def test_same_unit_uses_typed_constants_on_both_sides_when_only_new_side_has_cast():
    unit = _patch_unit(1, "return 0xffffffff;", "return (int) 0xffffffff;")
    atoms, variables = {}, {}
    full = analyzer._variant_outputs([unit], None, atoms, variables)
    reverted = analyzer._variant_outputs([unit], 1, atoms, variables)
    for outputs in (full, reverted):
        assert len(outputs) == 1
        assert outputs[0]["unsupported"] == {"output_target_type_not_established:int"} or outputs[0]["unsupported"] == ["output_target_type_not_established:int"]
        assert outputs[0]["expression_model"]
        assert z3.simplify(outputs[0]["expression"]).as_long() == -1
    assert analyzer._compare_symbolic_outputs(full, reverted)["equivalent"] is None


def test_same_unit_without_parameter_context_keeps_both_dynamic_outputs_unknown():
    unit = _patch_unit(1, "return x;", "return (byte) x;")
    atoms, variables = {}, {}
    full = analyzer._variant_outputs([unit], None, atoms, variables)
    reverted = analyzer._variant_outputs([unit], 1, atoms, variables)
    for outputs in (full, reverted):
        assert len(outputs) == 1
        assert outputs[0]["unsupported"]
        assert outputs[0]["expression_model"]
    assert "x:int@0" not in variables
    compared = analyzer._compare_symbolic_outputs(full, reverted)
    assert compared["status"] == "unavailable"
    assert compared["equivalent"] is None
