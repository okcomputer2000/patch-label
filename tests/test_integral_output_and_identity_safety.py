import pytest
import z3

from patch_label import analyzer
from patch_label.expression_models.values import ModelContext


def _condition(source):
    source = source.encode()
    tree = analyzer._java_parser().parse(source)
    assert not tree.root_node.has_error
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "if_statement":
            return node.child_by_field_name("condition"), source
        stack.extend(reversed(node.named_children))
    raise AssertionError("missing condition")


@pytest.mark.parametrize("old,new", [("(byte)1", "1"), ("'A'", "65")])
def test_equal_numbers_with_unknown_output_target_do_not_prove_equivalence(old, new):
    left = analyzer._extract_symbolic_outputs((f"return {old};\n",), {}, {}, force_typed=True)
    right = analyzer._extract_symbolic_outputs((f"return {new};\n",), {}, {}, force_typed=True)
    assert z3.is_true(z3.simplify(left[0]["expression"] == right[0]["expression"]))
    assert left[0]["java_type"] != right[0]["java_type"]
    assert all(item["unsupported"] for item in left + right)
    compared = analyzer._compare_symbolic_outputs(left, right)
    assert compared["status"] == "unavailable"
    assert compared["equivalent"] is None


def test_package_spelling_shadowed_by_a_type_does_not_accept_wrong_descriptor():
    node, source = _condition("""package p;
class C {} class p { static class C {} }
class D { boolean f(p.C ref, int x) { if ((byte)x == 0) return true; return false; } }
""")
    context = ModelContext.from_node(node, source)
    matched, reason = context.match_graph_method({"method_id": "p.D#f(Lp/C;I)Z"})
    assert not matched
    assert reason


def test_resolved_method_signature_gates_shared_parameters():
    for source, method_id in [
        ("package p; class C { boolean f(int x, String s) { if ((byte)x == 0) return true; return false; } }", "p.C#f(ILjava/lang/String;)Z"),
        ("package p; class String {} class C { boolean f(int x, String s) { if ((byte)x == 0) return true; return false; } }", "p.C#f(ILp/String;)Z"),
    ]:
        node, raw = _condition(source)
        token = analyzer._BATCH1_SHARED_METHODS.set(frozenset())
        try:
            result = analyzer._expression_result(node, raw, {}, {}, 0, "bool", graph_node={"method_id": method_id})
        finally:
            analyzer._BATCH1_SHARED_METHODS.reset(token)
        assert "paired_method_signature_not_established" in result.unsupported
