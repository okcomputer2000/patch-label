from patch_label.graph import enumerate_static_paths, label_graph, split_method_invocations


METHOD = "sample.Branchy#classify(I)I"


def graph_fixture() -> dict[str, object]:
    nodes = [
        {"id": f"{METHOD}:ENTRY", "method_id": METHOD, "virtual": True},
        {"id": f"{METHOD}:B0", "method_id": METHOD, "virtual": False},
        {"id": f"{METHOD}:B1", "method_id": METHOD, "virtual": False},
        {"id": f"{METHOD}:B2", "method_id": METHOD, "virtual": False},
        {"id": f"{METHOD}:EXIT", "method_id": METHOD, "virtual": True},
    ]
    edges = [
        {"source": f"{METHOD}:ENTRY", "target": f"{METHOD}:B0", "kind": "entry"},
        {"source": f"{METHOD}:B0", "target": f"{METHOD}:B1", "kind": "jump"},
        {"source": f"{METHOD}:B0", "target": f"{METHOD}:B2", "kind": "fallthrough"},
        {"source": f"{METHOD}:B1", "target": f"{METHOD}:EXIT", "kind": "exit"},
        {"source": f"{METHOD}:B2", "target": f"{METHOD}:EXIT", "kind": "exit"},
    ]
    return {"nodes": nodes, "edges": edges}


def test_enumerates_and_labels_untested_branch() -> None:
    graph = graph_fixture()
    paths, truncations = enumerate_static_paths(
        graph, max_loop_visits=2, max_paths_per_method=100
    )
    trace = [f"{METHOD}:ENTRY", f"{METHOD}:B0", f"{METHOD}:B1", f"{METHOD}:EXIT"]
    labels = label_graph(
        graph,
        paths,
        [
            {
                "test": {"selector": "sample.BranchyTest::positive"},
                "status": "true",
                "traces": [{"file": "trace-1.tsv", "thread_id": "1", "nodes": trace}],
            }
        ],
    )

    assert not truncations
    assert len(paths) == 2
    assert {label["observation"] for label in labels["labels"]} == {"observed", "not_observed"}
    assert {label["test_outcome"] for label in labels["labels"]} == {"passed", None}
    assert f"{METHOD}:B2" in labels["coverage"]["unobserved_nodes"]


def test_splits_recursive_invocations_with_entry_exit_stack() -> None:
    node_method = {
        f"{METHOD}:{suffix}": METHOD for suffix in ("ENTRY", "B0", "B1", "EXIT")
    }
    events = [
        f"{METHOD}:ENTRY",
        f"{METHOD}:B0",
        f"{METHOD}:ENTRY",
        f"{METHOD}:B0",
        f"{METHOD}:B1",
        f"{METHOD}:EXIT",
        f"{METHOD}:B1",
        f"{METHOD}:EXIT",
    ]

    invocations = split_method_invocations(events, node_method)

    assert len(invocations) == 2
    assert [len(invocation["nodes"]) for invocation in invocations] == [4, 4]
    assert all(invocation["complete"] for invocation in invocations)


def test_incomplete_invocation_cannot_label_a_static_path() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "false",
        "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": [
            f"{METHOD}:ENTRY", f"{METHOD}:B0", f"{METHOD}:B1",
        ]}],
    }])
    assert all(item["observation"] == "unknown" for item in labels["labels"])
    assert labels["evidence_issues"] == [{"test_id": "test", "reason": "incomplete_invocation"}]


def test_truncated_trace_cannot_supply_a_path_label() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "true",
        "trace_truncated": True,
        "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": [
            f"{METHOD}:ENTRY", f"{METHOD}:B0", f"{METHOD}:B1", f"{METHOD}:EXIT",
        ]}],
    }])
    assert all(item["observation"] == "unknown" for item in labels["labels"])
    assert labels["evidence_issues"] == [{"test_id": "test", "reason": "trace_truncated"}]


def test_missing_tests_or_discovery_errors_make_unmatched_paths_unknown() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [], discovery_incomplete=True)
    assert all(item["observation"] == "unknown" for item in labels["labels"])
    assert {item["reason"] for item in labels["evidence_issues"]} == {
        "no_tests_selected", "test_discovery_incomplete",
    }


def test_timeout_is_not_reported_as_a_failed_test_path() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "false",
        "execution_status": "timed_out",
        "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": [
            f"{METHOD}:ENTRY", f"{METHOD}:B0", f"{METHOD}:B1", f"{METHOD}:EXIT",
        ]}],
    }])
    assert any(item["test_outcome"] == "timed_out" for item in labels["labels"])
    assert any(item["observation"] == "unknown" for item in labels["labels"])

