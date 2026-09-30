import json
from pathlib import Path

from patch_label.analyzer import (
    _path_constraint,
    _path_is_sat,
    analyze_pair,
    load_patch_units,
)


def _document(label: str, *, changed: bool = False) -> dict:
    method = "sample.Foo#run()V"
    nodes = [
        {"id": f"{method}:ENTRY", "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
        {"id": f"{method}:B0", "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 10, "end_line": 10},
        {"id": f"{method}:EXIT", "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
    ]
    path = {"id": "path:one", "method_id": method, "nodes": [node["id"] for node in nodes], "kind": "bounded-entry-exit"}
    return {
        "graph": {
            "nodes": nodes,
            "edges": [
                {"source": nodes[0]["id"], "target": nodes[1]["id"]},
                {"source": nodes[1]["id"], "target": nodes[2]["id"]},
            ],
        },
        "path_set": [path],
        "labels": [{"path_id": path["id"], "label": label}],
    }


def test_analyze_pair_confirms_same_false_path_and_keeps_evidence(tmp_path: Path) -> None:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n@@ -10,1 +10,1 @@\n-old();\n+new();\n",
        encoding="utf-8",
    )
    buggy = tmp_path / "buggy.json"
    patched = tmp_path / "patched.json"
    buggy.write_text(json.dumps(_document("false")), encoding="utf-8")
    patched.write_text(json.dumps(_document("false")), encoding="utf-8")
    result = analyze_pair("Foo-1", patch, buggy, patched)
    test = result["tests"]["root_cause_not_fixed"]
    assert test["label"] is True
    assert test["status"] == "confirmed"
    assert test["stopped_after"] == "same_false_path"
    assert test["evidence"]["sat"]["sat"] is True


def test_patch_unit_uses_applied_source_coordinates_for_both_versions(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n"
        "@@ -5,2 +5,2 @@\n-old();\n+new();\n context();\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "patch-application.json"
    manifest.write_text(
        json.dumps({"hunks": [{"hunk": 1, "line": 200, "source_file": "Foo.java"}]}),
        encoding="utf-8",
    )
    unit = load_patch_units(patch, manifest)[0]
    assert unit.old_changed_ranges == ((200, 200),)
    assert unit.new_changed_ranges == ((200, 200),)


def test_analyze_pair_reports_inconclusive_multi_unit_overrepair(tmp_path: Path) -> None:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n"
        "@@ -10,1 +10,1 @@\n-old();\n+new();\n"
        "@@ -10,1 +10,1 @@\n-old2();\n+new2();\n",
        encoding="utf-8",
    )
    buggy = tmp_path / "buggy.json"
    patched = tmp_path / "patched.json"
    buggy.write_text(json.dumps(_document("false")), encoding="utf-8")
    patched.write_text(json.dumps(_document("true")), encoding="utf-8")
    result = analyze_pair("Foo-1", patch, buggy, patched)
    test = result["tests"]["overrepair"]
    assert test["status"] == "inconclusive"
    assert test["stopped_after"] == "all_patch_units_checked"


def test_path_sat_uses_abstract_branch_polarity() -> None:
    method = "sample.Foo#run()V"
    entry, branch, taken, exit_node = [f"{method}:{name}" for name in ("ENTRY", "B0", "B1", "EXIT")]
    document = {
        "graph": {
            "nodes": [
                {"id": entry, "method_id": method, "virtual": True, "start_line": -1, "end_line": -1},
                {"id": branch, "method_id": method, "virtual": False, "start_line": 10, "end_line": 10},
                {"id": taken, "method_id": method, "virtual": False, "start_line": 11, "end_line": 11},
                {"id": exit_node, "method_id": method, "virtual": True, "start_line": -1, "end_line": -1},
            ],
            "edges": [
                {"source": entry, "target": branch, "kind": "entry"},
                {"source": branch, "target": taken, "kind": "jump"},
                {"source": branch, "target": exit_node, "kind": "fallthrough"},
                {"source": taken, "target": exit_node, "kind": "exit"},
            ],
        }
    }
    result = _path_is_sat(document, {"id": "path:branch", "nodes": [entry, branch, taken, exit_node]})
    assert result["sat"] is True
    assert result["solver"] == "z3-abstract-branches"
    assert result["edge_constraints"][1]["polarity"] == "true"


def test_source_condition_enrichment_models_integer_comparison(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "sample"
    source_root.mkdir(parents=True)
    source_file = source_root / "Foo.java"
    source_file.write_text("if (a < 5) {\n}\n", encoding="utf-8")
    method = "sample.Foo#run()V"
    entry, branch, taken, exit_node = [f"{method}:{name}" for name in ("ENTRY", "B0", "B1", "EXIT")]
    document = {
        "metadata": {"dir.src.classes": "src", "cp.test": str(tmp_path / "build")},
        "graph": {
            "nodes": [
                {"id": entry, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
                {"id": branch, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 1, "end_line": 1},
                {"id": taken, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 2, "end_line": 2},
                {"id": exit_node, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
            ],
            "edges": [
                {"source": entry, "target": branch, "kind": "entry"},
                {"source": branch, "target": taken, "kind": "jump"},
                {"source": branch, "target": exit_node, "kind": "fallthrough"},
                {"source": taken, "target": exit_node, "kind": "exit"},
            ],
        },
    }
    variables = {}
    formula, evidence = _path_constraint(
        document,
        {"nodes": [entry, branch, taken, exit_node]},
        source_root.parent,
        {},
        variables,
    )
    assert "5" in str(formula)
    assert "a:int@0" in variables
    assert evidence[1]["constraint_type"] == "source_expression"
    assert evidence[1]["predicate"] == "(a < 5)"
    json.dumps(evidence)


def test_source_condition_models_boolean_composition(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "sample"
    source_root.mkdir(parents=True)
    (source_root / "Foo.java").write_text(
        "if (enabled && amount < 5) {\n}\n", encoding="utf-8"
    )
    method = "sample.Foo#run()V"
    entry, branch, taken, exit_node = [
        f"{method}:{name}" for name in ("ENTRY", "B0", "B1", "EXIT")
    ]
    document = {
        "graph": {
            "nodes": [
                {"id": entry, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
                {"id": branch, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 1, "end_line": 1},
                {"id": taken, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 2, "end_line": 2},
                {"id": exit_node, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
            ],
            "edges": [
                {"source": entry, "target": branch, "kind": "entry"},
                {"source": branch, "target": taken, "kind": "jump"},
                {"source": branch, "target": exit_node, "kind": "fallthrough"},
                {"source": taken, "target": exit_node, "kind": "exit"},
            ],
        }
    }
    variables = {}
    formula, evidence = _path_constraint(
        document,
        {"nodes": [entry, branch, taken, exit_node]},
        source_root.parent,
        {},
        variables,
    )
    assert "enabled:bool@0" in variables
    assert "amount:int@0" in variables
    assert "And" in str(formula)
    assert evidence[1]["precision"] == "exact_subset"


def _analyze_boundary_case(
    tmp_path: Path,
    *,
    old_condition: str,
    new_condition: str,
    old_label: str,
    new_label: str,
    old_test_id: str | None,
    new_test_id: str | None,
    branch_kind: str = "jump",
    old_extra_labels: tuple[tuple[str, str | None], ...] = (),
    new_extra_labels: tuple[tuple[str, str | None], ...] = (),
) -> dict:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n"
        f"@@ -1,1 +1,1 @@\n-if ({old_condition}) {{\n+if ({new_condition}) {{\n",
        encoding="utf-8",
    )
    buggy_root = tmp_path / "buggy-src"
    patched_root = tmp_path / "patched-src"
    for root, condition in (
        (buggy_root, old_condition),
        (patched_root, new_condition),
    ):
        source = root / "sample" / "Foo.java"
        source.parent.mkdir(parents=True)
        source.write_text(f"if ({condition}) {{\n}}\n", encoding="utf-8")

    method = "sample.Foo#run()V"
    entry, branch, taken, exit_node = [
        f"{method}:{name}" for name in ("ENTRY", "B0", "B1", "EXIT")
    ]
    nodes = [
        {"id": entry, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
        {"id": branch, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 1, "end_line": 1},
        {"id": taken, "method_id": method, "class_name": "sample.Foo", "virtual": False, "start_line": 2, "end_line": 2},
        {"id": exit_node, "method_id": method, "class_name": "sample.Foo", "virtual": True, "start_line": -1, "end_line": -1},
    ]
    path_nodes = (
        [entry, branch, taken, exit_node]
        if branch_kind == "jump"
        else [entry, branch, exit_node]
    )
    path = {"id": "path:branch", "method_id": method, "nodes": path_nodes}
    graph = {
        "nodes": nodes,
        "edges": [
            {"source": entry, "target": branch, "kind": "entry"},
            {"source": branch, "target": taken, "kind": "jump"},
            {"source": branch, "target": exit_node, "kind": "fallthrough"},
            {"source": taken, "target": exit_node, "kind": "exit"},
        ],
    }
    buggy_doc = {
        "graph": graph,
        "path_set": [path],
        "labels": [
            {"path_id": path["id"], "label": old_label, "test_id": old_test_id},
            *(
                {"path_id": path["id"], "label": label, "test_id": test_id}
                for label, test_id in old_extra_labels
            ),
        ],
    }
    patched_doc = {
        "graph": graph,
        "path_set": [path],
        "labels": [
            {"path_id": path["id"], "label": new_label, "test_id": new_test_id},
            *(
                {"path_id": path["id"], "label": label, "test_id": test_id}
                for label, test_id in new_extra_labels
            ),
        ],
    }
    buggy = tmp_path / "buggy.json"
    patched = tmp_path / "patched.json"
    buggy.write_text(json.dumps(buggy_doc), encoding="utf-8")
    patched.write_text(json.dumps(patched_doc), encoding="utf-8")

    return analyze_pair(
        "Foo-1",
        patch,
        buggy,
        patched,
        buggy_source_root=buggy_root,
        patched_source_root=patched_root,
    )


def test_boundary_label_uses_exact_source_constraints(tmp_path: Path) -> None:
    result = _analyze_boundary_case(
        tmp_path,
        old_condition="a < 5",
        new_condition="a <= 5",
        old_label="false",
        new_label="false",
        old_test_id="sample.FooTest::boundary",
        new_test_id="sample.FooTest::boundary",
    )
    boundary = result["tests"]["boundary_incomplete"]
    assert boundary["status"] == "confirmed"
    assert boundary["stopped_after"] == "v2_failing_test_remains_in_changed_boundary"
    assert boundary["evidence"]["shared_failing_tests"] == [
        "sample.FooTest::boundary"
    ]
    assert boundary["evidence"]["boundary"]["solver"]["sat"] is True
    assert boundary["evidence"]["boundary"]["solver"]["model"]["a:int@0"] == 5


def test_boundary_change_without_failure_evidence_is_inconclusive(
    tmp_path: Path,
) -> None:
    result = _analyze_boundary_case(
        tmp_path,
        old_condition="dataset != null",
        new_condition="dataset == null",
        old_label="false",
        new_label="untested",
        old_test_id="sample.FooTest::bug",
        new_test_id=None,
        branch_kind="fallthrough",
    )
    boundary = result["tests"]["boundary_incomplete"]
    assert boundary["label"] is None
    assert boundary["status"] == "inconclusive"
    assert (
        boundary["stopped_after"]
        == "boundary_change_without_semantic_failure_witness"
    )
    candidate = boundary["evidence"]["candidates"][0]
    assert candidate["boundary"]["solver"]["sat"] is True
    assert candidate["reason"] == "changed_boundary_without_cross_phase_failure_evidence"


def test_boundary_confirms_passing_test_regression(tmp_path: Path) -> None:
    result = _analyze_boundary_case(
        tmp_path,
        old_condition="a < 5",
        new_condition="a <= 5",
        old_label="false",
        new_label="false",
        old_test_id="sample.FooTest::originalBug",
        new_test_id="sample.FooTest::regression",
        old_extra_labels=(("true", "sample.FooTest::regression"),),
    )
    boundary = result["tests"]["boundary_incomplete"]
    assert boundary["label"] is True
    assert boundary["status"] == "confirmed"
    assert boundary["stopped_after"] == "v1_passing_test_regressed_in_changed_boundary"
    assert boundary["evidence"]["shared_regression_tests"] == [
        "sample.FooTest::regression"
    ]


def test_overrepair_confirms_symbolically_equivalent_rollback(tmp_path: Path) -> None:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n"
        "@@ -10,1 +10,1 @@\n-return a + 0;\n+return a;\n"
        "@@ -10,1 +10,1 @@\n-old2();\n+new2();\n",
        encoding="utf-8",
    )
    buggy = tmp_path / "buggy.json"
    patched = tmp_path / "patched.json"
    buggy.write_text(json.dumps(_document("false")), encoding="utf-8")
    patched.write_text(json.dumps(_document("true")), encoding="utf-8")
    result = analyze_pair("Foo-1", patch, buggy, patched)
    overrepair = result["tests"]["overrepair"]
    assert overrepair["status"] == "confirmed"
    assert overrepair["stopped_after"] == "reachable_rollback_with_equivalent_output"
    assert overrepair["evidence"]["confirmed_patch_unit"] == 1


def test_overrepair_stops_when_a_unit_misses_all_original_false_paths(tmp_path: Path) -> None:
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Foo.java\n+++ repaired/Foo.java\n"
        "@@ -10,1 +10,1 @@\n-old();\n+new();\n"
        "@@ -20,1 +20,1 @@\n-old2();\n+new2();\n",
        encoding="utf-8",
    )
    buggy = tmp_path / "buggy.json"
    patched = tmp_path / "patched.json"
    buggy.write_text(json.dumps(_document("false")), encoding="utf-8")
    patched.write_text(json.dumps(_document("true")), encoding="utf-8")
    result = analyze_pair("Foo-1", patch, buggy, patched)
    test = result["tests"]["overrepair"]
    assert test["status"] == "confirmed"
    assert test["stopped_after"] == "patch_unit_not_on_false_path"
    assert test["evidence"]["unused_patch_units"] == [2]
