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
    assert {label["label"] for label in labels["labels"]} == {"true", "untested"}
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


def test_ignores_exit_probe_when_exception_is_caught_in_same_method() -> None:
    node_method = {
        f"{METHOD}:{suffix}": METHOD for suffix in ("ENTRY", "B0", "B1", "B2", "EXIT")
    }
    events = [
        f"{METHOD}:ENTRY",
        f"{METHOD}:B0",
        f"{METHOD}:B1",
        f"{METHOD}:EXIT",
        f"{METHOD}:B2",
        f"{METHOD}:EXIT",
    ]

    invocations = split_method_invocations(
        events,
        node_method,
        {(f"{METHOD}:B1", f"{METHOD}:B2")},
    )

    assert invocations == [{
        "method_id": METHOD,
        "nodes": [
            f"{METHOD}:ENTRY",
            f"{METHOD}:B0",
            f"{METHOD}:B1",
            f"{METHOD}:B2",
            f"{METHOD}:EXIT",
        ],
        "complete": True,
    }]


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
    path_methods = {path["id"]: path["nodes"] for path in labels["path_set"]}
    affected = [
        item for item in labels["labels"]
        if f"{METHOD}:B1" in path_methods[item["path_id"]]
    ]
    unaffected = [
        item for item in labels["labels"]
        if f"{METHOD}:B2" in path_methods[item["path_id"]]
    ]
    assert {item["label"] for item in affected} == {"unknown"}
    assert {item["label"] for item in unaffected} == {"untested"}
    assert all(item["observation"] != "observed" for item in labels["labels"])
    assert labels["evidence_issues"] == [{
        "test_id": "test",
        "method_id": METHOD,
        "reason": "incomplete_invocation",
    }]


def test_incomplete_invocation_only_makes_its_method_unknown() -> None:
    graph = graph_fixture()
    other_method = "sample.Branchy#other()V"
    graph["nodes"].extend([
        {"id": f"{other_method}:ENTRY", "method_id": other_method, "virtual": True},
        {"id": f"{other_method}:B0", "method_id": other_method, "virtual": False},
        {"id": f"{other_method}:EXIT", "method_id": other_method, "virtual": True},
    ])
    graph["edges"].extend([
        {"source": f"{other_method}:ENTRY", "target": f"{other_method}:B0", "kind": "entry"},
        {"source": f"{other_method}:B0", "target": f"{other_method}:EXIT", "kind": "exit"},
    ])
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)

    labeled = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "true",
        "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": [
            f"{METHOD}:ENTRY", f"{METHOD}:B0",
        ]}],
    }])
    path_methods = {path["id"]: path["method_id"] for path in labeled["path_set"]}
    labels_by_method = {
        method_id: {
            label["label"] for label in labeled["labels"]
            if path_methods[label["path_id"]] == method_id
        }
        for method_id in (METHOD, other_method)
    }

    assert labels_by_method[METHOD] == {"unknown"}
    assert labels_by_method[other_method] == {"untested"}


def test_complete_path_before_trace_truncation_remains_usable() -> None:
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
    assert {item["observation"] for item in labels["labels"]} == {"observed", "not_observed"}
    assert {item["label"] for item in labels["labels"]} == {"true", "untested"}
    assert labels["evidence_issues"] == [{"test_id": "test", "reason": "trace_truncated"}]


def test_complete_observed_loop_path_is_added_beyond_static_bound() -> None:
    graph = graph_fixture()
    graph["edges"].append(
        {"source": f"{METHOD}:B1", "target": f"{METHOD}:B0", "kind": "jump"}
    )
    paths, _ = enumerate_static_paths(graph, max_loop_visits=1, max_paths_per_method=100)
    dynamic_path = [
        f"{METHOD}:ENTRY",
        f"{METHOD}:B0",
        f"{METHOD}:B1",
        f"{METHOD}:B0",
        f"{METHOD}:B1",
        f"{METHOD}:EXIT",
    ]

    labeled = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "true",
        "execution_status": "passed",
        "traces": [{"file": "trace.tsv", "thread_id": "1", "nodes": dynamic_path}],
    }])

    added = [path for path in labeled["path_set"] if path["kind"] == "observed-entry-exit"]
    assert [path["nodes"] for path in added] == [dynamic_path]
    added_id = added[0]["id"]
    assert any(
        label["path_id"] == added_id and label["label"] == "true"
        for label in labeled["labels"]
    )


def test_missing_tests_or_discovery_errors_do_not_fabricate_path_unknowns() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [], discovery_incomplete=True)
    assert all(item["observation"] == "not_observed" for item in labels["labels"])
    assert all(item["label"] == "untested" for item in labels["labels"])
    assert {item["reason"] for item in labels["evidence_issues"]} == {
        "no_tests_selected", "test_discovery_incomplete",
    }


def test_empty_trace_is_valid_when_test_does_not_reach_tracked_code() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "true",
        "execution_status": "passed",
        "trace_files": ["trace.tsv"],
        "traces": [],
    }])

    assert labels["evidence_issues"] == []
    assert all(item["label"] == "untested" for item in labels["labels"])


def test_missing_trace_file_is_run_issue_not_a_path_label() -> None:
    graph = graph_fixture()
    paths, _ = enumerate_static_paths(graph, max_loop_visits=2, max_paths_per_method=100)
    labels = label_graph(graph, paths, [{
        "test": {"selector": "test"},
        "status": "true",
        "execution_status": "passed",
        "trace_files": [],
        "traces": [],
    }])

    assert labels["evidence_issues"] == [{"test_id": "test", "reason": "no_trace_file"}]
    assert all(item["label"] == "untested" for item in labels["labels"])


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
    assert any(item["label"] == "unknown" for item in labels["labels"])
    assert any(
        item["observation"] == "observed" and item["label"] == "unknown"
        for item in labels["labels"]
    )
