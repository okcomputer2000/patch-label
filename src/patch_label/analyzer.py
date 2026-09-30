from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .patching import Hunk, apply_context_patch, parse_unified_patch

try:
    import z3
except ImportError:  # pragma: no cover - exercised when the optional solver is absent
    z3 = None

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java
except ImportError:  # pragma: no cover - dependency is installed by the project
    Language = None
    Parser = None
    tree_sitter_java = None


@dataclass(frozen=True, slots=True)
class PatchUnit:
    index: int
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    applied_line: int | None
    source_file: str | None
    old_changed_ranges: tuple[tuple[int, int], ...]
    new_changed_ranges: tuple[tuple[int, int], ...]

    @property
    def old_end(self) -> int:
        return self.old_start + max(self.old_count, 1) - 1

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_count, 1) - 1


def _hunk_numbers(hunk: Hunk) -> tuple[int, int, int, int]:
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hunk.header)
    if match is None:
        raise ValueError(f"Unsupported hunk header: {hunk.header}")
    old_start, old_count, new_start, new_count = match.groups()
    return int(old_start), int(old_count or 1), int(new_start), int(new_count or 1)


def load_patch_units(patch_file: Path, manifest_file: Path | None = None) -> list[PatchUnit]:
    hunks = parse_unified_patch(patch_file)
    manifest_hunks: dict[int, dict[str, Any]] = {}
    if manifest_file is not None and manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest_hunks = {int(item["hunk"]): item for item in manifest.get("hunks", [])}
    units: list[PatchUnit] = []
    for index, hunk in enumerate(hunks, start=1):
        old_start, old_count, new_start, new_count = _hunk_numbers(hunk)
        applied = manifest_hunks.get(index, {})
        old_line = int(applied.get("line", old_start))
        new_line = int(applied.get("line", new_start))
        old_changed: list[int] = []
        new_changed: list[int] = []
        for line in hunk.lines:
            prefix = line[:1]
            if prefix == "-":
                old_changed.append(old_line)
                old_line += 1
            elif prefix == "+":
                new_changed.append(new_line)
                new_line += 1
            else:
                old_line += 1
                new_line += 1
        units.append(PatchUnit(
            index=index,
            header=hunk.header,
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            old_lines=hunk.old_lines,
            new_lines=hunk.new_lines,
            applied_line=applied.get("line"),
            source_file=applied.get("source_file"),
            old_changed_ranges=_ranges(old_changed),
            new_changed_ranges=_ranges(new_changed),
        ))
    return units


def _ranges(lines: list[int]) -> tuple[tuple[int, int], ...]:
    if not lines:
        return ()
    result: list[tuple[int, int]] = []
    start = previous = lines[0]
    for line in lines[1:]:
        if line != previous + 1:
            result.append((start, previous))
            start = line
        previous = line
    result.append((start, previous))
    return tuple(result)


def _line_interval(node: dict[str, Any]) -> tuple[int, int] | None:
    start, end = node.get("start_line"), node.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < 0:
        return None
    return start, max(start, end)


def _overlaps(node: dict[str, Any], start: int, end: int) -> bool:
    interval = _line_interval(node)
    return interval is not None and interval[0] <= end and start <= interval[1]


def _path_labels(document: dict[str, Any], wanted: str) -> list[dict[str, Any]]:
    by_id = {path["id"]: path for path in document.get("path_set", [])}
    return [
        {"path": by_id[item["path_id"]], "label": item}
        for item in document.get("labels", [])
        if item.get("label") == wanted and item.get("path_id") in by_id
    ]


def _path_test_ids(
    document: dict[str, Any], path_id: str, wanted: str
) -> set[str]:
    return {
        str(item["test_id"])
        for item in document.get("labels", [])
        if item.get("path_id") == path_id
        and item.get("label") == wanted
        and item.get("test_id")
    }


def _node_key(node: dict[str, Any]) -> tuple[Any, ...]:
    interval = _line_interval(node)
    return interval if interval is not None else (node.get("virtual"), node.get("id", "").rsplit(":", 1)[-1])


def _path_signature(document: dict[str, Any], path: dict[str, Any], unit: PatchUnit | None = None) -> tuple[Any, ...]:
    nodes = {node["id"]: node for node in document["graph"]["nodes"]}
    values: list[Any] = [path.get("method_id")]
    for node_id in path.get("nodes", []):
        node = nodes.get(node_id, {"id": node_id})
        if unit is not None:
            start, end = unit.new_start, unit.new_end
            if _overlaps(node, start, end):
                values.append(("PATCH",))
                continue
        values.append(_node_key(node))
    return tuple(values)


def _outside_signature(document: dict[str, Any], path: dict[str, Any], units: list[PatchUnit]) -> tuple[Any, ...]:
    nodes = {node["id"]: node for node in document["graph"]["nodes"]}
    values: list[Any] = [path.get("method_id")]
    for node_id in path.get("nodes", []):
        node = nodes.get(node_id, {"id": node_id})
        if any(
            _overlaps(
                node,
                unit.applied_line or unit.new_start,
                (unit.applied_line or unit.new_start) + max(unit.new_count, 1) - 1,
            )
            for unit in units
        ):
            continue
        values.append(_node_key(node))
    return tuple(values)


def _path_touches_units(document: dict[str, Any], path: dict[str, Any], units: list[PatchUnit]) -> bool:
    nodes = {node["id"]: node for node in document["graph"]["nodes"]}
    path_nodes = [nodes[node_id] for node_id in path.get("nodes", []) if node_id in nodes]
    return any(
        any(
            any(_overlaps(node, start, end) for start, end in unit.old_changed_ranges)
            for unit in units
        )
        for node in path_nodes
    )


def _path_touches_any_unit(
    document: dict[str, Any], path: dict[str, Any], units: list[PatchUnit], *, patched: bool
) -> bool:
    return any(_path_touches_unit(document, path, unit, patched=patched) for unit in units)


def _path_touches_unit(
    document: dict[str, Any], path: dict[str, Any], unit: PatchUnit, *, patched: bool
) -> bool:
    nodes = {node["id"]: node for node in document["graph"]["nodes"]}
    ranges = unit.new_changed_ranges if patched else unit.old_changed_ranges
    return any(
        any(_overlaps(nodes[node_id], start, end) for start, end in ranges)
        for node_id in path.get("nodes", [])
        if node_id in nodes
    )


