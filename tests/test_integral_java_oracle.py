"""Independent Java execution oracle; this does not run Defects4J.

The expressions are parsed from the Java fixture itself. Java overload selection
reports the actual primitive result type, so the Python checks cannot accidentally
use a second handwritten copy of the same expected arithmetic implementation.
Only the execution-oracle tests skip when a JDK is absent; safety checks still run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import z3

from patch_label.analyzer import _java_expression, _java_parser
from patch_label.expression_models.integrals import (
    contains_batch1_syntax,
    decode_java_char_literal,
    evaluate_integral_constant,
)


FIXTURE = Path(__file__).parent / "fixtures" / "java" / "sample" / "IntegralOracle.java"


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _parser():
    parser = _java_parser()
    assert parser is not None, "The declared tree-sitter dependencies are required."
    return parser


def _fixture_expressions(method_name="emit"):
    source = FIXTURE.read_bytes()
    tree = _parser().parse(source)
    assert not tree.root_node.has_error, "The oracle Java fixture must parse without errors."
    expressions = {}
    for node in _walk(tree.root_node):
        if node.type != "method_invocation":
            continue
        name = node.child_by_field_name("name")
        if name is None or source[name.start_byte:name.end_byte] != method_name.encode("ascii"):
            continue
        arguments = node.child_by_field_name("arguments")
        assert arguments is not None
        children = list(arguments.named_children)
        assert len(children) == 2
        case_id = json.loads(source[children[0].start_byte:children[0].end_byte].decode("utf-8"))
        assert case_id not in expressions
        expressions[case_id] = (children[1], source)
    assert expressions, "No executable oracle examples were found."
    return expressions


EXPRESSIONS = _fixture_expressions()
COMPARISON_EXPRESSIONS = _fixture_expressions("emitBoolean")


def _expression(text: str):
    source = ("class Probe { Object value() { return " + text + "; } }").encode("utf-8")
    tree = _parser().parse(source)
    assert not tree.root_node.has_error, f"Invalid test syntax: {text}"
    returns = [n for n in _walk(tree.root_node) if n.type == "return_statement"]
    assert len(returns) == 1
    return returns[0].named_children[0], source


def _jdk_tools():
    suffix = ".exe" if os.name == "nt" else ""
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        folder = Path(java_home) / "bin"
        java, javac = folder / ("java" + suffix), folder / ("javac" + suffix)
        if java.is_file() and javac.is_file():
            return str(java), str(javac)
    java, javac = shutil.which("java"), shutil.which("javac")
    if java and javac:
        return java, javac
    pytest.skip("Java oracle not executed: both java and javac are required (JDK 17 is supported).")


@pytest.fixture(scope="module")
def java_oracle(tmp_path_factory):
    java, javac = _jdk_tools()
    classes = tmp_path_factory.mktemp("integral-java-oracle")
    compiled = subprocess.run(
        [javac, "-encoding", "UTF-8", "-d", str(classes), str(FIXTURE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    assert compiled.returncode == 0, f"javac failed:\n{compiled.stdout}\n{compiled.stderr}"
    executed = subprocess.run(
        [java, "-cp", str(classes), "sample.IntegralOracle"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert executed.returncode == 0, f"java failed:\n{executed.stdout}\n{executed.stderr}"
    result = {}
    for line in executed.stdout.splitlines():
        case_id, java_type, decimal_value = line.split("\t")
        assert case_id not in result
        result[case_id] = (java_type, int(decimal_value))
    assert set(result) == set(EXPRESSIONS) | set(COMPARISON_EXPRESSIONS), (
        "Every fixture expression must actually execute."
    )
    return result


@pytest.mark.parametrize("case_id", list(EXPRESSIONS))
def test_integral_helper_matches_java_type_and_value(case_id, java_oracle):
    node, source = EXPRESSIONS[case_id]
    expected_type, expected_value = java_oracle[case_id]
    result = evaluate_integral_constant(node, source)
    assert result is not None, source[node.start_byte:node.end_byte].decode("utf-8")
    assert result.java_type == expected_type
    assert result.value == expected_value


@pytest.mark.parametrize(
    "case_id",
    [case_id for case_id, (node, _) in EXPRESSIONS.items() if contains_batch1_syntax(node)],
)
def test_existing_analyzer_interface_matches_java_value(case_id, java_oracle):
    node, source = EXPRESSIONS[case_id]
    unsupported = set()
    value = _java_expression(node, source, {}, {}, 0, expected_sort="int", unsupported=unsupported)
    simplified = z3.simplify(value)
    assert not unsupported, (case_id, unsupported)
    assert z3.is_int_value(simplified), (case_id, simplified)
    assert simplified.as_long() == java_oracle[case_id][1]


@pytest.mark.parametrize("case_id", list(COMPARISON_EXPRESSIONS))
def test_new_integral_comparisons_match_java(case_id, java_oracle):
    node, source = COMPARISON_EXPRESSIONS[case_id]
    unsupported = set()
    value = _java_expression(node, source, {}, {}, 0, expected_sort="bool", unsupported=unsupported)
    simplified = z3.simplify(value)
    assert not unsupported, (case_id, unsupported)
    assert z3.is_true(simplified) or z3.is_false(simplified)
    assert java_oracle[case_id][0] == "boolean"
    assert z3.is_true(simplified) == bool(java_oracle[case_id][1])


@pytest.mark.parametrize(
    "literal, expected",
    [("'中'", 0x4E2D), ("'Ω'", 0x03A9), (r"'\377'", 255),
     (r"'\77'", 63), (r"'\0'", 0), (r"'\\'", 92), (r"'\''", 39)],
)
def test_character_decoder_has_independent_basic_checks(literal, expected):
    assert decode_java_char_literal(literal) == expected


@pytest.mark.parametrize(
    "literal",
    ["''", "'ab'", "'😀'", r"'\400'", r"'\777'", r"'\8'", r"'\x41'",
     r"'\s'", r"'\u0041'", r"'\uu0041'", "'\n'", "'\r'", "'", "A"],
)
def test_character_decoder_does_not_guess_invalid_or_deferred_forms(literal):
    assert decode_java_char_literal(literal) is None


def test_cast_uses_the_real_grammar_value_and_type_fields():
    node, source = _expression("(long) x")
    assert node.type == "cast_expression"
    assert node.child_by_field_name("expression") is None
    value_node = node.child_by_field_name("value")
    type_node = node.child_by_field_name("type")
    assert value_node is not None and type_node is not None
    assert source[value_node.start_byte:value_node.end_byte] == b"x"
    assert source[type_node.start_byte:type_node.end_byte] == b"long"


UNSUPPORTED_EXPRESSIONS = [
    "(long) x", "(byte) helper()", "(int) values[0]", "(long) holder.value",
    "(long) 1.9", "(int) null", "(int) true", "(Object) x", "(int) (x + 1)",
    "(byte) (1 / 0)", "(byte) (1 % 0)", "(char) (1L / 0L)", "(long) 1.0f",
    "(long) 1e30", "(int) (++x)", "(int) (x = 1)",
]


@pytest.mark.parametrize("expression", UNSUPPORTED_EXPRESSIONS)
def test_cast_helper_rejects_dynamic_nonintegral_and_undefined_inputs(expression):
    node, source = _expression(expression)
    assert evaluate_integral_constant(node, source) is None


@pytest.mark.parametrize("expression", UNSUPPORTED_EXPRESSIONS)
def test_unsupported_cast_stays_unknown_in_existing_analyzer(expression):
    node, source = _expression(expression)
    unsupported = set()
    _java_expression(node, source, {}, {}, 0, expected_sort="int", unsupported=unsupported)
    assert unsupported, f"Unsupported conversion silently became precise: {expression}"


@pytest.mark.parametrize("expression", ["'A' + x", "(byte) 1 + x", "'A' / x", "'A' % x"])
def test_new_integral_support_does_not_make_dynamic_arithmetic_precise(expression):
    node, source = _expression(expression)
    assert evaluate_integral_constant(node, source) is None
    unsupported = set()
    _java_expression(node, source, {}, {}, 0, expected_sort="int", unsupported=unsupported)
    assert unsupported, "Java width or division semantics are not established for this input."


def test_supported_character_does_not_erase_unrelated_unknown_call():
    node, source = _expression("helper() == 'A'")
    unsupported = set()
    _java_expression(node, source, {}, {}, 0, expected_sort="bool", unsupported=unsupported)
    assert any("method_invocation" in item for item in unsupported)
    assert not any("character_literal" in item for item in unsupported)


def test_supported_character_does_not_clear_preexisting_reasons():
    node, source = _expression("'A'")
    unsupported = {"previously_unmodeled_state"}
    value = _java_expression(node, source, {}, {}, 0, expected_sort="int", unsupported=unsupported)
    assert z3.simplify(value).as_long() == 65
    assert unsupported == {"previously_unmodeled_state"}
