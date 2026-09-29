from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .patching import Hunk, parse_unified_patch

try:
    import z3
except ImportError:  # pragma: no cover - exercised when the optional solver is absent
    z3 = None


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
        new_line = new_start
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


def _path_is_sat(document: dict[str, Any], path: dict[str, Any]) -> dict[str, Any]:
    edges = {(edge["source"], edge["target"]) for edge in document["graph"].get("edges", [])}
    path_edges = list(zip(path.get("nodes", []), path.get("nodes", [])[1:]))
    missing = [edge for edge in path_edges if edge not in edges]
    if missing:
        return {"sat": False, "solver": "structural", "missing_edges": missing}
    if z3 is None:
        return {"sat": True, "solver": "structural", "constraints": len(path_edges)}
    atoms: dict[str, Any] = {}
    formula, evidence = _abstract_path_constraint(document, path, atoms)
    solved = _solve_formula(formula, atoms)
    solved.update({"solver": "z3-abstract-branches", "edge_constraints": evidence})
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


def _abstract_path_constraint(
    document: dict[str, Any], path: dict[str, Any], atoms: dict[str, Any]
) -> tuple[Any, list[dict[str, Any]]]:
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in document.get("graph", {}).get("edges", []):
        outgoing.setdefault(edge["source"], []).append(edge)
    terms: list[Any] = []
    evidence: list[dict[str, Any]] = []
    nodes = path.get("nodes", [])
    for source, target in zip(nodes, nodes[1:]):
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
        key = f"{predicate}={polarity}"
        atom = atoms.setdefault(key, z3.Bool("branch_" + hashlib.sha1(key.encode()).hexdigest()[:16]))
        terms.append(atom)
        item.update({"predicate": predicate, "polarity": polarity})
        evidence.append(item)
    return (z3.And(*terms) if terms else z3.BoolVal(True)), evidence


def _solve_formula(formula: Any, atoms: dict[str, Any]) -> dict[str, Any]:
    solver = z3.Solver()
    solver.add(formula)
    status = solver.check()
    model: dict[str, bool] = {}
    if status == z3.sat:
        assignment = solver.model()
        for text, variable in atoms.items():
            model[text] = z3.is_true(assignment.eval(variable, model_completion=True))
    return {
        "status": str(status),
        "sat": status == z3.sat,
        "model": model,
        "smt2": solver.sexpr(),
    }


def _normalize_source(source: str) -> str:
    return " ".join(source.strip().split())


def _extract_symbolic_outputs(lines: tuple[str, ...]) -> tuple[str, ...]:
    source = _normalize_source("".join(lines))
    outputs: list[str] = []
    outputs.extend(f"return {value.strip()}" for value in re.findall(r"\breturn\s+([^;]+);", source))
    outputs.extend(
        f"{left.strip()} = {right.strip()}"
        for left, right in re.findall(
            r"(?<![=!<>])\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*([^;]+);",
            source,
        )
    )
    outputs.extend(
        statement.strip()
        for statement in re.findall(r"\b[A-Za-z_$][\w$\.]*\.(?:add|append|put|remove|set)\s*\([^;]*\);", source)
    )
    return tuple(outputs)


def _variant_constraint(
    units: list[PatchUnit],
    reverted: int | None,
    *,
    document: dict[str, Any] | None = None,
    path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "constraints": "abstract CFG branch identities and edge polarities",
        "solver": {"status": "sat", "sat": True, "model": {}, "smt2": ""},
    }
    if document is not None and path is not None:
        structural = _path_is_sat(document, path)
        result["path_id"] = path.get("id")
        result["structural_path"] = structural
        if not structural.get("sat", False):
            result["solver"]["sat"] = False
            result["solver"]["status"] = "unsat"
    return result


def _variant_outputs(units: list[PatchUnit], reverted: int | None) -> tuple[str, ...]:
    return tuple(
        output
        for unit in units
        for output in _extract_symbolic_outputs(
            unit.old_lines if unit.index == reverted else unit.new_lines
        )
    )


def _compare_symbolic_outputs(full: tuple[str, ...], variant: tuple[str, ...]) -> dict[str, Any]:
    if not full and not variant:
        return {"status": "unavailable", "equivalent": None, "reason": "no_symbolic_output"}
    full_value = z3.StringVal("\n".join(full))
    variant_value = z3.StringVal("\n".join(variant))
    solver = z3.Solver()
    solver.add(full_value != variant_value)
    status = solver.check()
    return {
        "status": str(status),
        "equivalent": status == z3.unsat,
        "full": list(full),
        "variant": list(variant),
        "equivalence_query": "symbolic_output_full != symbolic_output_variant",
    }


