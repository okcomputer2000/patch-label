import csv
from pathlib import Path

from patch_label.graph import enumerate_static_paths, label_graph
from patch_label.report import _display_path, render_cfg_dot, write_human_reports
from test_graph import METHOD, graph_fixture


def test_report_labels_complete_paths_not_nodes(tmp_path: Path) -> None:
    graph = graph_fixture()
    paths, truncations = enumerate_static_paths(
        graph, max_loop_visits=2, max_paths_per_method=100
    )
    trace = [f"{METHOD}:ENTRY", f"{METHOD}:B0", f"{METHOD}:B1", f"{METHOD}:EXIT"]
    labeled = label_graph(
        graph,
        paths,
        [{
            "test": {"selector": "sample.BranchyTest::positive"},
            "status": "true",
            "execution_status": "passed",
            "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": trace}],
        }],
    )
    document = {
        "example": {"dataset_version": "sample", "key": "Branchy-1"},
        "phase": "buggy",
        "configuration": {"test_scope": "trigger"},
        "graph": graph,
        **labeled,
        "path_enumeration_truncations": truncations,
        "tests": [{
            "test": {"selector": "sample.BranchyTest::positive"},
            "execution_status": "passed",
            "trace_truncated": False,
            "duration_seconds": 0.1,
        }],
        "summary": {
            "test_count": 1,
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "bounded_static_path_count": len(paths),
            "complete_path_count": len(labeled["path_set"]),
            "observed_path_addition_count": 0,
        },
    }

    report_path, cfg_path, csv_path = write_human_reports(document, tmp_path)

    report = report_path.read_text(encoding="utf-8")
    assert "result below applies to the complete" in report
    assert "ENTRY -> B0 -> B1 -> EXIT" in report
    assert "Labeled complete paths" in report
    assert "Path-test label records" in report
    assert "Complete CFG" in report
    assert "Paths and labels" in report
    assert "true x1" in report
    assert "untested" not in report
    cfg = cfg_path.read_text(encoding="utf-8")
    assert cfg.startswith("digraph CFG {")
    assert cfg.count("tooltip=") == len(graph["nodes"])
    assert cfg.count(" -> ") == len(graph["edges"])
    assert METHOD in cfg
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["label"] for row in rows} == {"true", "untested"}
    assert all(row["whole_path"].startswith("ENTRY -> ") for row in rows)


def test_long_path_is_abbreviated_only_for_markdown_display() -> None:
    whole_path = " -> ".join(["ENTRY", *(f"B{index}" for index in range(20)), "EXIT"])
    displayed = _display_path(whole_path)

    assert "10 nodes omitted; 22 total" in displayed
    assert displayed.startswith("ENTRY -> B0")
    assert displayed.endswith("B19 -> EXIT")


def test_cfg_dot_contains_every_node_and_edge() -> None:
    graph = graph_fixture()
    document = {
        "example": {"dataset_version": "sample", "key": "Branchy-1"},
        "phase": "buggy",
        "graph": graph,
    }

    cfg = render_cfg_dot(document)

    assert cfg.count("tooltip=") == len(graph["nodes"])
    assert cfg.count(" -> ") == len(graph["edges"])
    for node in graph["nodes"]:
        assert node["id"] in cfg
    for edge in graph["edges"]:
        assert f'label="{edge["kind"]}"' in cfg
