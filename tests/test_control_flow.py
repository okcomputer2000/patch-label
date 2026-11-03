from pathlib import Path

import pytest

from patch_label.control_flow import assess_control_flow
from patch_label.experiment import ExperimentConfig, ExperimentRunner
from patch_label.models import CommandResult, Example


def graph():
    return {"nodes": [
        {"id": "Sample#value()I:ENTRY", "method_id": "Sample#value()I", "virtual": True},
        {"id": "Sample#value()I:B0", "method_id": "Sample#value()I", "virtual": False},
    ], "edges": [{"source": "Sample#value()I:ENTRY", "target": "Sample#value()I:B0", "kind": "entry"}]}


def compare(old, new, before=None, after=None):
    return assess_control_flow({"Sample.java": old.encode()}, {"Sample.java": new.encode()},
                               before or graph(), after or graph())


@pytest.mark.parametrize("old,new", [
    ("int value() { return 1; }", "int value() { return 2; }"),
    ("int value() { return 1; }", "int value() { /* fixed value */ return 2; }"),
    ("int value(int x) { if (x > 0) return 1; return 2; }",
     "int value(int x) { if (x > 0) return 3; return 2; }"),
    ("int value(int x, int y) { return x + y; }",
     "int value(int x, int y) { return x - y; }"),
    ("int value(int x, int y) { return x; }", "int value(int x, int y) { return y; }"),
])
def test_only_proven_control_neutral_changes_select_relevant(old, new):
    result = compare("class Sample { " + old + " }", "class Sample { " + new + " }")
    assert result["status"] == "unchanged"
    assert result["effective_scope"] == "relevant"


@pytest.mark.parametrize("old,new", [
    ("if (x > 0) return 1;", "if (x >= 0) return 1;"),
    ("if (x > 0) return 1;", "if (x > 1) return 1;"),
    ("while (x > 0) x--;", "while (x >= 0) x--;"),
    ("if (a && b) return 1;", "if (a || b) return 1;"),
    ("switch (x) {case 1: return 1;}", "switch (x) {case 2: return 1;}"),
    ("try { work(); } catch (Exception e) {}", "try { work(); } catch (Error e) {}"),
    ("assert x > 0;", "assert x >= 0;"),
    ("if (x > 0) return 1;", "return 1;"),
])
def test_control_decisions_change_even_with_identical_cfg_topology(old, new):
    result = compare("class Sample { int value() { " + old + " return 0; } }",
                     "class Sample { int value() { " + new + " return 0; } }")
    assert result["status"] == "changed"
    assert result["effective_scope"] == "all"


@pytest.mark.parametrize("old,new", [
    ("x = 1; if (x > 0) return 1;", "x = 2; if (x > 0) return 1;"),
    ("return work();", "return other();"),
    ("return x / 1;", "return x / 0;"),
    ("return x;", "return y;"),
    ("return true && a;", "return false && a;"),
])
def test_data_dependencies_calls_and_exceptions_never_silently_select_relevant(old, new):
    result = compare("class Sample { int value() { " + old + " } }",
                     "class Sample { int value() { " + new + " } }")
    assert result["effective_scope"] == "all"


def test_boxing_return_is_not_treated_as_primitive_constant_return():
    result = compare("class Sample { Integer value() { return 1; } }",
                     "class Sample { Integer value() { return 2; } }")
    assert result["status"] == "unknown"
    assert result["effective_scope"] == "all"


@pytest.mark.parametrize("old,new", [
    ("int value(Integer x) { return 1; }", "int value(Integer x) { return x; }"),
    ("int value(int x[]) { return 1; }", "int value(int x[]) { return x; }"),
    ("int value(int x) { return x / 1; }", "int value(int x) { return x / 0; }"),
    ("int value(int x) { return x; }", "int value(int x) { return x++; }"),
    ("boolean value(int x) { return x > 0; }", "boolean value(int x) { return x >= 0; }"),
])
def test_return_expressions_with_exceptions_side_effects_or_decisions_use_all(old, new):
    assert compare("class Sample { " + old + " }", "class Sample { " + new + " }")["effective_scope"] == "all"


