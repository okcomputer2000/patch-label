"""Total, typed integral expressions for the first model batch.

Parameters use fixed-width values: their Java range is part of the value,
not a path assumption that a boundary comparison could accidentally negate.
Dynamic arithmetic and mutable bindings remain unsupported in this batch.
"""
from __future__ import annotations

import hashlib
from typing import Any

try:
    import z3
except ImportError:  # pragma: no cover
    z3 = None

from .integrals import evaluate_integral_constant
from .values import ExprResult, ModelContext

WIDTHS = {"byte": 8, "short": 16, "char": 16, "int": 32, "long": 64}
WIDENING = {
    "byte": {"short", "int", "long"}, "short": {"int", "long"},
    "char": {"int", "long"}, "int": {"long"}, "long": set(),
}


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _operator(node: Any, source: bytes) -> str | None:
    operator = node.child_by_field_name("operator")
    if operator is not None:
        return _text(operator, source)
    return next((_text(c, source) for c in node.children if not c.is_named), None)


def _cast(value: Any, target: str) -> Any:
    width = WIDTHS[target]
    unsigned = value % (1 << width)
    return unsigned if target == "char" else z3.If(
        unsigned >= (1 << (width - 1)), unsigned - (1 << width), unsigned,
    )


def evaluate_typed_expression(
    node: Any, source: bytes, context: ModelContext,
    reasons: set[str], *, active_bindings: frozenset[str] = frozenset(),
) -> ExprResult | None:
    """Return an exact result only for a total expression in this small subset."""
    if node is None or node.has_error or node.is_missing or z3 is None:
        reasons.add("integral_syntax_unavailable")
        return None
    literal = evaluate_integral_constant(node, source)
    if literal is not None:
        return ExprResult(z3.IntVal(literal.value), literal.java_type, z3.BoolVal(True))
    kind = node.type
    if kind in {"true", "false", "true_literal", "false_literal"}:
        return ExprResult(z3.BoolVal(kind.startswith("true")), "boolean", z3.BoolVal(True))
    if kind == "parenthesized_expression" and len(node.named_children) == 1:
        return evaluate_typed_expression(
            node.named_children[0], source, context, reasons, active_bindings=active_bindings,
        )
    if kind == "identifier":
        binding = context.resolve(node)
        if binding.reason:
            reasons.add(f"integral_binding_unavailable:{_text(node, source)}:{binding.reason}")
            return None
        if binding.java_type not in WIDTHS:
            reasons.add(f"integral_type_not_modeled:{binding.java_type}:{_text(node, source)}")
            return None
        if binding.parameter_key is not None:
            width = WIDTHS[binding.java_type]
            name = "java_input_" + hashlib.sha256(binding.parameter_key.encode()).hexdigest()[:24]
            bits = z3.BitVec(name, width)
            value = z3.BV2Int(bits, is_signed=binding.java_type != "char")
            return ExprResult(value, binding.java_type, z3.BoolVal(True), model_evidence=[{
                **binding.evidence, "parameter_key": binding.parameter_key,
                "solver_symbol": name,
            }])
        initializer = binding.initializer_node
        identity = str(binding.evidence.get("declaration_start_byte", node.start_byte))
        if initializer is None or identity in active_bindings:
            reasons.add(f"integral_initializer_unavailable:{_text(node, source)}")
            return None
        result = evaluate_typed_expression(
            initializer, source, context, reasons, active_bindings=active_bindings | {identity},
        )
        if result is None or result.java_type not in WIDTHS:
            return None
        target = binding.java_type
        allowed = result.java_type == target or target in WIDENING[result.java_type]
        constant = z3.simplify(result.value)
        if not allowed and result.java_type in {"byte", "short", "char", "int"} and z3.is_int_value(constant):
            low, high = (0, 65535) if target == "char" else (-(1 << (WIDTHS[target] - 1)), (1 << (WIDTHS[target] - 1)) - 1)
            allowed = target in {"byte", "short", "char"} and low <= constant.as_long() <= high
        if not allowed:
            reasons.add(f"integral_assignment_conversion_unavailable:{result.java_type}->{target}")
            return None
        return ExprResult(
            _cast(result.value, target), target, z3.BoolVal(True),
            model_evidence=[*result.model_evidence, binding.evidence],
        )
    if kind == "cast_expression":
        value_node = node.child_by_field_name("value")
        type_node = node.child_by_field_name("type")
        target = _text(type_node, source) if type_node is not None else ""
        if value_node is None or target not in WIDTHS:
            reasons.add(f"integral_cast_target_unavailable:{target}")
            return None
        result = evaluate_typed_expression(value_node, source, context, reasons, active_bindings=active_bindings)
        if result is None or result.java_type not in WIDTHS:
            reasons.add(f"cast_source_not_modeled:{_text(value_node, source)}")
            return None
        return ExprResult(
            _cast(result.value, target), target, z3.BoolVal(True),
            model_evidence=[*result.model_evidence, {"model": "java-integral-cast-v2", "source_type": result.java_type, "target_type": target, "start_byte": node.start_byte}],
        )
    if kind == "unary_expression" and _operator(node, source) == "!":
        operand = node.child_by_field_name("operand")
        result = evaluate_typed_expression(operand, source, context, reasons, active_bindings=active_bindings)
        if result is not None and result.java_type == "boolean":
            return ExprResult(z3.Not(result.value), "boolean", z3.BoolVal(True), model_evidence=result.model_evidence)
    if kind == "binary_expression":
        symbol = _operator(node, source)
        if symbol in {"<", "<=", ">", ">=", "==", "!=", "&&", "||"}:
            left = evaluate_typed_expression(node.child_by_field_name("left"), source, context, reasons, active_bindings=active_bindings)
            right = evaluate_typed_expression(node.child_by_field_name("right"), source, context, reasons, active_bindings=active_bindings)
            if left is not None and right is not None:
                boolean = left.java_type == right.java_type == "boolean"
                integral = left.java_type in WIDTHS and right.java_type in WIDTHS
                allowed = boolean if symbol in {"&&", "||"} else integral or (boolean and symbol in {"==", "!="})
                if allowed:
                    a, b = left.value, right.value
                    operations = {"<": lambda: a < b, "<=": lambda: a <= b, ">": lambda: a > b, ">=": lambda: a >= b, "==": lambda: a == b, "!=": lambda: a != b, "&&": lambda: z3.And(a, b), "||": lambda: z3.Or(a, b)}
                    return ExprResult(operations[symbol](), "boolean", z3.BoolVal(True), model_evidence=[*left.model_evidence, *right.model_evidence])
    reasons.add(f"typed_integral_not_modeled:{kind}:{_text(node, source)}")
    return None