def _document_source_root(document: dict[str, Any]) -> Path | None:
    metadata = document.get("metadata", {})
    recorded_root = metadata.get("patch_label.source_root") or document.get("_analysis_source_root")
    if recorded_root:
        root = Path(str(recorded_root))
        if root.is_dir():
            return root
    class_dir = metadata.get("dir.src.classes")
    classpath = metadata.get("cp.test", "")
    if class_dir and classpath:
        for entry in classpath.split(os.pathsep):
            candidate = Path(entry)
            if candidate.name in {"build", "classes"}:
                root = candidate.parent / str(class_dir)
                if root.is_dir():
                    return root
    patch_application = document.get("patch_application") or {}
    source_parts = tuple(Path(str(class_dir)).parts) if class_dir else ()
    for hunk in patch_application.get("hunks", []):
        source_file = Path(str(hunk.get("source_file", "")))
        parts = source_file.parts
        if source_parts:
            for index in range(len(parts) - len(source_parts) + 1):
                if parts[index : index + len(source_parts)] == source_parts:
                    root = Path(*parts[:index], *source_parts)
                    if root.is_dir():
                        return root
    return None


def _path_is_sat(document: dict[str, Any], path: dict[str, Any]) -> dict[str, Any]:
    edges = {(edge["source"], edge["target"]) for edge in document["graph"].get("edges", [])}
    path_edges = list(zip(path.get("nodes", []), path.get("nodes", [])[1:]))
    missing = [edge for edge in path_edges if edge not in edges]
    if missing:
        return {"sat": False, "solver": "structural", "missing_edges": missing}
    if z3 is None:
        return {"sat": True, "solver": "structural", "constraints": len(path_edges)}
    atoms: dict[str, Any] = {}
    variables: dict[str, Any] = {}
    formula, evidence = _path_constraint(
        document, path, _document_source_root(document), atoms, variables
    )
    solved = _solve_formula(formula, atoms, variables)
    solved.update({
        "solver": "z3-source-expressions-with-abstract-fallback"
        if any(item.get("constraint_type") == "source_expression" for item in evidence)
        else "z3-abstract-branches",
        "edge_constraints": evidence,
    })
    return solved


def _modified_nodes(document: dict[str, Any], unit: PatchUnit, *, patched: bool) -> list[dict[str, Any]]:
    ranges = unit.new_changed_ranges if patched else unit.old_changed_ranges
    source_file = unit.source_file or ""
    class_name = Path(source_file.replace("\\", "/")).stem
    return [
        node for node in document["graph"]["nodes"]
        if (not class_name or str(node.get("class_name", "")).rsplit(".", 1)[-1] == class_name)
        and any(_overlaps(node, start, end) for start, end in ranges)
    ]