def _root_cause_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    old_false = [
        item for item in _path_labels(buggy, "false")
        if _path_touches_any_unit(buggy, item["path"], units, patched=False)
    ]
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
    if branch["new_branch"]:
        old_outside = {_outside_signature(buggy, item["path"], units) for item in old_false}
        for item in new_false:
            if _outside_signature(patched, item["path"], units) in old_outside:
                return {
                    "label": True,
                    "status": "confirmed",
                    "stopped_after": "old_false_path_reachable_after_new_branch",
                    "evidence": {
                        "branch": branch,
                        "patched_false_path_id": item["path"]["id"],
                        "sat": _path_is_sat(patched, item["path"]),
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
    for old_item in old_false:
        atoms: dict[str, Any] = {}
        old_constraint, old_evidence = _abstract_path_constraint(buggy, old_item["path"], atoms)
        old_impacted = [
            unit.index for unit in units
            if _path_touches_unit(buggy, old_item["path"], unit, patched=False)
        ]
        if not old_impacted:
            continue
        patched_path = next(
            (
                item["path"] for item in _path_labels(patched, "false")
                if _path_touches_any_unit(patched, item["path"], units, patched=True)
                and _outside_signature(patched, item["path"], units)
                == _outside_signature(buggy, old_item["path"], units)
            ),
            None,
        )
        if patched_path is None:
            patched_path = old_item["path"]
        new_constraint, new_evidence = _abstract_path_constraint(patched, patched_path, atoms)
        new_impacted = [
            unit.index for unit in units
            if _path_touches_unit(patched, patched_path, unit, patched=True)
        ] or list(old_impacted)
        formula = z3.Or(
            z3.And(old_constraint, z3.Not(new_constraint)),
            z3.And(z3.Not(old_constraint), new_constraint),
        )
        boundary = {
            "formula": "(C_old AND NOT C_new) OR (NOT C_old AND C_new)",
            "formula_symbols": "(C_old ∧ ¬C_new) ∨ (¬C_old ∧ C_new)",
            "constraint_source": "abstract_cfg_branch_polarity",
            "old_constraints": old_evidence,
            "new_constraints": new_evidence,
            "solver": _solve_formula(formula, atoms),
        }
        if boundary["solver"]["sat"]:
            return {
                "label": True,
                "status": "confirmed",
                "stopped_after": "old_false_path_and_sat_boundary_formula",
                "evidence": {
                    "branch": branch,
                    "old_false_path_id": old_item["path"]["id"],
                    "patched_false_path_id": patched_path.get("id"),
                    "patch_units_on_old_false_path": old_impacted,
                    "patch_units_on_patched_path": new_impacted,
                    "path_reaches_modified_unit": True,
                    "boundary": boundary,
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
    full_outputs = _variant_outputs(units, reverted=None)
    attempts: list[dict[str, Any]] = []
    unavailable_output = False
    for unit in units:
        witness_paths = [
            path for path in old_false
            if _path_touches_unit(buggy, path, unit, patched=False)
        ]
        witness_path = witness_paths[0] if witness_paths else None
        reachability = _variant_constraint(
            units,
            reverted=unit.index,
            document=buggy if witness_path is not None else None,
            path=witness_path,
        )
        attempt: dict[str, Any] = {
            "reverted_patch_unit": unit.index,
            "active_patch_units": [item.index for item in units if item.index != unit.index],
            "reset_from": "all_patch_units_applied",
            "false_path_ids": [path.get("id") for path in witness_paths],
            "reachability": reachability,
        }
        if not reachability["solver"]["sat"]:
            attempt["decision"] = "skip_unreachable"
            attempts.append(attempt)
            continue
        comparison = _compare_symbolic_outputs(
            full_outputs, _variant_outputs(units, reverted=unit.index)
        )
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
) -> dict[str, Any]:
    buggy = json.loads(buggy_file.read_text(encoding="utf-8"))
    patched = json.loads(patched_file.read_text(encoding="utf-8"))
    units = load_patch_units(patch_file, patched_manifest)
    return {
        "schema_version": "1.0",
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
            "scope": "abstract CFG branch identities and edge polarities over complete paths",
            "concrete_values": "not modeled; predicates intentionally do not encode Java input values",
            "symbolic_outputs": "compared as conservative patch-unit statement signatures",
        },
    }


def _one_result(root: Path, key: str, phase: str) -> Path | None:
    candidates = sorted((root / key / phase / "runs").glob("*/experiment.json"))
    return candidates[-1] if candidates else None


def analyze_results(results_dir: Path, dataset_dir: Path, output_file: Path) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
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
            pairs.append(analyze_pair(key, patch_file, buggy_file, patched_file, manifest))
    document = {"schema_version": "1.0", "count": len(pairs), "results": pairs}
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
            f"| {result['example']} | {tests['root_cause_not_fixed']['status']} | "
            f"{tests['boundary_incomplete']['status']} | {tests['overrepair']['status']} |"
        )
    report_lines.extend(["", "Evidence is retained in the adjacent JSON file.", ""])
    output_file.with_suffix(".md").write_text("\n".join(report_lines), encoding="utf-8")
    return document
