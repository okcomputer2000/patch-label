from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable


def _path_id(method: str, nodes: list[str]) -> str:
    digest = hashlib.sha1((method + "\0" + "\0".join(nodes)).encode()).hexdigest()[:16]
    return f"path:{digest}"


def enumerate_static_paths(
    graph: dict[str, Any],
    *,
    max_loop_visits: int,
    max_paths_per_method: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    node_method = {node["id"]: node["method_id"] for node in graph["nodes"]}
    nodes_by_method: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for node_id, method_id in node_method.items():
        nodes_by_method[method_id].add(node_id)
    for edge in graph["edges"]:
        if node_method.get(edge["source"]) == node_method.get(edge["target"]):
            outgoing[edge["source"]].append(edge["target"])
    for targets in outgoing.values():
        targets.sort()

    paths: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    for method_id in sorted(nodes_by_method):
        entry = f"{method_id}:ENTRY"
        exit_node = f"{method_id}:EXIT"
        if entry not in nodes_by_method[method_id] or exit_node not in nodes_by_method[method_id]:
            continue
        method_paths: list[list[str]] = []
        stack: list[tuple[str, list[str], dict[str, int]]] = [(entry, [entry], {entry: 1})]
        truncated = False
        while stack:
            node_id, path, visits = stack.pop()
            if node_id == exit_node:
                method_paths.append(path)
                if len(method_paths) >= max_paths_per_method:
                    truncated = bool(stack)
                    break
                continue
            targets = outgoing.get(node_id, [])
            if not targets:
                continue
            for target in reversed(targets):
                count = visits.get(target, 0)
                limit = 1 if target in {entry, exit_node} else max_loop_visits
                if count >= limit:
                    continue
                next_visits = visits.copy()
                next_visits[target] = count + 1
                stack.append((target, [*path, target], next_visits))
        for nodes in method_paths:
            paths.append(
                {
                    "id": _path_id(method_id, nodes),
                    "method_id": method_id,
                    "nodes": nodes,
                    "kind": "bounded-entry-exit",
                }
            )
        if truncated:
            truncations.append(
                {
                    "method_id": method_id,
                    "limit": max_paths_per_method,
                    "reason": "max_paths_per_method",
                }
            )
    return paths, truncations


def split_method_invocations(
    events: Iterable[str], node_method: dict[str, str]
) -> list[dict[str, Any]]:
    projected: dict[str, list[str]] = defaultdict(list)
    for node_id in events:
        method_id = node_method.get(node_id)
        if method_id is not None:
            projected[method_id].append(node_id)

    invocations: list[dict[str, Any]] = []
    for method_id, nodes in projected.items():
        entry = f"{method_id}:ENTRY"
        exit_node = f"{method_id}:EXIT"
        stack: list[list[str]] = []
        incomplete: list[str] = []
        for node_id in nodes:
            if node_id == entry:
                stack.append([node_id])
            elif stack:
                stack[-1].append(node_id)
                if node_id == exit_node:
                    invocations.append({"method_id": method_id, "nodes": stack.pop()})
            else:
                incomplete.append(node_id)
        invocations.extend({"method_id": method_id, "nodes": value} for value in stack)
        if incomplete:
            invocations.append({"method_id": method_id, "nodes": incomplete})
    return invocations


def _is_contiguous_subpath(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return False
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


def label_graph(
    graph: dict[str, Any],
    static_paths: list[dict[str, Any]],
    tests: list[dict[str, Any]],
) -> dict[str, Any]:
    node_method = {node["id"]: node["method_id"] for node in graph["nodes"]}
    graph_edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    covered_nodes: set[str] = set()
    covered_edges: set[tuple[str, str]] = set()
    observed_paths: list[dict[str, Any]] = []
    invocations_by_test: dict[str, list[dict[str, Any]]] = {}

    for test in tests:
        selector = test["test"]["selector"]
        invocations: list[dict[str, Any]] = []
        for trace in test.get("traces", []):
            trace_invocations = split_method_invocations(trace, node_method)
            invocations.extend(trace_invocations)
            covered_nodes.update(node for invocation in trace_invocations for node in invocation["nodes"])
            for invocation in trace_invocations:
                nodes = invocation["nodes"]
                covered_edges.update(
                    pair for pair in zip(nodes, nodes[1:]) if pair in graph_edges
                )
                observed_paths.append(
                    {
                        "id": _path_id(invocation["method_id"], nodes),
                        "method_id": invocation["method_id"],
                        "nodes": nodes,
                        "test_id": selector,
                        "status": test["status"],
                    }
                )
        invocations_by_test[selector] = invocations

    labels: list[dict[str, Any]] = []
    for path in static_paths:
        matches = 0
        for test in tests:
            selector = test["test"]["selector"]
            covered = any(
                invocation["method_id"] == path["method_id"]
                and _is_contiguous_subpath(path["nodes"], invocation["nodes"])
                for invocation in invocations_by_test[selector]
            )
            if covered:
                matches += 1
                labels.append(
                    {
                        "path_id": path["id"],
                        "status": test["status"],
                        "test_id": selector,
                    }
                )
        if matches == 0:
            labels.append({"path_id": path["id"], "status": "untested", "test_id": None})

    deduplicated_observed = list(
        {
            (item["id"], item["test_id"], item["status"]): item for item in observed_paths
        }.values()
    )
    nonvirtual_nodes = {
        node["id"] for node in graph["nodes"] if not node.get("virtual")
    }
    return {
        "path_set": static_paths,
        "labels": labels,
        "observed_paths": sorted(
            deduplicated_observed,
            key=lambda item: (item["test_id"], item["method_id"], item["id"]),
        ),
        "coverage": {
            "covered_nodes": sorted(covered_nodes),
            "untested_nodes": sorted(nonvirtual_nodes - covered_nodes),
            "covered_edges": [
                {"source": source, "target": target} for source, target in sorted(covered_edges)
            ],
            "untested_edges": [
                {"source": source, "target": target}
                for source, target in sorted(graph_edges - covered_edges)
            ],
        },
    }
