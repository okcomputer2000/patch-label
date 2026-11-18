"""Java 7 integral constants, with Java widths rather than Python widths.

This module does not infer a value or type for an identifier, call, array read,
floating-point expression, or reference. Returning None keeps that expression
unsupported in the analyzer. It is deliberately not a Java interpreter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IntegralValue:
    value: int
    java_type: str


_WIDTHS = {"byte": 8, "short": 16, "char": 16, "int": 32, "long": 64}
_INTEGER_NODES = {
    "decimal_integer_literal", "hex_integer_literal",
    "octal_integer_literal", "binary_integer_literal",
}
_ESCAPES = {
    "b": 8, "t": 9, "n": 10, "f": 12, "r": 13,
    '"': 34, "'": 39, "\\": 92,
}


def decode_java_char_literal(text: str) -> int | None:
    """Decode one Java char, excluding source-level Unicode preprocessing."""
    if len(text) < 3 or text[0] != "'" or text[-1] != "'":
        return None
    body = text[1:-1]
    if body.startswith("\\"):
        escape = body[1:]
        if escape in _ESCAPES:
            return _ESCAPES[escape]
        # A three-digit octal escape must begin with 0..3 (JLS 3.10.6).
        if re.fullmatch(r"(?:[0-7]{1,2}|[0-3][0-7]{2})", escape):
            return int(escape, 8)
        return None
    if len(body) != 1 or body in {"'", "\\", "\r", "\n"}:
        return None
    value = ord(body)
    return value if value <= 0xFFFF else None


def _text(node: Any, source: bytes) -> str:
    try:
        return source[node.start_byte:node.end_byte].decode("utf-8").strip()
    except UnicodeDecodeError:
        # A replacement character would invent a Java char value. Keep an
        # undecodable source token unsupported instead of guessing or raising.
        return ""


def _operator(node: Any, source: bytes) -> str | None:
    field = node.child_by_field_name("operator")
    if field is not None:
        return _text(field, source)
    for child in node.children:
        if not child.is_named:
            return _text(child, source)
    return None


def _wrap(value: int, java_type: str) -> IntegralValue:
    width = _WIDTHS[java_type]
    value %= 1 << width
    if java_type != "char" and value >= 1 << (width - 1):
        value -= 1 << width
    return IntegralValue(value, java_type)


def _integer_literal(node: Any, source: bytes, *, negative_min: bool = False) -> IntegralValue | None:
    token = _text(node, source)
    java_type = "long" if token[-1:] in {"L", "l"} else "int"
    if java_type == "long":
        token = token[:-1]
    width = _WIDTHS[java_type]
    kind = node.type
    if kind == "hex_integer_literal":
        digits, base, pattern = token[2:], 16, r"[0-9a-fA-F](?:[0-9a-fA-F_]*[0-9a-fA-F])?"
        if not token.startswith(("0x", "0X")):
            return None
    elif kind == "binary_integer_literal":
        digits, base, pattern = token[2:], 2, r"[01](?:[01_]*[01])?"
        if not token.startswith(("0b", "0B")):
            return None
    elif kind == "octal_integer_literal":
        digits, base, pattern = token, 8, r"0[0-7_]*[0-7]"
    elif kind == "decimal_integer_literal":
        digits, base, pattern = token, 10, r"(?:0|[1-9](?:[0-9_]*[0-9])?)"
    else:
        return None
    if not re.fullmatch(pattern, digits):
        return None
    value = int(digits.replace("_", ""), base)
    if base == 10:
        limit = (1 << (width - 1)) - 1
        if value > limit:
            if negative_min and value == limit + 1:
                return IntegralValue(-value, java_type)
            return None
        return IntegralValue(value, java_type)
    if value >= 1 << width:
        return None
    return _wrap(value, java_type)


def contains_batch1_syntax(node: Any) -> bool:
    """Limit new arithmetic handling to trees containing a char or a cast."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {"character_literal", "cast_expression"}:
            return True
        stack.extend(current.named_children)
    return False


def evaluate_integral_constant(node: Any, source: bytes) -> IntegralValue | None:
    """Evaluate a closed, supported integral expression with Java semantics.

    A dynamic value is intentionally rejected: the existing unbounded Int
    translator does not establish its Java type, range, or current assignment.
    """
    if node is None or node.has_error or node.is_missing:
        return None
    kind = node.type
    if kind == "character_literal":
        value = decode_java_char_literal(_text(node, source))
        return None if value is None else IntegralValue(value, "char")
    if kind in _INTEGER_NODES:
        return _integer_literal(node, source)
    if kind == "parenthesized_expression":
        if len(node.named_children) != 1:
            return None
        return evaluate_integral_constant(node.named_children[0], source)
    if kind == "cast_expression":
        value_node = node.child_by_field_name("value")
        type_node = node.child_by_field_name("type")
        if value_node is None or type_node is None or type_node.type != "integral_type":
            return None
        target = _text(type_node, source)
        if target not in _WIDTHS:
            return None
        value = evaluate_integral_constant(value_node, source)
        return None if value is None else _wrap(value.value, target)
    if kind == "unary_expression":
        operand = node.child_by_field_name("operand")
        operator = _operator(node, source)
        if operand is None or operator not in {"+", "-", "~"}:
            return None
        if operator == "-" and operand.type == "decimal_integer_literal":
            # The magnitude of MIN_VALUE is only legal directly after '-'.
            special = _integer_literal(operand, source, negative_min=True)
            if special is not None and special.value < 0:
                return special
        value = evaluate_integral_constant(operand, source)
        if value is None:
            return None
        promoted = "long" if value.java_type == "long" else "int"
        result = value.value if operator == "+" else -value.value if operator == "-" else ~value.value
        return _wrap(result, promoted)
    if kind == "binary_expression":
        operator = _operator(node, source)
        if operator not in {"+", "-", "*", "/", "%"}:
            return None
        left = evaluate_integral_constant(node.child_by_field_name("left"), source)
        right = evaluate_integral_constant(node.child_by_field_name("right"), source)
        if left is None or right is None:
            return None
        promoted = "long" if "long" in {left.java_type, right.java_type} else "int"
        a, b = left.value, right.value
        if operator in {"/", "%"}:
            if b == 0:
                return None
            quotient = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                quotient = -quotient
            result = quotient if operator == "/" else a - quotient * b
        else:
            result = a + b if operator == "+" else a - b if operator == "-" else a * b
        return _wrap(result, promoted)
    return None
