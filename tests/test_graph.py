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
                "traces": [trace],
            }
        ],
    )

    assert not truncations
    assert len(paths) == 2
    assert {label["status"] for label in labels["labels"]} == {"true", "untested"}
    assert f"{METHOD}:B2" in labels["coverage"]["untested_nodes"]


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

