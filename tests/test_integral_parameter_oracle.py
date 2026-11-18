"""Compare typed symbolic parameters against Java execution on boundary inputs.

Expected values come from the Java fixture, never a second Python conversion.
Each method's exact return AST is read from that same fixture. The solver must
admit the concrete input and rule out a result different from Java's result.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import z3

import patch_label.analyzer as analyzer


FIXTURE = Path(__file__).parent / "fixtures" / "java" / "sample" / "IntegralParameterOracle.java"


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _methods():
    source = FIXTURE.read_bytes()
    parser = analyzer._java_parser()
    assert parser is not None
    tree = parser.parse(source)
    assert not tree.root_node.has_error
    result = {}
    for node in _walk(tree.root_node):
        if node.type != "method_declaration":
            continue
        name_node = node.child_by_field_name("name")
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8")
        if not name.startswith("probe"):
            continue
        parameters = node.child_by_field_name("parameters").named_children
        assert len(parameters) == 1
        parameter_type = parameters[0].child_by_field_name("type")
        primitive = source[parameter_type.start_byte:parameter_type.end_byte].decode("ascii")
        returns = [item for item in _walk(node) if item.type == "return_statement"]
        assert len(returns) == 1
        expression = returns[0].named_children[0]
        result_type = node.child_by_field_name("type")
        returned = source[result_type.start_byte:result_type.end_byte].decode("ascii")
        assert name not in result
        result[name] = (expression, source, primitive, returned)
    return result


METHODS = _methods()


def _jdk_tools():
    suffix = ".exe" if os.name == "nt" else ""
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        bin_dir = Path(java_home) / "bin"
        java, javac = bin_dir / ("java" + suffix), bin_dir / ("javac" + suffix)
        if java.is_file() and javac.is_file():
            return str(java), str(javac)
    java, javac = shutil.which("java"), shutil.which("javac")
    if java and javac:
        return java, javac
    pytest.skip("Parameter Java oracle requires both java and javac; it was not executed.")


@pytest.fixture(scope="module")
def parameter_oracle(tmp_path_factory):
    java, javac = _jdk_tools()
    classes = tmp_path_factory.mktemp("integral-parameter-oracle")
    compiled = subprocess.run(
        [javac, "-encoding", "UTF-8", "-d", str(classes), str(FIXTURE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run(
        [java, "-cp", str(classes), "sample.IntegralParameterOracle"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    cases = {}
    seen = set()
    for line in executed.stdout.splitlines():
        method, input_type, input_value, result_type, result_value = line.split("\t")
        key = (method, input_type, int(input_value))
        assert key not in seen
        seen.add(key)
        cases.setdefault(method, []).append((input_type, int(input_value), result_type, int(result_value)))
    assert set(cases) == set(METHODS), "Every parsed probe method must execute."
    assert all(len(items) >= 8 for items in cases.values())
    return cases


def test_parameter_oracle_source_is_complete_without_requiring_a_jdk():
    assert len(METHODS) == 18
    assert {item[2] for item in METHODS.values()} == {"byte", "short", "char", "int", "long"}
    assert {item[3] for item in METHODS.values()} == {"byte", "short", "char", "int", "long", "boolean"}


@pytest.mark.parametrize("method", sorted(METHODS))
def test_symbolic_parameter_conversion_matches_actual_java(method, parameter_oracle):
    node, source, declared_input, declared_return = METHODS[method]
    variables = {}
    modeled = analyzer._expression_result(
        node, source, {}, variables, 0,
        expected_sort="bool" if declared_return == "boolean" else "int", force_typed=True,
    )
    assert not modeled.unsupported, (method, modeled.unsupported)
    assert modeled.java_type == declared_return
    assert not modeled.domain_constraints
    assert z3.is_true(z3.simplify(modeled.normal_when))
    parameter_keys = {
        item["parameter_key"] for item in modeled.model_evidence if item.get("parameter_key")
    }
    assert len(parameter_keys) == 1
    key = next(iter(parameter_keys))
    assert key in variables
    for input_type, input_value, result_type, result_value in parameter_oracle[method]:
        assert input_type == declared_input
        assert result_type == declared_return
        input_binding = variables[key] == input_value
        solver = z3.Solver()
        solver.set(timeout=15000)
        solver.add(input_binding)
        assert solver.check() == z3.sat, (method, input_value, "input incorrectly excluded")
        expected = z3.BoolVal(bool(result_value)) if result_type == "boolean" else z3.IntVal(result_value)
        solver.add(modeled.value != expected)
        assert solver.check() == z3.unsat, (method, input_value, result_value, solver.reason_unknown())
