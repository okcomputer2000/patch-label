"""Bindings never turn missing Java state or declaration evidence into a value."""
from __future__ import annotations

import hashlib

import pytest
from tree_sitter import Language, Parser
import tree_sitter_java

from patch_label.expression_models.values import ExprResult, ModelContext


def parse(source: str):
    raw = source.encode("utf-8")
    tree = Parser(Language(tree_sitter_java.language())).parse(raw)
    return tree, raw


def nodes(root, kind):
    stack = [root]
    result = []
    while stack:
        node = stack.pop()
        if node.type == kind:
            result.append(node)
        stack.extend(reversed(node.named_children))
    return result


def use(source: str, name: str):
    tree, raw = parse(source)
    matches = [n for n in nodes(tree.root_node, "identifier") if n.text.decode() == name]
    assert matches
    node = matches[-1]
    return ModelContext.from_node(node, raw), node, raw


def test_parameter_identity_survives_renaming_and_offsets():
    a, node_a, raw = use("package p; class C { int f(char a, int b) { return b; } }", "b")
    b, node_b, _ = use("package p; class C { /* new */ int f(char first, int second) { return second; } }", "second")
    old, new = a.resolve(node_a), b.resolve(node_b)
    assert not old.reason and not new.reason
    assert a.method_key == b.method_key == "p.C#f(char,int)"
    assert old.parameter_key == new.parameter_key == "p.C#f(char,int):parameter:1"
    assert old.evidence["source_sha256"] == hashlib.sha256(raw).hexdigest()
    start, end = old.evidence["declaration_start_byte"], old.evidence["declaration_end_byte"]
    assert raw[start:end] == b"int b"
    assert old.initializer_node is None


@pytest.mark.parametrize("variant", [
    "package q; class C { int f(char a, int b) { return b; } }",
    "package p; class D { int f(char a, int b) { return b; } }",
    "package p; class C { int g(char a, int b) { return b; } }",
    "package p; class C { int f(int a, int b) { return b; } }",
    "package p; class C { long f(char a, long b) { return b; } }",
])
def test_signature_and_owner_prevent_parameter_collisions(variant):
    original, node, _ = use("package p; class C { int f(char a, int b) { return b; } }", "b")
    changed, other, _ = use(variant, "b")
    assert original.resolve(node).parameter_key != changed.resolve(other).parameter_key


def test_same_named_parameters_use_different_ordinals():
    tree, raw = parse("class C { int f(int a, int b) { return a + b; } }")
    references = [n for n in nodes(tree.root_node, "identifier") if n.parent.type == "binary_expression"]
    context = ModelContext.from_node(references[0], raw)
    assert context.resolve(references[0]).parameter_key != context.resolve(references[1]).parameter_key


@pytest.mark.parametrize("java_type", ["byte", "short", "char", "int", "long"])
def test_scalar_integral_parameters_are_accepted(java_type):
    context, node, _ = use(f"class C {{ long f(final {java_type} x) {{ return x; }} }}", "x")
    binding = context.resolve(node)
    assert not binding.reason and binding.java_type == java_type


@pytest.mark.parametrize("parameter", ["double x", "float x", "Object x", "boolean x", "int[] x", "int x[]", "int... x"])
def test_non_integral_parameters_are_rejected(parameter):
    context, node, _ = use(f"class C {{ Object f({parameter}) {{ return x; }} }}", "x")
    binding = context.resolve(node)
    assert binding.reason
    assert binding.parameter_key is None


@pytest.mark.parametrize("mutation", ["x = 3;", "x += 1;", "x++;", "++x;", "x--;", "--x;", "if (false) { x = 1; }"])
def test_any_parameter_write_even_after_use_rejects_it(mutation):
    tree, raw = parse("class C { int f(int x) { int y = x; " + mutation + " return y; } }")
    node = next(n for n in nodes(tree.root_node, "identifier") if n.text == b"x" and n.parent.type == "variable_declarator")
    binding = ModelContext.from_node(node, raw).resolve(node)
    assert binding.reason == "binding_written_in_method"
    assert binding.parameter_key is None


