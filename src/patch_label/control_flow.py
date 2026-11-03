from __future__ import annotations

import hashlib
import json
from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_java


POLICY_VERSION = "conservative-control-flow-v1"
PRIMITIVES = {"boolean", "byte", "short", "char", "int", "long", "float", "double"}
LITERALS = {
    "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
    "binary_integer_literal", "decimal_floating_point_literal",
    "hex_floating_point_literal", "character_literal", "true", "false",
}
CONTROL_NODES = {
    "if_statement", "while_statement", "do_statement", "for_statement",
    "enhanced_for_statement", "switch_expression", "switch_statement",
    "switch_label", "ternary_expression", "break_statement", "continue_statement",
    "throw_statement", "try_statement", "try_with_resources_statement",
    "catch_clause", "finally_clause", "synchronized_statement", "assert_statement",
}
PURE_BINARY_OPERATORS = {b"+", b"-", b"*", b"<<", b">>", b">>>", b"&", b"|", b"^"}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _source_signatures(source: bytes) -> tuple[Any, Any]:
    parser = Parser(Language(tree_sitter_java.language()))
    tree = parser.parse(source)
    if tree.root_node.has_error:
        raise ValueError("Java source contains parse errors")

    def primitive_return(expression: Any, method: Any) -> bool:
        parameters = method.child_by_field_name("parameters")
        primitive_parameters = set()
        if parameters is not None:
            for parameter in parameters.named_children:
                parameter_type = parameter.child_by_field_name("type")
                name = parameter.child_by_field_name("name")
                dimensions = parameter.child_by_field_name("dimensions")
                if (parameter_type is not None and name is not None and dimensions is None
                        and source[parameter_type.start_byte:parameter_type.end_byte].decode() in PRIMITIVES):
                    primitive_parameters.add(source[name.start_byte:name.end_byte])

        def pure(node: Any) -> bool:
            if node.type in LITERALS:
                return True
            if node.type == "identifier":
                return source[node.start_byte:node.end_byte] in primitive_parameters
            if node.type == "parenthesized_expression":
                children = [child for child in node.named_children
                            if child.type not in {"line_comment", "block_comment"}]
                return len(children) == 1 and pure(children[0])
            if node.type == "binary_expression":
                operator = node.child_by_field_name("operator")
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                return (operator is not None and left is not None and right is not None
                        and source[operator.start_byte:operator.end_byte] in PURE_BINARY_OPERATORS
                        and pure(left) and pure(right))
            if node.type == "unary_expression":
                operator = node.child_by_field_name("operator")
                operand = node.child_by_field_name("operand")
                return (operator is not None and operand is not None
                        and source[operator.start_byte:operator.end_byte] in {b"+", b"-", b"~"}
                        and pure(operand))
            return False

        return pure(expression)

    def signature(node: Any, allow_return_values: bool = False) -> Any:
        if node.type in {"line_comment", "block_comment"}:
            return None
        if allow_return_values and node.type == "return_statement":
            expressions = [child for child in node.named_children
                           if child.type not in {"line_comment", "block_comment"}]
            method = node.parent
            while method is not None and method.type not in {
                "method_declaration", "lambda_expression", "constructor_declaration"
            }:
                method = method.parent
            return_type = method.child_by_field_name("type") if method is not None else None
            if (len(expressions) == 1
                    and return_type is not None
                    and method.child_by_field_name("dimensions") is None
                    and source[return_type.start_byte:return_type.end_byte].decode() in PRIMITIVES
                    and primitive_return(expressions[0], method)):
                return ("pure_primitive_return", source[return_type.start_byte:return_type.end_byte].decode())
        if not node.children:
            return (node.type, source[node.start_byte:node.end_byte].decode("utf-8", errors="strict"))
        return (node.type, tuple(value for child in node.children
                                if (value := signature(child, allow_return_values)) is not None))

    controls = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type in CONTROL_NODES or (
            node.type == "binary_expression" and any(
                source[child.start_byte:child.end_byte] in {b"&&", b"||"}
                for child in node.children
            )
        ):
            if node.type in {"if_statement", "while_statement", "do_statement", "ternary_expression"}:
                condition = node.child_by_field_name("condition")
                controls.append((node.type, signature(condition) if condition else signature(node)))
            else:
                controls.append((node.type, signature(node)))
        pending.extend(reversed(node.children))
    return signature(tree.root_node, True), controls


def _graph_signature(graph: dict[str, Any]) -> Any:
    if graph.get("missing_classes") or not graph.get("nodes"):
        raise ValueError("Missing or empty bytecode CFG")
    return {
        "nodes": sorted((node["id"], node["method_id"], node["virtual"]) for node in graph["nodes"]),
        "edges": sorted((edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]),
    }


def assess_control_flow(
    before_sources: dict[str, bytes], after_sources: dict[str, bytes],
    before_graph: dict[str, Any], after_graph: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "policy": POLICY_VERSION,
        "domain": "intraprocedural CFG and source control decisions in modified classes",
        "status": "unknown",
        "effective_scope": "all",
        "source_sha256": {
            "buggy": {name: hashlib.sha256(value).hexdigest() for name, value in before_sources.items()},
            "patched": {name: hashlib.sha256(value).hexdigest() for name, value in after_sources.items()},
        },
    }
    try:
        old_graph = _graph_signature(before_graph)
        new_graph = _graph_signature(after_graph)
        result["cfg_sha256"] = {"buggy": _fingerprint(old_graph), "patched": _fingerprint(new_graph)}
        if old_graph != new_graph:
            return result | {"status": "changed", "reason": "bytecode_cfg_structure_changed"}
        if not before_sources or before_sources.keys() != after_sources.keys():
            raise ValueError("Missing or mismatched source files")
        old = {name: _source_signatures(value) for name, value in before_sources.items()}
        new = {name: _source_signatures(value) for name, value in after_sources.items()}
        if any(old[name][1] != new[name][1] for name in old):
            return result | {"status": "changed", "reason": "source_control_decisions_changed"}
        if any(old[name][0] != new[name][0] for name in old):
            return result | {"reason": "source_change_not_proven_control_neutral"}
        return result | {
            "status": "unchanged", "effective_scope": "relevant",
            "reason": "identical_cfg_and_source_except_comments_layout_or_pure_primitive_returns",
        }
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        return result | {"reason": "comparison_unavailable", "error": str(error)}