def test_cfg_identity_and_edges_are_compared_instead_of_counts():
    changed = graph()
    changed["edges"][0]["kind"] = "jump"
    result = compare("class Sample {}", "class Sample {}", after=changed)
    assert result["status"] == "changed"
    assert result["effective_scope"] == "all"


def test_source_line_and_instruction_offsets_do_not_count_as_flow_changes():
    changed = graph()
    changed["nodes"][0]["start_line"] = 50
    changed["nodes"][0]["start_instruction"] = 100
    assert compare("class Sample {}", "class Sample {}", after=changed)["status"] == "unchanged"


def test_parse_error_falls_back_to_all():
    assert compare("class Sample {}", "class Sample { broken")["effective_scope"] == "all"


def test_missing_graph_falls_back_to_all():
    result = compare("class Sample {}", "class Sample {}", after={"nodes": [], "edges": []})
    assert result["status"] == "unknown"
    assert result["effective_scope"] == "all"


def test_preflight_restores_sources_and_removes_patched_binaries(tmp_path: Path, monkeypatch):
    checkout = tmp_path / "checkout"
    source = checkout / "src" / "Sample.java"
    source.parent.mkdir(parents=True)
    original = b"class Sample { int value() { return 1; } }\n"
    source.write_bytes(original)
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text("@@ -1 +1 @@\n-class Sample { int value() { return 1; } }\n+class Sample { int value() { return 2; } }\n")
    runner = object.__new__(ExperimentRunner)
    runner.config = ExperimentConfig(tmp_path, tmp_path, tmp_path, tmp_path / "state", tmp_path / "results")
    runner.d4j = type("FakeD4J", (), {"compile": lambda self, *args, **kwargs: CommandResult((), 0, "", "", 0)})()
    monkeypatch.setattr(runner, "_build_graph", lambda *args: graph())
    metadata = {"dir.src.classes": "src", "dir.bin.classes": "classes", "dir.bin.tests": "test-classes",
                "classes.modified": ["Sample"], "tests.relevant": ["SampleTest"]}
    example = Example("D4JV2.0", "Sample", 1, tmp_path, patch)
    result = runner._select_test_scope(example, checkout, metadata, tmp_path / "out")
    assert source.read_bytes() == original
    assert result["effective_scope"] == "relevant"
    assert result["relevant_test_source"] == "defects4j export -p tests.relevant"
    runner._control_flow_cache = {}
    metadata["tests.relevant"] = []
    assert runner._select_test_scope(example, checkout, metadata, tmp_path / "out")["effective_scope"] == "all"
    runner._control_flow_cache = {}
    monkeypatch.setattr(runner, "_build_graph", lambda *args: (_ for unused in ()).throw(RuntimeError("cannot compare")))
    result = runner._select_test_scope(example, checkout, metadata, tmp_path / "out")
    assert source.read_bytes() == original
    assert result["status"] == "unknown"
    assert result["effective_scope"] == "all"


def test_discovery_uses_official_relevant_classes(tmp_path: Path, monkeypatch):
    runner = object.__new__(ExperimentRunner)
    runner.config = ExperimentConfig(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path)
    runner.helper_jar = tmp_path / "helper.jar"
    runner.d4j = type("FakeD4J", (), {"java_home": tmp_path})()
    selected = []
    monkeypatch.setattr("patch_label.experiment.discover_test_cases",
                        lambda helper, checkout, classes, **kwargs: (selected.extend(classes) or [], [], []))
    runner._discover_tests(tmp_path, {
        "patch_label.test_scope": "relevant", "tests.relevant": ["OfficialTest"],
        "tests.all": ["OfficialTest", "UnrelatedTest"], "dir.bin.classes": "classes",
        "dir.bin.tests": "tests", "cp.test": "dependencies",
    })
    assert selected == ["OfficialTest"]


def test_migrated_source_root_overrides_remote_metadata(tmp_path: Path):
    from patch_label.analyzer import _document_source_root

    source_root = tmp_path / "local-sources"
    source_root.mkdir()
    document = {"metadata": {"patch_label.source_root": "/missing/server/worktree/src"},
                "_analysis_source_root": str(source_root)}
    assert _document_source_root(document) == source_root