@pytest.mark.parametrize("initializer, declared", [("'A'", "char"), ("65", "char"), ("-128", "byte"), ("x", "int"), ("(byte) x", "byte"), ("x + 1", "int")])
def test_unique_immutable_local_binding_retains_initializer(initializer, declared):
    context, node, raw = use(f"class C {{ long f(int x) {{ {declared} value = {initializer}; return value; }} }}", "value")
    binding = context.resolve(node)
    assert not binding.reason, binding
    assert binding.java_type == declared and binding.parameter_key is None
    assert binding.initializer_node.text.decode() == initializer
    start, end = binding.evidence["declaration_start_byte"], binding.evidence["declaration_end_byte"]
    assert raw[start:end].decode() == f"{declared} value = {initializer};"


def test_local_alias_chain_resolves_back_to_parameter():
    context, node, _ = use("class C { long f(char input) { int first = input; long second = first; return second; } }", "second")
    second = context.resolve(node)
    first = context.resolve(second.initializer_node)
    parameter = context.resolve(first.initializer_node)
    assert not second.reason and not first.reason and not parameter.reason
    assert parameter.parameter_key == "C#f(char):parameter:0"


@pytest.mark.parametrize("body, reason", [
    ("int value; return value;", "local_has_no_initializer"),
    ("int value = x; value = 3; return value;", "binding_written_in_method"),
    ("int value = x; value++; return value;", "binding_written_in_method"),
    ("int value = x; if (x > 0) { return value; } return 0;", "local_use_in_different_block"),
    ("int value = x, other = 0; return value;", "multiple_declarators_not_modeled"),
    ("for (int value = 0; value < 1;) { return value; } return 0;", "local_requires_direct_block_declaration"),
    ("int value = x; { int value = 2; } return value;", "ambiguous_or_missing_declaration"),
    ("int value = x; { int other = 2; } value += 1; return value;", "binding_written_in_method"),
    ("int value = next(); return value;", "initializer_unsupported_expression:method_invocation"),
    ("char value = x; return value;", "initializer_assignment_conversion_not_supported"),
    ("byte value = 128; return value;", "initializer_assignment_conversion_not_supported"),
    ("int value = (int) 1.0; return value;", "initializer_unsupported_expression:decimal_floating_point_literal"),
    ("int value[] = new int[1]; return value;", "local_is_not_scalar_integral"),
    ("if (x < 0) return 0; int value = x; return value;", "local_after_method_entry_declaration_prefix"),
    ("work(); int value = x; return value;", "local_after_method_entry_declaration_prefix"),
    ("{ int value = x; return value; }", "local_not_in_method_entry_block"),
])
def test_local_rejections(body, reason):
    context, node, _ = use("class C { int f(int x) { " + body + " } }", "value")
    binding = context.resolve(node)
    assert binding.reason == reason
    assert binding.parameter_key is None and binding.initializer_node is None


def test_write_to_dependency_rejects_alias_chain():
    context, node, _ = use("class C { int f(int x) { int first = x; int second = first; x++; return second; } }", "second")
    assert "binding_written_in_method" in context.resolve(node).reason


def test_parameter_can_be_used_inside_loop_when_never_written():
    context, node, _ = use("class C { int f(int x) { for (int i=0; i<1; i++) { return x; } return 0; } }", "x")
    assert not context.resolve(node).reason
    assert context.resolve(node).parameter_key == "C#f(int):parameter:0"


def test_comments_do_not_end_method_entry_declaration_prefix():
    context, node, _ = use("class C { int f(int x) { /* input */ int first=x; // alias\n int second=first; return second; } }", "second")
    assert not context.resolve(node).reason


def test_later_declaration_is_not_used_for_earlier_reference():
    tree, raw = parse("class C { int f() { int y = value; int value = 1; return y; } }")
    node = next(n for n in nodes(tree.root_node, "identifier") if n.text == b"value")
    assert ModelContext.from_node(node, raw).resolve(node).reason == "local_declaration_not_before_use"


def test_fragment_and_mismatched_source_are_rejected():
    context, node, raw = use("int f(int x) { return x; }", "x")
    assert context.resolve(node).reason
    tree, raw = parse("class C { int f(int x) { return x; } }")
    node = [n for n in nodes(tree.root_node, "identifier") if n.text == b"x"][-1]
    assert ModelContext.from_node(node, raw.replace(b"return", b"xxxxxx")).resolve(node).reason == "incomplete_or_mismatched_source"


def test_foreign_method_node_cannot_be_resolved_by_context():
    tree, raw = parse("class C { int f(int x) { return x; } int g(int x) { return x; } }")
    uses = [n for n in nodes(tree.root_node, "identifier") if n.text == b"x" and n.parent.type == "return_statement"]
    context = ModelContext.from_node(uses[0], raw)
    assert context.resolve(uses[1]).reason == "identifier_from_other_method"


