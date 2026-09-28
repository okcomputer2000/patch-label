from __future__ import annotations

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
        ))
    return units


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
            _overlaps(
                node,
                unit.applied_line or unit.old_start,
                (unit.applied_line or unit.old_start) + max(unit.old_count, 1) - 1,
            )
            for unit in units
        )
        for node in path_nodes
    )


def _path_is_sat(document: dict[str, Any], path: dict[str, Any]) -> dict[str, Any]:
    edges = {(edge["source"], edge["target"]) for edge in document["graph"].get("edges", [])}
    path_edges = list(zip(path.get("nodes", []), path.get("nodes", [])[1:]))
    missing = [edge for edge in path_edges if edge not in edges]
    if missing:
        return {"sat": False, "solver": "structural", "missing_edges": missing}
    if z3 is None:
        return {"sat": True, "solver": "structural", "constraints": len(path_edges)}
    solver = z3.Solver()
    variables = {
        edge: z3.Bool(f"e_{index}")
        for index, edge in enumerate(sorted(edges))
    }
    solver.add(*(variables[edge] for edge in path_edges))
    return {
        "sat": solver.check() == z3.sat,
        "solver": "z3",
        "constraints": len(path_edges),
    }


def _modified_nodes(document: dict[str, Any], unit: PatchUnit, *, patched: bool) -> list[dict[str, Any]]:
    start = unit.applied_line or (unit.new_start if patched else unit.old_start)
    count = unit.new_count if patched else unit.old_count
    end = start + max(count, 1) - 1
    source_file = unit.source_file or ""
    class_name = Path(source_file.replace("\\", "/")).stem
    return [
        node for node in document["graph"]["nodes"]
        if (not class_name or str(node.get("class_name", "")).rsplit(".", 1)[-1] == class_name)
        and _overlaps(node, start, end)
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


def _root_cause_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    old_false = [item for item in _path_labels(buggy, "false") if _path_touches_units(buggy, item["path"], units)]
    new_false = [item for item in _path_labels(patched, "false") if _path_touches_units(patched, item["path"], units)]
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
    if not branch["new_branch"]:
        return {"label": False, "status": "not_confirmed", "stopped_after": "no_new_branch", "evidence": {"branch": branch}}
    old_false = [item for item in _path_labels(buggy, "false") if _path_touches_units(buggy, item["path"], units)]
    patched_false = [item for item in _path_labels(patched, "false") if _path_touches_units(patched, item["path"], units)]
    old_outside = {_outside_signature(buggy, item["path"], units) for item in old_false}
    for item in patched_false:
        if _outside_signature(patched, item["path"], units) in old_outside:
            return {
                "label": True,
                "status": "confirmed",
                "stopped_after": "boundary_false_witness",
                "evidence": {
                    "branch": branch,
                    "patched_false_path_id": item["path"]["id"],
                    "sat": _path_is_sat(patched, item["path"]),
                    "boundary_formula": "(cond_orig AND NOT cond_patch) OR (NOT cond_orig AND cond_patch)",
                },
            }
    return {
        "label": False,
        "status": "not_confirmed",
        "stopped_after": "no_boundary_false_witness",
        "evidence": {"branch": branch, "old_false_paths": len(old_false), "patched_false_paths": len(patched_false)},
    }


def _overrepair_test(buggy: dict[str, Any], patched: dict[str, Any], units: list[PatchUnit]) -> dict[str, Any]:
    if len(units) < 2:
        return {"label": False, "status": "not_confirmed", "stopped_after": "single_patch_unit", "evidence": {"unit_count": len(units)}}
    old_false = [item["path"] for item in _path_labels(buggy, "false")]
    if not old_false:
        return {"label": False, "status": "not_confirmed", "stopped_after": "no_bug_paths", "evidence": {"unit_count": len(units)}}
    unused: list[int] = []
    for unit in units:
        touched = any(
            any(
                _overlaps(
                    node,
                    unit.applied_line or unit.old_start,
                    (unit.applied_line or unit.old_start) + max(unit.old_count, 1) - 1,
                )
                for node in buggy["graph"]["nodes"]
                if node["id"] in path.get("nodes", [])
            )
            for path in old_false
        )
        if not touched:
            unused.append(unit.index)
    if unused:
        return {
            "label": True,
            "status": "confirmed",
            "stopped_after": "patch_unit_not_on_false_path",
            "evidence": {
                "unused_patch_units": unused,
                "unit_count": len(units),
                "output_equivalence": "structurally_unchanged_on_bug_paths",
            },
        }
    return {
        "label": None,
        "status": "inconclusive",
        "stopped_after": "requires_symbolic_output_equivalence",
        "evidence": {
            "unit_count": len(units),
            "output_equivalence": "not_proved",
            "note": "Every patch unit intersects a false path; removing units requires symbolic output modelling.",
        },
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
            "scope": "intraprocedural CFG edge conjunctions",
            "source_predicates": "not stored in schema 2.0 results",
            "symbolic_outputs": "not stored in schema 2.0 results",
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