def _branch_evidence(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    old_nodes = {node["id"]: node for unit in units for node in _modified_nodes(buggy, unit, patched=False)}
    new_nodes = {node["id"]: node for unit in units for node in _modified_nodes(patched, unit, patched=True)}
    old_out: dict[tuple[Any, ...], int] = {}
    new_out: dict[tuple[Any, ...], int] = {}
    for document, selected, output in ((buggy, old_nodes, old_out), (patched, new_nodes, new_out)):
        outgoing: dict[str, set[str]] = {}
        for edge in document["graph"].get("edges", []):
            outgoing.setdefault(edge["source"], set()).add(edge["target"])
        for node in selected.values():
            output[_node_key(node)] = len(outgoing.get(node["id"], set()))
    increased = [key for key, degree in new_out.items() if degree > old_out.get(key, 0) and degree > 1]
    return {
        "new_branch": bool(increased),
        "increased_branch_nodes": [list(key) for key in increased],
        "old_modified_node_count": len(old_nodes),
        "new_modified_node_count": len(new_nodes),
    }


def _branch_choice(
    document: dict[str, Any], source: str, edge: dict[str, Any], outgoing: list[dict[str, Any]]
) -> tuple[str, str] | None:
    if len(outgoing) < 2:
        return None
    nodes = {node["id"]: node for node in document.get("graph", {}).get("nodes", [])}
    node = nodes.get(source, {"id": source})
    predicate = f"{node.get('method_id', '')}:{_node_key(node)}"
    ordered = sorted(outgoing, key=lambda item: (item.get("target", ""), item.get("kind", "")))
    if edge.get("kind") == "jump":
        polarity = "true"
    elif edge.get("kind") == "fallthrough":
        polarity = "false"
    elif edge.get("kind") == "switch-case":
        polarity = f"case:{ordered.index(edge)}"
    elif edge.get("kind") == "switch-default":
        polarity = "default"
    else:
        polarity = f"edge:{ordered.index(edge)}"
    return predicate, polarity


def _java_parser() -> Any | None:
    if Language is None or Parser is None or tree_sitter_java is None:
        return None
    return Parser(Language(tree_sitter_java.language()))


def _source_conditions(source_file: Path) -> dict[int, list[dict[str, Any]]]:
    """Extract branch conditions with tree-sitter source locations.

    The parser supplies Java syntax nodes and therefore handles nested calls,
    strings, comments, and multiline expressions without trying to tokenize
    Java with regular expressions.
    """
    if not source_file.is_file():
        return {}
    parser = _java_parser()
    if parser is None:
        return {}
    source = source_file.read_bytes()
    tree = parser.parse(source)
    conditions: dict[int, list[dict[str, Any]]] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in {"if_statement", "while_statement", "do_statement", "for_statement"}:
            condition = node.child_by_field_name("condition")
            if condition is not None:
                record = {
                    "node": condition,
                    "source": source,
                    "start_line": condition.start_point[0] + 1,
                    "end_line": condition.end_point[0] + 1,
                    "statement_start_line": node.start_point[0] + 1,
                    "statement_end_line": node.end_point[0] + 1,
                    "source_file": str(source_file),
                }
                conditions.setdefault(node.start_point[0] + 1, []).append(record)
        stack.extend(reversed(node.named_children))
    return conditions


def _condition_from_lines(lines: tuple[str, ...]) -> dict[str, Any] | None:
    parser = _java_parser()
    if parser is None:
        return None
    body = "".join(lines)
    source = f"class __PatchLabel__ {{ void __condition__() {{\n{body}\n}} }}".encode()
    tree = parser.parse(source)
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in {"if_statement", "while_statement", "do_statement", "for_statement"}:
            condition = node.child_by_field_name("condition")
            if condition is not None:
                return {
                    "node": condition,
                    "source": source,
                    "start_line": condition.start_point[0] + 1,
                    "end_line": condition.end_point[0] + 1,
                    "statement_start_line": node.start_point[0] + 1,
                    "statement_end_line": node.end_point[0] + 1,
                    "source_file": "<patch-unit>",
                }
        stack.extend(reversed(node.named_children))
    return None


def _source_file_for_node(source_root: Path | None, node: dict[str, Any]) -> Path | None:
    if source_root is None:
        return None
    class_name = str(node.get("class_name", "")).split("$", 1)[0]
    if not class_name:
        return None
    candidate = source_root / Path(*class_name.split(".")).with_suffix(".java")
    return candidate if candidate.is_file() else None


def _condition_for_node(
    source_root: Path | None,
    node: dict[str, Any],
    cache: dict[Path, dict[int, list[dict[str, Any]]]],
) -> dict[str, Any] | None:
    source_file = _source_file_for_node(source_root, node)
    interval = _line_interval(node)
    if source_file is None or interval is None:
        return None
    if source_file not in cache:
        cache[source_file] = _source_conditions(source_file)
    candidates = [
        record
        for records in cache[source_file].values()
        for record in records
        if record["statement_start_line"] <= interval[1]
        and interval[0] <= record["statement_end_line"]
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda record: (
            not (
                record["start_line"] <= interval[1]
                and interval[0] <= record["end_line"]
            ),
            abs(record["start_line"] - interval[0]),
            record["statement_end_line"] - record["statement_start_line"],
        ),
    )


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _java_literal(node: Any, source: bytes) -> Any | None:
    text = _node_text(node, source).replace("_", "")
    suffix = text[-1:] if text else ""
    if suffix in {"l", "L"}:
        text = text[:-1]
    if not text:
        return None
    try:
        if text.startswith(("0x", "0X")):
            return z3.IntVal(int(text[2:], 16))
        if text.startswith(("0b", "0B")):
            return z3.IntVal(int(text[2:], 2))
        if len(text) > 1 and text.startswith("0") and text.isdigit():
            return z3.IntVal(int(text, 8))
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return z3.IntVal(int(text))
    except ValueError:
        return None
    return None


def _java_operator(node: Any, source: bytes) -> str | None:
    operator = next((child for child in node.children if not child.is_named), None)
    return _node_text(operator, source) if operator is not None else None


def _java_sort_hint(node: Any, source: bytes) -> str | None:
    if node.type in {"true", "false", "instanceof_expression"}:
        return "bool"
    if node.type == "parenthesized_expression":
        inner = node.child_by_field_name("expression") or _named_child(node)
        return _java_sort_hint(inner, source) if inner is not None else None
    if node.type == "unary_expression" and _java_operator(node, source) == "!":
        return "bool"
    if node.type == "binary_expression":
        operator = _java_operator(node, source)
        if operator in {"&&", "||", "<", "<=", ">", ">=", "==", "!="}:
            return "bool"
        if operator in {"+", "-", "*", "/", "%"}:
            return "int"
    if node.type in {
        "decimal_integer_literal",
        "hex_integer_literal",
        "octal_integer_literal",
        "binary_integer_literal",
        "null_literal",
    }:
        return "int"
    return None


def _java_variable(
    name: str,
    variables: dict[str, Any],
    occurrence: int,
    sort: str,
) -> Any:
    key = f"{name}:{sort}@{occurrence}"
    safe_name = "value_" + hashlib.sha1(key.encode()).hexdigest()[:16]
    constructor = z3.Bool if sort == "bool" else z3.Int
    return variables.setdefault(key, constructor(safe_name))


def _java_atom(
    node: Any,
    source: bytes,
    atoms: dict[str, Any],
    variables: dict[str, Any],
    occurrence: int,
    sort: str,
    unsupported: set[str],
) -> Any:
    text = _node_text(node, source)
    unsupported.add(f"{node.type}:{text}")
    if sort != "bool":
        return _java_variable(text, variables, occurrence, sort)
    key = f"condition:{text}@{occurrence}"
    return atoms.setdefault(
        key, z3.Bool("condition_" + hashlib.sha1(key.encode()).hexdigest()[:16])
    )


def _named_child(node: Any, index: int = 0) -> Any | None:
    children = list(node.named_children)
    return children[index] if len(children) > index else None


def _java_expression(
    node: Any,
    source: bytes,
    atoms: dict[str, Any],
    variables: dict[str, Any],
    occurrence: int,
    expected_sort: str | None = None,
    unsupported: set[str] | None = None,
) -> Any:
    """Translate the supported Java expression AST subset to Z3.

    Calls, object accesses, and other expressions whose semantics require a
    type/model are represented as stable Boolean atoms instead of guessed text
    parsing. Unsupported syntax is therefore explicit in the evidence.
    """
    unsupported = unsupported if unsupported is not None else set()
    node_type = node.type
    result_sort = expected_sort or _java_sort_hint(node, source) or "int"
    if node_type == "parenthesized_expression":
        inner = node.child_by_field_name("expression") or _named_child(node)
        return (
            _java_expression(
                inner,
                source,
                atoms,
                variables,
                occurrence,
                expected_sort=result_sort,
                unsupported=unsupported,
            )
            if inner is not None
            else _java_atom(
                node, source, atoms, variables, occurrence, result_sort, unsupported
            )
        )
    if node_type in {"true", "true_literal"}:
        return z3.BoolVal(True)
    if node_type in {"false", "false_literal"}:
        return z3.BoolVal(False)
    if node_type in {
        "decimal_integer_literal",
        "hex_integer_literal",
        "octal_integer_literal",
        "binary_integer_literal",
    }:
        literal = _java_literal(node, source)
        return (
            literal
            if literal is not None
            else _java_atom(
                node, source, atoms, variables, occurrence, result_sort, unsupported
            )
        )
    if node_type == "null_literal":
        return z3.IntVal(0)
    if node_type == "identifier":
        return _java_variable(
            _node_text(node, source), variables, occurrence, result_sort
        )
    if node_type == "field_access":
        return _java_variable(
            _node_text(node, source), variables, occurrence, result_sort
        )
    if node_type in {"array_access", "method_invocation"}:
        return _java_atom(
            node, source, atoms, variables, occurrence, result_sort, unsupported
        )
    if node_type in {"binary_expression", "infix_expression"}:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        symbol = _java_operator(node, source)
        if left is None or right is None or symbol is None:
            return _java_atom(
                node, source, atoms, variables, occurrence, result_sort, unsupported
            )
        if symbol in {"&&", "||"}:
            operand_sort = "bool"
        elif symbol in {"<", "<=", ">", ">=", "+", "-", "*", "/", "%"}:
            operand_sort = "int"
        elif symbol in {"==", "!="}:
            operand_sort = (
                "bool"
                if "bool" in {_java_sort_hint(left, source), _java_sort_hint(right, source)}
                else "int"
            )
        else:
            operand_sort = result_sort
        left_value = _java_expression(
            left,
            source,
            atoms,
            variables,
            occurrence,
            expected_sort=operand_sort,
            unsupported=unsupported,
        )
        right_value = _java_expression(
            right,
            source,
            atoms,
            variables,
            occurrence,
            expected_sort=operand_sort,
            unsupported=unsupported,
        )
        operations = {
            "&&": lambda: z3.And(left_value, right_value),
            "||": lambda: z3.Or(left_value, right_value),
            "<": lambda: left_value < right_value,
            "<=": lambda: left_value <= right_value,
            ">": lambda: left_value > right_value,
            ">=": lambda: left_value >= right_value,
            "==": lambda: left_value == right_value,
            "!=": lambda: left_value != right_value,
            "+": lambda: left_value + right_value,
            "-": lambda: left_value - right_value,
            "*": lambda: left_value * right_value,
            "/": lambda: left_value / right_value,
            "%": lambda: z3.Mod(left_value, right_value),
        }
        operation = operations.get(symbol)
        if operation is not None:
            try:
                return operation()
            except (TypeError, z3.Z3Exception):
                return _java_atom(
                    node, source, atoms, variables, occurrence, result_sort, unsupported
                )
        return _java_atom(
            node, source, atoms, variables, occurrence, result_sort, unsupported
        )
    if node_type in {"unary_expression", "update_expression"}:
        operand = node.child_by_field_name("operand") or _named_child(node)
        symbol = _java_operator(node, source)
        if operand is not None and symbol is not None:
            operand_sort = "bool" if symbol == "!" else "int"
            value = _java_expression(
                operand,
                source,
                atoms,
                variables,
                occurrence,
                expected_sort=operand_sort,
                unsupported=unsupported,
            )
            if symbol == "!":
                return z3.Not(value)
            if symbol == "-":
                return -value
            if symbol == "+":
                return value
        return _java_atom(
            node, source, atoms, variables, occurrence, result_sort, unsupported
        )
    if node_type == "cast_expression":
        expression = node.child_by_field_name("expression") or _named_child(node)
        if expression is not None:
            return _java_expression(
                expression,
                source,
                atoms,
                variables,
                occurrence,
                expected_sort=result_sort,
                unsupported=unsupported,
            )
    return _java_atom(
        node, source, atoms, variables, occurrence, result_sort, unsupported
    )


def _path_constraint(
    document: dict[str, Any],
    path: dict[str, Any],
    source_root: Path | None,
    atoms: dict[str, Any],
    variables: dict[str, Any],
    condition_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in document.get("graph", {}).get("edges", []):
        outgoing.setdefault(edge["source"], []).append(edge)
    nodes = {node["id"]: node for node in document.get("graph", {}).get("nodes", [])}
    source_cache: dict[Path, dict[int, list[dict[str, Any]]]] = {}
    terms: list[Any] = []
    evidence: list[dict[str, Any]] = []
    occurrences: dict[str, int] = {}
    path_nodes = path.get("nodes", [])
    for source, target in zip(path_nodes, path_nodes[1:]):
        candidates = [edge for edge in outgoing.get(source, []) if edge.get("target") == target]
        if not candidates:
            evidence.append({"source": source, "target": target, "missing": True})
            continue
        edge = candidates[0]
        choice = _branch_choice(document, source, edge, outgoing[source])
        item = {"source": source, "target": target, "kind": edge.get("kind")}
        if choice is None:
            evidence.append(item)
            continue
        predicate, polarity = choice
        occurrence = occurrences.get(predicate, 0)
        occurrences[predicate] = occurrence + 1
        expression = (condition_overrides or {}).get(source) or _condition_for_node(
            source_root, nodes.get(source, {}), source_cache
        )
        if expression is not None and polarity in {"true", "false"}:
            condition_node = expression["node"]
            condition_source = expression["source"]
            unsupported: set[str] = set()
            condition = _java_expression(
                condition_node,
                condition_source,
                atoms,
                variables,
                occurrence,
                expected_sort="bool",
                unsupported=unsupported,
            )
            term = condition if polarity == "true" else z3.Not(condition)
            item.update(
                {
                    "predicate": _node_text(condition_node, condition_source),
                    "predicate_lines": [
                        expression["start_line"],
                        expression["end_line"],
                    ],
                    "polarity": polarity,
                    "constraint_type": "source_expression",
                    "precision": "abstracted" if unsupported else "exact_subset",
                    "unsupported_expressions": sorted(unsupported),
                    "source_file": expression["source_file"],
                }
            )
        else:
            key = f"{predicate}={polarity}"
            term = atoms.setdefault(key, z3.Bool("branch_" + hashlib.sha1(key.encode()).hexdigest()[:16]))
            item.update(
                {
                    "predicate": predicate,
                    "polarity": polarity,
                    "constraint_type": "abstract_branch",
                    "precision": "abstracted",
                }
            )
        terms.append(term)
        evidence.append(item)
    return (z3.And(*terms) if terms else z3.BoolVal(True)), evidence


def _solve_formula(
    formula: Any,
    atoms: dict[str, Any],
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    solver = z3.Solver()
    solver.add(formula)
    status = solver.check()
    model: dict[str, Any] = {}
    if status == z3.sat:
        assignment = solver.model()
        for text, variable in atoms.items():
            model[text] = z3.is_true(assignment.eval(variable, model_completion=True))
        for text, variable in (variables or {}).items():
            value = assignment.eval(variable, model_completion=True)
            if z3.is_true(value):
                model[text] = True
            elif z3.is_false(value):
                model[text] = False
            elif z3.is_int_value(value):
                model[text] = value.as_long()
            else:
                model[text] = str(value)
    return {
        "status": str(status),
        "sat": status == z3.sat,
        "model": model,
        "smt2": solver.sexpr(),
    }


def _constraint_quality(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    constraints = [item for item in evidence if item.get("constraint_type")]
    exact = [
        item
        for item in constraints
        if item.get("constraint_type") == "source_expression"
        and item.get("precision") == "exact_subset"
    ]
    abstracted = [item for item in constraints if item not in exact]
    return {
        "constraint_count": len(constraints),
        "exact_source_constraint_count": len(exact),
        "abstracted_constraint_count": len(abstracted),
        "sufficient_for_confirmation": bool(constraints) and not abstracted,
    }


def _boundary_constraint_quality(
    old_evidence: list[dict[str, Any]], new_evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    def fingerprint(item: dict[str, Any]) -> tuple[Any, ...] | None:
        if not item.get("constraint_type"):
            return None
        return (
            item.get("constraint_type"),
            item.get("predicate"),
            item.get("polarity"),
            item.get("precision"),
        )

    old_items = [fingerprint(item) for item in old_evidence]
    new_items = [fingerprint(item) for item in new_evidence]
    old_counts = Counter(item for item in old_items if item is not None)
    new_counts = Counter(item for item in new_items if item is not None)
    old_changed = list((old_counts - new_counts).elements())
    new_changed = list((new_counts - old_counts).elements())
    changed = [*old_changed, *new_changed]
    exact = bool(changed) and all(
        item[0] == "source_expression" and item[3] == "exact_subset"
        for item in changed
    )
    return {
        "sufficient_for_confirmation": exact,
        "old_changed_constraints": [list(item) for item in old_changed],
        "new_changed_constraints": [list(item) for item in new_changed],
        "common_constraint_count": sum((old_counts & new_counts).values()),
    }


def _extract_symbolic_outputs(
    lines: tuple[str, ...],
    atoms: dict[str, Any],
    variables: dict[str, Any],
) -> list[dict[str, Any]]:
    parser = _java_parser()
    if parser is None:
        return []
    body = "".join(lines)
    source = f"class __PatchLabel__ {{ Object __output__() {{\n{body}\n}} }}".encode()
    tree = parser.parse(source)
    outputs: list[dict[str, Any]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        expression = None
        channel = None
        if node.type == "return_statement":
            expression = _named_child(node)
            channel = "return"
        elif node.type == "assignment_expression":
            operator = node.child_by_field_name("operator")
            if operator is not None and _node_text(operator, source) == "=":
                expression = node.child_by_field_name("right")
                left = node.child_by_field_name("left")
                if left is not None:
                    channel = f"assign:{_node_text(left, source)}"
        elif node.type == "method_invocation":
            name = node.child_by_field_name("name")
            if name is not None and _node_text(name, source) in {
                "add",
                "append",
                "put",
                "remove",
                "set",
            }:
                channel = f"effect:{_node_text(node, source)}"
        if channel is not None:
            if expression is None:
                outputs.append(
                    {
                        "channel": channel,
                        "text": _node_text(node, source),
                        "expression": None,
                        "unsupported": ["side_effect_call"],
                    }
                )
            else:
                unsupported: set[str] = set()
                sort = _java_sort_hint(expression, source) or "int"
                symbolic = _java_expression(
                    expression,
                    source,
                    atoms,
                    variables,
                    0,
                    expected_sort=sort,
                    unsupported=unsupported,
                )
                outputs.append(
                    {
                        "channel": channel,
                        "text": _node_text(expression, source),
                        "expression": symbolic,
                        "unsupported": sorted(unsupported),
                    }
                )
        stack.extend(reversed(node.named_children))
    return outputs


def _variant_constraint(
    units: list[PatchUnit],
    reverted: int | None,
    *,
    document: dict[str, Any] | None = None,
    path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "constraints": "patched path with one patch unit restored to its old condition",
        "solver": {"status": "unavailable", "sat": None, "model": {}, "smt2": ""},
    }
    if document is None or path is None or reverted is None:
        return result
    structural = _path_is_sat(document, path)
    result["path_id"] = path.get("id")
    result["structural_path"] = structural
    if not structural.get("sat", False):
        result["solver"] = {"status": "unsat", "sat": False, "model": {}, "smt2": ""}
        return result
    unit = next((item for item in units if item.index == reverted), None)
    if unit is None:
        return result
    old_condition = _condition_from_lines(unit.old_lines)
    new_condition = _condition_from_lines(unit.new_lines)
    if new_condition is not None and old_condition is None:
        result["reason"] = "old_condition_not_recovered"
        return result
    nodes = {node["id"]: node for node in document.get("graph", {}).get("nodes", [])}
    overrides: dict[str, dict[str, Any]] = {}
    if old_condition is not None:
        for node_id in path.get("nodes", []):
            node = nodes.get(node_id)
            if node is not None and any(
                _overlaps(node, start, end) for start, end in unit.new_changed_ranges
            ):
                overrides[node_id] = old_condition
        if not overrides:
            result["reason"] = "reverted_condition_not_mapped_to_path"
            return result
    atoms: dict[str, Any] = {}
    variables: dict[str, Any] = {}
    formula, evidence = _path_constraint(
        document,
        path,
        _document_source_root(document),
        atoms,
        variables,
        condition_overrides=overrides,
    )
    solved = _solve_formula(formula, atoms, variables)
    result["solver"] = solved
    result["edge_constraints"] = evidence
    result["reverted_condition"] = (
        _node_text(old_condition["node"], old_condition["source"])
        if old_condition is not None
        else None
    )
    result["condition_override_nodes"] = sorted(overrides)
    return result


def _variant_outputs(
    units: list[PatchUnit],
    reverted: int | None,
    atoms: dict[str, Any],
    variables: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        output
        for unit in units
        for output in _extract_symbolic_outputs(
            unit.old_lines if unit.index == reverted else unit.new_lines,
            atoms,
            variables,
        )
    ]


def _compare_symbolic_outputs(
    full: list[dict[str, Any]], variant: list[dict[str, Any]]
) -> dict[str, Any]:
    if not full and not variant:
        return {"status": "unavailable", "equivalent": None, "reason": "no_symbolic_output"}
    full_channels = [item["channel"] for item in full]
    variant_channels = [item["channel"] for item in variant]
    if full_channels != variant_channels:
        return {
            "status": "different_output_channels",
            "equivalent": False,
            "full_channels": full_channels,
            "variant_channels": variant_channels,
        }
    unsupported = sorted(
        {
            value
            for item in [*full, *variant]
            for value in item["unsupported"]
        }
    )
    if unsupported or any(item["expression"] is None for item in [*full, *variant]):
        return {
            "status": "unavailable",
            "equivalent": None,
            "reason": "unsupported_symbolic_output",
            "unsupported_expressions": unsupported,
            "full": [{"channel": item["channel"], "text": item["text"]} for item in full],
            "variant": [
                {"channel": item["channel"], "text": item["text"]} for item in variant
            ],
        }
    differences = [
        old["expression"] != new["expression"]
        for old, new in zip(full, variant)
    ]
    solver = z3.Solver()
    solver.add(z3.Or(*differences) if differences else z3.BoolVal(False))
    status = solver.check()
    return {
        "status": str(status),
        "equivalent": status == z3.unsat,
        "full": [{"channel": item["channel"], "text": item["text"]} for item in full],
        "variant": [
            {"channel": item["channel"], "text": item["text"]} for item in variant
        ],
        "equivalence_query": "OR(symbolic_output_full != symbolic_output_variant)",
        "smt2": solver.sexpr(),
    }


def _root_cause_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    old_false = [
        item for item in _path_labels(buggy, "false")
        if _path_touches_any_unit(buggy, item["path"], units, patched=False)
    ]
    patched_paths = patched.get("path_set", [])
    new_false = [
        item for item in _path_labels(patched, "false")
        if _path_touches_any_unit(patched, item["path"], units, patched=True)
    ]
    new_signatures = {
        _path_signature(patched, item["path"]): item for item in new_false
    }
    for item in old_false:
        signature = _path_signature(buggy, item["path"])
        if signature in new_signatures:
            return {
                "label": True,
                "status": "confirmed",
                "stopped_after": "same_false_path",
                "evidence": {
                    "reason": "same_false_path_after_patch",
                    "old_path_id": item["path"]["id"],
                    "patched_path_id": new_signatures[signature]["path"]["id"],
                    "path_signature": list(signature),
                    "sat": _path_is_sat(patched, new_signatures[signature]["path"]),
                },
            }
    branch = _branch_evidence(buggy, patched, units)
    inconclusive_candidates: list[dict[str, Any]] = []
    if branch["new_branch"]:
        old_outside = {_outside_signature(buggy, item["path"], units) for item in old_false}
        for path in patched_paths:
            if _outside_signature(patched, path, units) in old_outside and _path_touches_any_unit(
                patched, path, units, patched=True
            ):
                reachability = _path_is_sat(patched, path)
                quality = _constraint_quality(reachability.get("edge_constraints", []))
                if not reachability.get("sat", False):
                    continue
                if not quality["sufficient_for_confirmation"]:
                    inconclusive_candidates.append(
                        {
                            "patched_path_id": path["id"],
                            "sat": reachability,
                            "constraint_quality": quality,
                        }
                    )
                    continue
                return {
                    "label": True,
                    "status": "confirmed",
                    "stopped_after": "old_false_path_reachable_after_new_branch",
                    "evidence": {
                        "branch": branch,
                        "patched_path_id": path["id"],
                        "sat": reachability,
                        "constraint_quality": quality,
                    },
                }
    if inconclusive_candidates:
        return {
            "label": None,
            "status": "inconclusive",
            "stopped_after": "only_abstracted_reachability_witnesses",
            "evidence": {
                "branch": branch,
                "candidates": inconclusive_candidates,
            },
        }
    return {
        "label": False,
        "status": "not_confirmed",
        "stopped_after": "no_false_path_witness",
        "evidence": {"branch": branch, "old_false_paths": len(old_false), "patched_false_paths": len(new_false)},
    }


def _boundary_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    branch = _branch_evidence(buggy, patched, units)
    old_false = _path_labels(buggy, "false")
    inconclusive_candidates: list[dict[str, Any]] = []
    for old_item in old_false:
        atoms: dict[str, Any] = {}
        variables: dict[str, Any] = {}
        old_constraint, old_evidence = _path_constraint(
            buggy,
            old_item["path"],
            _document_source_root(buggy),
            atoms,
            variables,
        )
        old_impacted = [
            unit.index for unit in units
            if _path_touches_unit(buggy, old_item["path"], unit, patched=False)
        ]
        if not old_impacted:
            continue
        patched_paths = [
            path for path in patched.get("path_set", [])
            if _path_touches_any_unit(patched, path, units, patched=True)
            and _outside_signature(patched, path, units)
            == _outside_signature(buggy, old_item["path"], units)
        ]
        for patched_path in patched_paths:
            candidate_atoms = dict(atoms)
            candidate_variables = dict(variables)
            new_constraint, new_evidence = _path_constraint(
                patched,
                patched_path,
                _document_source_root(patched),
                candidate_atoms,
                candidate_variables,
            )
            new_impacted = [
                unit.index for unit in units
                if _path_touches_unit(patched, patched_path, unit, patched=True)
            ] or list(old_impacted)
            old_only = z3.And(old_constraint, z3.Not(new_constraint))
            new_only = z3.And(z3.Not(old_constraint), new_constraint)
            formula = z3.Or(old_only, new_only)
            old_test_ids = _path_test_ids(
                buggy, str(old_item["path"]["id"]), "false"
            )
            patched_false_test_ids = _path_test_ids(
                patched, str(patched_path.get("id")), "false"
            )
            shared_failing_tests = sorted(old_test_ids & patched_false_test_ids)
            boundary = {
                "formula": "(C_old AND NOT C_new) OR (NOT C_old AND C_new)",
                "formula_symbols": "(C_old ∧ ¬C_new) ∨ (¬C_old ∧ C_new)",
                "constraint_source": "source_expressions_with_abstract_fallback",
                "old_constraints": old_evidence,
                "new_constraints": new_evidence,
                "old_constraint_quality": _constraint_quality(old_evidence),
                "new_constraint_quality": _constraint_quality(new_evidence),
                "changed_constraint_quality": _boundary_constraint_quality(
                    old_evidence, new_evidence
                ),
                "directions": {
                    "old_only": _solve_formula(
                        old_only, candidate_atoms, candidate_variables
                    ),
                    "new_only": _solve_formula(
                        new_only, candidate_atoms, candidate_variables
                    ),
                },
                "solver": _solve_formula(
                    formula, candidate_atoms, candidate_variables
                ),
            }
            precise = boundary["changed_constraint_quality"][
                "sufficient_for_confirmation"
            ]
            candidate = {
                "old_false_path_id": old_item["path"]["id"],
                "patched_path_id": patched_path.get("id"),
                "patch_units_on_old_false_path": old_impacted,
                "patch_units_on_patched_path": new_impacted,
                "shared_failing_tests": shared_failing_tests,
                "boundary": boundary,
            }
            if boundary["solver"]["sat"] and precise and shared_failing_tests:
                return {
                    "label": True,
                    "status": "confirmed",
                    "stopped_after": "v2_failing_test_remains_in_changed_boundary",
                    "evidence": {
                        "branch": branch,
                        "boundary_variant": "v2_buggy_input_not_fully_repaired",
                        "path_reaches_modified_unit": True,
                        **candidate,
                    },
                }
            if boundary["solver"]["sat"]:
                candidate["reason"] = (
                    "changed_boundary_without_cross_phase_failure_evidence"
                    if precise
                    else "satisfiable_only_with_abstracted_constraints"
                )
                inconclusive_candidates.append(candidate)

    if any(
        _path_touches_any_unit(buggy, item["path"], units, patched=False)
        for item in old_false
    ):
        for old_item in _path_labels(buggy, "true"):
            if not _path_touches_any_unit(
                buggy, old_item["path"], units, patched=False
            ):
                continue
            old_test_ids = _path_test_ids(
                buggy, str(old_item["path"]["id"]), "true"
            )
            for patched_item in _path_labels(patched, "false"):
                if (
                    not _path_touches_any_unit(
                        patched, patched_item["path"], units, patched=True
                    )
                    or _outside_signature(patched, patched_item["path"], units)
                    != _outside_signature(buggy, old_item["path"], units)
                ):
                    continue
                shared_regression_tests = sorted(
                    old_test_ids
                    & _path_test_ids(
                        patched, str(patched_item["path"]["id"]), "false"
                    )
                )
                if not shared_regression_tests:
                    continue
                atoms: dict[str, Any] = {}
                variables: dict[str, Any] = {}
                old_constraint, old_evidence = _path_constraint(
                    buggy,
                    old_item["path"],
                    _document_source_root(buggy),
                    atoms,
                    variables,
                )
                new_constraint, new_evidence = _path_constraint(
                    patched,
                    patched_item["path"],
                    _document_source_root(patched),
                    atoms,
                    variables,
                )
                formula = z3.Or(
                    z3.And(old_constraint, z3.Not(new_constraint)),
                    z3.And(z3.Not(old_constraint), new_constraint),
                )
                solved = _solve_formula(formula, atoms, variables)
                quality = _boundary_constraint_quality(old_evidence, new_evidence)
                if solved["sat"] and quality["sufficient_for_confirmation"]:
                    return {
                        "label": True,
                        "status": "confirmed",
                        "stopped_after": "v1_passing_test_regressed_in_changed_boundary",
                        "evidence": {
                            "branch": branch,
                            "boundary_variant": "v1_safe_input_incorrectly_changed",
                            "old_true_path_id": old_item["path"]["id"],
                            "patched_false_path_id": patched_item["path"]["id"],
                            "shared_regression_tests": shared_regression_tests,
                            "boundary": {
                                "formula": "(C_old AND NOT C_new) OR (NOT C_old AND C_new)",
                                "formula_symbols": "(C_old ∧ ¬C_new) ∨ (¬C_old ∧ C_new)",
                                "old_constraints": old_evidence,
                                "new_constraints": new_evidence,
                                "changed_constraint_quality": quality,
                                "solver": solved,
                            },
                        },
                    }
    if inconclusive_candidates:
        return {
            "label": None,
            "status": "inconclusive",
            "stopped_after": "boundary_change_without_semantic_failure_witness",
            "evidence": {
                "branch": branch,
                "old_false_paths": len(old_false),
                "candidates": inconclusive_candidates,
            },
        }
    return {
        "label": False,
        "status": "not_confirmed",
        "stopped_after": "no_modified_false_path_or_unsat_boundary",
        "evidence": {"branch": branch, "old_false_paths": len(old_false)},
    }


def _overrepair_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    if len(units) < 2:
        return {"label": False, "status": "not_confirmed", "stopped_after": "single_patch_unit", "evidence": {"unit_count": len(units)}}
    old_false = [item["path"] for item in _path_labels(buggy, "false")]
    if not old_false:
        return {"label": False, "status": "not_confirmed", "stopped_after": "no_bug_paths", "evidence": {"unit_count": len(units)}}
    unused: list[int] = []
    touched_by_unit: dict[str, list[str]] = {}
    for unit in units:
        path_ids = [
            str(path.get("id")) for path in old_false
            if _path_touches_unit(buggy, path, unit, patched=False)
        ]
        touched_by_unit[str(unit.index)] = path_ids
        touched = bool(path_ids)
        if not touched:
            unused.append(unit.index)
    if unused:
        return {
            "label": True,
            "status": "confirmed",
            "stopped_after": "patch_unit_not_on_false_path",
            "evidence": {
                "unused_patch_units": unused,
                "paths_by_patch_unit": touched_by_unit,
                "unit_count": len(units),
                "criterion": "patch_unit_not_on_any_original_false_path",
            },
        }
    attempts: list[dict[str, Any]] = []
    unavailable_output = False
    for unit in units:
        witness_paths = [
            path for path in old_false
            if _path_touches_unit(buggy, path, unit, patched=False)
        ]
        witness_path = witness_paths[0] if witness_paths else None
        patched_witness = next(
            (
                path
                for path in patched.get("path_set", [])
                if witness_path is not None
                and _path_touches_unit(patched, path, unit, patched=True)
                and _outside_signature(patched, path, units)
                == _outside_signature(buggy, witness_path, units)
            ),
            None,
        )
        reachability = _variant_constraint(
            units,
            reverted=unit.index,
            document=patched if patched_witness is not None else None,
            path=patched_witness,
        )
        attempt: dict[str, Any] = {
            "reverted_patch_unit": unit.index,
            "active_patch_units": [item.index for item in units if item.index != unit.index],
            "reset_from": "all_patch_units_applied",
            "false_path_ids": [path.get("id") for path in witness_paths],
            "patched_witness_path_id": (
                patched_witness.get("id") if patched_witness is not None else None
            ),
            "reachability": reachability,
        }
        if reachability["solver"]["sat"] is False:
            attempt["decision"] = "skip_unreachable"
            attempts.append(attempt)
            continue
        if reachability["solver"]["sat"] is None:
            unavailable_output = True
            attempt["decision"] = "inconclusive_reachability"
            attempts.append(attempt)
            continue
        output_atoms: dict[str, Any] = {}
        output_variables: dict[str, Any] = {}
        full_outputs = _variant_outputs(
            units, reverted=None, atoms=output_atoms, variables=output_variables
        )
        variant_outputs = _variant_outputs(
            units,
            reverted=unit.index,
            atoms=output_atoms,
            variables=output_variables,
        )
        comparison = _compare_symbolic_outputs(full_outputs, variant_outputs)
        attempt["symbolic_output"] = comparison
        if comparison["equivalent"] is True:
            attempt["decision"] = "redundant"
            attempts.append(attempt)
            return {
                "label": True,
                "status": "confirmed",
                "stopped_after": "reachable_rollback_with_equivalent_output",
                "evidence": {
                    "unit_count": len(units),
                    "confirmed_patch_unit": unit.index,
                    "attempts": attempts,
                },
            }
        if comparison["equivalent"] is None:
            unavailable_output = True
            attempt["decision"] = "inconclusive_output"
        else:
            attempt["decision"] = "output_changed"
        attempts.append(attempt)
    return {
        "label": None if unavailable_output else False,
        "status": "inconclusive" if unavailable_output else "not_confirmed",
        "stopped_after": "all_patch_units_checked",
        "evidence": {"unit_count": len(units), "attempts": attempts},
    }


def analyze_pair(
    key: str,
    patch_file: Path,
    buggy_file: Path,
    patched_file: Path,
    patched_manifest: Path | None = None,
    buggy_source_root: Path | None = None,
    patched_source_root: Path | None = None,
) -> dict[str, Any]:
    buggy = json.loads(buggy_file.read_text(encoding="utf-8"))
    patched = json.loads(patched_file.read_text(encoding="utf-8"))
    if buggy_source_root is not None and buggy_source_root.is_dir():
        buggy["_analysis_source_root"] = str(buggy_source_root)
    if patched_source_root is not None and patched_source_root.is_dir():
        patched["_analysis_source_root"] = str(patched_source_root)
    units = load_patch_units(patch_file, patched_manifest)
    return {
        "schema_version": "1.1",
        "example": key,
        "patch_file": str(patch_file),
        "patch_units": [asdict(unit) for unit in units],
        "tests": {
            "root_cause_not_fixed": _root_cause_test(buggy, patched, units),
            "boundary_incomplete": _boundary_test(buggy, patched, units),
            "overrepair": _overrepair_test(buggy, patched, units),
        },
        "solver": "z3" if z3 is not None else "structural-fallback",
        "constraint_model": {
            "scope": "source-level branch expressions mapped to CFG edges when matching worktree source is available",
            "fallback": "abstract CFG branch identities and edge polarities are evidence only and cannot by themselves confirm a label",
            "symbolic_values": "Boolean and integer identifiers, field reads, literals, comparisons, arithmetic, and Boolean operators are modeled by Z3",
            "symbolic_outputs": "return and assignment expressions are parsed from Java AST nodes and compared with Z3; unsupported side effects are inconclusive",
        },
    }


def _one_result(root: Path, key: str, phase: str) -> Path | None:
    candidates = sorted((root / key / phase / "runs").glob("*/experiment.json"))
    return candidates[-1] if candidates else None


def _result_source_root(
    state_dir: Path,
    dataset_version: str,
    key: str,
    phase: str,
    result_file: Path,
) -> Path | None:
    document = json.loads(result_file.read_text(encoding="utf-8"))
    source_dir = document.get("metadata", {}).get("dir.src.classes")
    fingerprint = document.get("run_fingerprint") or result_file.parent.name
    if not source_dir or not fingerprint:
        return None
    candidate = (
        state_dir
        / "worktrees-v2"
        / dataset_version
        / key
        / phase
        / str(fingerprint)
        / str(source_dir)
    )
    if candidate.is_dir():
        return candidate
    phase_root = state_dir / "worktrees-v2" / dataset_version / key / phase
    alternatives = sorted(
        (path / str(source_dir) for path in phase_root.iterdir()),
        key=lambda path: path.stat().st_mtime if path.is_dir() else -1,
        reverse=True,
    ) if phase_root.is_dir() else []
    return next((path for path in alternatives if path.is_dir()), None)


def _patched_source_copy(
    temporary_root: Path,
    buggy_source_root: Path,
    patch_file: Path,
    class_names: list[str],
) -> Path | None:
    patched_root = temporary_root / hashlib.sha1(str(patch_file).encode()).hexdigest()
    copied: list[Path] = []
    for class_name in class_names:
        source = buggy_source_root / Path(*class_name.split("$", 1)[0].split(".")).with_suffix(
            ".java"
        )
        if not source.is_file():
            continue
        destination = patched_root / source.relative_to(buggy_source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    if not copied:
        return None
    apply_context_patch(patch_file, copied)
    return patched_root


def _analysis_report_cell(test_name: str, result: dict[str, Any]) -> str:
    status = str(result.get("status", "unknown"))
    stopped = str(result.get("stopped_after", ""))
    evidence = result.get("evidence", {})
    detail = stopped
    if status == "confirmed" and test_name == "root_cause_not_fixed":
        detail = str(evidence.get("reason") or stopped)
        old_path = evidence.get("old_path_id")
        patched_path = evidence.get("patched_path_id")
        if old_path or patched_path:
            detail += f" ({old_path or '?'} -> {patched_path or '?'})"
    elif status == "confirmed" and test_name == "boundary_incomplete":
        boundary = evidence.get("boundary", {})
        model = boundary.get("solver", {}).get("model", {})
        if evidence.get("boundary_variant") == "v1_safe_input_incorrectly_changed":
            detail = (
                f"v1 regression: {evidence.get('old_true_path_id', '?')} -> "
                f"{evidence.get('patched_false_path_id', '?')}; tests "
                f"{evidence.get('shared_regression_tests', [])}; model "
                f"{json.dumps(model, ensure_ascii=False, sort_keys=True)}"
            )
        else:
            detail = (
                f"v2 still failing: {evidence.get('old_false_path_id', '?')} -> "
                f"{evidence.get('patched_path_id', '?')}; tests "
                f"{evidence.get('shared_failing_tests', [])}; model "
                f"{json.dumps(model, ensure_ascii=False, sort_keys=True)}"
            )
    elif status == "confirmed" and test_name == "overrepair":
        unit = evidence.get("confirmed_patch_unit")
        unused = evidence.get("unused_patch_units")
        detail = f"redundant unit {unit}" if unit is not None else f"units outside false paths {unused}"
    return f"**{status}** — {detail}".replace("|", "\\|")


def analyze_results(
    results_dir: Path,
    dataset_dir: Path,
    output_file: Path,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="patch-label-analysis-") as temporary:
        temporary_root = Path(temporary)
        for dataset_root in sorted(results_dir.glob("D4JV*")):
            for example_root in sorted(dataset_root.iterdir() if dataset_root.is_dir() else []):
                if not example_root.is_dir():
                    continue
                key = example_root.name
                buggy_file = _one_result(dataset_root, key, "buggy")
                patched_file = _one_result(dataset_root, key, "patched")
                patch_file = dataset_dir / dataset_root.name / key / "thinkrepair.patch"
                if not buggy_file or not patched_file or not patch_file.is_file():
                    continue
                manifest = patched_file.parent / "patch-application.json"
                buggy_source_root = (
                    _result_source_root(
                        state_dir, dataset_root.name, key, "buggy", buggy_file
                    )
                    if state_dir is not None
                    else None
                )
                patched_source_root = (
                    _result_source_root(
                        state_dir, dataset_root.name, key, "patched", patched_file
                    )
                    if state_dir is not None
                    else None
                )
                if patched_source_root is None and buggy_source_root is not None:
                    document = json.loads(patched_file.read_text(encoding="utf-8"))
                    patched_source_root = _patched_source_copy(
                        temporary_root,
                        buggy_source_root,
                        patch_file,
                        list(document.get("metadata", {}).get("classes.modified", [])),
                    )
                pairs.append(
                    analyze_pair(
                        key,
                        patch_file,
                        buggy_file,
                        patched_file,
                        manifest,
                        buggy_source_root,
                        patched_source_root,
                    )
                )
    document = {"schema_version": "1.1", "count": len(pairs), "results": pairs}
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    report_lines = [
        "# Patch-label static patch analysis",
        "",
        "Labels are conservative: `confirmed` means the stated evidence was found; `not_confirmed` means the criterion was not found; `inconclusive` means the available CFG/path data cannot prove the criterion.",
        "",
        "| Example | Root cause not fixed | Boundary incomplete | Overrepair |",
        "| --- | --- | --- | --- |",
    ]
    for result in pairs:
        tests = result["tests"]
        report_lines.append(
            f"| {result['example']} | "
            f"{_analysis_report_cell('root_cause_not_fixed', tests['root_cause_not_fixed'])} | "
            f"{_analysis_report_cell('boundary_incomplete', tests['boundary_incomplete'])} | "
            f"{_analysis_report_cell('overrepair', tests['overrepair'])} |"
        )
    report_lines.extend(
        [
            "",
            "The table shows each early-stop decision and its compact witness. Complete predicates, path constraints, SMT-LIB formulas, models, and rollback attempts are retained in the adjacent JSON file.",
            "",
        ]
    )
    output_file.with_suffix(".md").write_text("\n".join(report_lines), encoding="utf-8")
    return document