def test_field_access_is_not_a_local_or_parameter_use():
    tree, raw = parse("class C { int f(int x) { return this.x; } }")
    node = [n for n in nodes(tree.root_node, "identifier") if n.text == b"x"][-1]
    assert ModelContext.from_node(node, raw).resolve(node).reason == "identifier_is_not_a_simple_value_use"


def test_shadowing_in_nested_lambda_is_rejected():
    context, node, _ = use("class C { int f(int x) { Object fn = (x) -> x; return x; } }", "x")
    assert context.resolve(node).reason == "ambiguous_or_missing_declaration"


@pytest.mark.parametrize("body", [
    "Object fn = () -> x; return 0;",
    "Object obj = new Object() { int field = x; }; return 0;",
])
def test_nested_executable_scope_is_not_the_enclosing_method_state(body):
    context, node, _ = use("class C { int f(int x) { " + body + " } }", "x")
    assert context.resolve(node).reason == "identifier_in_nested_executable_scope"


def test_parameter_shadowing_in_catch_is_rejected():
    context, node, _ = use("class C { int f(int x) { try { work(); } catch (Exception x) { } return x; } }", "x")
    assert context.resolve(node).reason == "ambiguous_or_missing_declaration"


def test_nested_named_class_has_qualified_identity():
    context, node, _ = use("package p; class Outer { static class Inner { int f(int x) { return x; } } }", "x")
    assert context.method_key == "p.Outer.Inner#f(int)"
    assert context.resolve(node).parameter_key == "p.Outer.Inner#f(int):parameter:0"


def test_incomplete_source_and_unicode_preprocessing_are_not_exact():
    context, node, _ = use("class C { int f(int x) { return x; }", "x")
    assert context.resolve(node).reason == "incomplete_or_mismatched_source"
    context, node, _ = use(r"class C { int f(int x) { /* \u000a */ return x; } }", "x")
    assert context.resolve(node).reason == "java_unicode_preprocessing_not_modeled"


def test_expr_result_does_not_share_mutable_evidence_or_create_solver_value():
    first, second = ExprResult(), ExprResult()
    first.unsupported.add("reason")
    first.model_evidence.append({"one": 1})
    first.domain_constraints.append(True)
    first.exceptions.append("exception")
    assert second.value is None and second.java_type is None and second.normal_when is True
    assert second.unsupported == set() and second.model_evidence == []
    assert second.domain_constraints == [] and second.exceptions == []


def method_context(source):
    tree, raw = parse(source)
    method = nodes(tree.root_node, "method_declaration")[0]
    return ModelContext.from_node(method, raw)


@pytest.mark.parametrize("source,method_id", [
    ("package org.apache.commons.codec.language; class Soundex { char getMappingCode(String text, int index) { return 'A'; } }",
     "org.apache.commons.codec.language.Soundex#getMappingCode(Ljava/lang/String;I)C"),
    ("class C { void f(boolean a, byte b, short c, char d, int e, long f, float g, double h) { } }",
     "C#f(ZBSCIJFD)V"),
    ("class C { String[][] f(int[] a, char b[][], Object... objects) { return null; } }",
     "C#f([I[[C[Ljava/lang/Object;)[[Ljava/lang/String;"),
    ("class C { int f()[] { return null; } }", "C#f()[I"),
    ("package p; import java.lang.CharSequence; class C { CharSequence f(CharSequence x) { return x; } }",
     "p.C#f(Ljava/lang/CharSequence;)Ljava/lang/CharSequence;"),
    ("package p; class C { java.lang.String f(java.lang.CharSequence x) { return null; } }",
     "p.C#f(Ljava/lang/CharSequence;)Ljava/lang/String;"),
    ("package p; class Outer { static class Inner { int f(int x) { return x; } } }",
     "p.Outer$Inner#f(I)I"),
    ("import java.util.*; class C { int f(int x) { return x; } }", "C#f(I)I"),
    ("import java.util.*; class C { int f(java.lang.String x) { return 0; } }",
     "C#f(Ljava/lang/String;)I"),
    ("package p; class C { C f(C x) { return x; } }", "p.C#f(Lp/C;)Lp/C;"),
    ("package p; class String {} class C { String f(String x) { return x; } }",
     "p.C#f(Lp/String;)Lp/String;"),
])
def test_graph_method_requires_complete_supported_descriptor(source, method_id):
    assert method_context(source).match_graph_method({"method_id": method_id}) == (True, "")


