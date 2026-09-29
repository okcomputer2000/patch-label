import json
from pathlib import Path

from patch_label.analyzer import analyze_pair, _path_is_sat


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
    assert test["evidence"]["sat"] ["sat"] is True


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