@pytest.mark.parametrize("method_id", [
    "other.C#f(I)I", "p.C#g(I)I", "p.C#f(J)I", "p.C#f(I)J",
    "p.C#f(II)I", "p.C#f([I)I", "p.C#f(I)Igarbage", "p.C#f(I)",
    "p.C#<init>(I)V", "p.C#<clinit>()V", "p/C#f(I)I", "p.C#f(I)V",
])
def test_graph_method_mismatches_never_use_nearest_line_or_name_only(method_id):
    context = method_context("package p; class C { int f(int x) { return x; } }")
    matched, reason = context.match_graph_method({"method_id": method_id})
    assert not matched and reason


def test_nested_owner_uses_dollar_not_package_separator():
    context = method_context("package p; class Outer { static class Inner { int f(int x) { return x; } } }")
    assert context.match_graph_method({"method_id": "p.Outer.Inner#f(I)I"}) == (False, "graph_method_owner_mismatch")


def test_overloaded_method_must_match_actual_parameter_types():
    source = "class C { int f(int x) { return x; } int f(long x) { return 0; } }"
    context = method_context(source)
    assert context.match_graph_method({"method_id": "C#f(I)I"})[0]
    assert context.match_graph_method({"method_id": "C#f(J)I"}) == (False, "graph_method_descriptor_mismatch")


@pytest.mark.parametrize("source,method_id", [
    ("import java.util.*; class C { int f(List x) { return 0; } }", "C#f(Ljava/util/List;)I"),
    ("import other.*; class C { int f(String x) { return 0; } }", "C#f(Ljava/lang/String;)I"),
    ("import java.util.Date; import java.sql.Date; class C { int f(Date x) { return 0; } }", "C#f(Ljava/sql/Date;)I"),
    ("import java.util.List; class C { int f(List<String> x) { return 0; } }", "C#f(Ljava/util/List;)I"),
    ("class C { <T> int f(T x) { return 0; } }", "C#f(Ljava/lang/Object;)I"),
    ("class C<T> { int f(int x) { return x; } }", "C#f(I)I"),
    ("package p; class C { int f(Unknown x) { return 0; } }", "p.C#f(Lp/Unknown;)I"),
    ("class C extends Parent { int f(String x) { return 0; } }", "C#f(Ljava/lang/String;)I"),
    ("import java.lang.String; class C extends Parent { int f(String x) { return 0; } }", "C#f(Ljava/lang/String;)I"),
    ("import java.util.Map.Entry; class C { int f(Entry x) { return 0; } }", "C#f(Ljava/util/Map$Entry;)I"),
    ("import java.util.Map.Entry; class C { int f(Entry x) { return 0; } }", "C#f(Ljava/util/Map/Entry;)I"),
    ("import java.util.Date; class C { Date f(Date x) { return x; } }", "C#f(Ljava/util/Date;)Ljava/util/Date;"),
    ("class C { int f(p.Outer.Inner x) { return 0; } }", "C#f(Lp/Outer/Inner;)I"),
    ("class C { int f(int x) { return x; } long f(int x) { return x; } }", "C#f(I)I"),
])
def test_unresolved_source_signature_does_not_claim_cfg_match(source, method_id):
    matched, reason = method_context(source).match_graph_method({"method_id": method_id})
    assert not matched and reason


def test_constructor_node_is_not_mistaken_for_nearby_method():
    tree, raw = parse("class C { C(int x) { work(); } int f(int x) { return x; } }")
    constructor = nodes(tree.root_node, "constructor_declaration")[0]
    context = ModelContext.from_node(constructor, raw)
    assert not context.match_graph_method({"method_id": "C#f(I)I"})[0]
    method = method_context("class C { C(int x) { work(); } int f(int x) { return x; } }")
    assert not method.match_graph_method({"method_id": "C#<init>(I)V"})[0]


@pytest.mark.parametrize("value", [{}, {"method_id": None}, {"method_id": 1}, None])
def test_graph_method_id_must_be_present_and_text(value):
    assert method_context("class C { int f(int x) { return x; } }").match_graph_method(value) == (False, "missing_graph_method_id")
