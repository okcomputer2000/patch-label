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
    events: Iterable[str],
    node_method: dict[str, str],
    graph_edges: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    known_edges = graph_edges or set()
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
        for index, node_id in enumerate(nodes):
            if node_id == entry:
                stack.append([node_id])
            elif stack:
                if node_id == exit_node and index + 1 < len(nodes):
                    next_node = nodes[index + 1]
                    previous_node = stack[-1][-1]
                    if next_node != entry and (previous_node, next_node) in known_edges:
                        continue
                stack[-1].append(node_id)
                if node_id == exit_node:
                    invocations.append({"method_id": method_id, "nodes": stack.pop(), "complete": True})
            else:
                incomplete.append(node_id)
        invocations.extend({"method_id": method_id, "nodes": value, "complete": False} for value in stack)
        if incomplete:
            invocations.append({"method_id": method_id, "nodes": incomplete, "complete": False})
    return invocations


def _is_contiguous_subpath(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return False
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


def _whole_path_label(observation: str, test_outcome: str | None) -> str:
    if observation == "not_observed":
        return "untested"
    if observation != "observed":
        return "unknown"
    if test_outcome == "passed":
        return "true"
    if test_outcome == "failed":
        return "false"
    return "unknown"


def label_graph(
    graph: dict[str, Any],
    static_paths: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    *,
    discovery_incomplete: bool = False,
) -> dict[str, Any]:
    node_method = {node["id"]: node["method_id"] for node in graph["nodes"]}
    graph_edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    covered_nodes: set[str] = set()
    covered_edges: set[tuple[str, str]] = set()
    observed_paths: list[dict[str, Any]] = []
    incomplete_invocations: list[dict[str, Any]] = []
    invocations_by_test: dict[str, list[dict[str, Any]]] = {}
    evidence_issues: list[dict[str, str]] = []
    if not tests:
        evidence_issues.append({"test_id": "", "reason": "no_tests_selected"})
    if discovery_incomplete:
        evidence_issues.append({"test_id": "", "reason": "test_discovery_incomplete"})

    for test in tests:
        selector = test["test"]["selector"]
        invocations: list[dict[str, Any]] = []
        if not test.get("trace_files") and not test.get("traces"):
            evidence_issues.append({"test_id": selector, "reason": "no_trace_file"})
        if test.get("trace_truncated"):
            evidence_issues.append({"test_id": selector, "reason": "trace_truncated"})
        if test.get("execution_status") == "timed_out":
            evidence_issues.append({"test_id": selector, "reason": "test_timed_out"})
        if test.get("execution_status") == "error":
            evidence_issues.append({"test_id": selector, "reason": "test_execution_error"})
        for trace in test.get("traces", []):
            events = trace["nodes"] if isinstance(trace, dict) else trace
            trace_invocations = split_method_invocations(events, node_method, graph_edges)
            for item in trace_invocations:
                if not item["complete"]:
                    incomplete_invocations.append({
                        **item,
                        "test_id": selector,
                        "test_outcome": test.get(
                            "execution_status",
                            "passed" if test["status"] == "true" else "failed",
                        ),
                    })
                    evidence_issues.append({
                        "test_id": selector,
                        "method_id": item["method_id"],
                        "reason": "incomplete_invocation",
                    })
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
                        "complete": invocation["complete"],
                        "trace_truncated": bool(test.get("trace_truncated")),
                        "test_outcome": test.get("execution_status", "passed" if test["status"] == "true" else "failed"),
                        "trace_file": trace.get("file") if isinstance(trace, dict) else None,
                        "thread_id": trace.get("thread_id") if isinstance(trace, dict) else None,
                    }
                )
            invocations.extend(item for item in trace_invocations if item["complete"])
        invocations_by_test[selector] = invocations

    path_set = list(static_paths)
    existing_path_ids = {path["id"] for path in path_set}
    observed_additions = {
        item["id"]: {
            "id": item["id"],
            "method_id": item["method_id"],
            "nodes": item["nodes"],
            "kind": "observed-entry-exit",
        }
        for item in observed_paths
        if item["complete"] and item["id"] not in existing_path_ids
    }
    path_set.extend(
        sorted(observed_additions.values(), key=lambda item: (item["method_id"], item["id"]))
    )

    labels: list[dict[str, Any]] = []
    for path in path_set:
        path_has_label = False
        for test in tests:
            selector = test["test"]["selector"]
            covered = any(
                invocation["method_id"] == path["method_id"]
                and _is_contiguous_subpath(path["nodes"], invocation["nodes"])
                for invocation in invocations_by_test[selector]
            )
            if covered:
                path_has_label = True
                test_outcome = test.get(
                    "execution_status", "passed" if test["status"] == "true" else "failed"
                )
                labels.append(
                    {
                        "path_id": path["id"],
                        "observation": "observed",
                        "test_outcome": test_outcome,
                        "test_id": selector,
                        "label": _whole_path_label("observed", test_outcome),
                    }
                )
                continue
            affected_by_incomplete_invocation = any(
                invocation["test_id"] == selector
                and invocation["method_id"] == path["method_id"]
                and _is_contiguous_subpath(invocation["nodes"], path["nodes"])
                for invocation in incomplete_invocations
            )
            if affected_by_incomplete_invocation:
                path_has_label = True
                labels.append({
                    "path_id": path["id"],
                    "observation": "unknown",
                    "test_outcome": test.get(
                        "execution_status",
                        "passed" if test["status"] == "true" else "failed",
                    ),
                    "test_id": selector,
                    "label": "unknown",
                })
        if not path_has_label:
            labels.append({
                "path_id": path["id"],
                "observation": "not_observed",
                "test_outcome": None,
                "test_id": None,
                "label": "untested",
            })

    deduplicated_observed = list(
        {
            (item["id"], item["test_id"], item["complete"], item["trace_truncated"], item["trace_file"], item["thread_id"]): item
            for item in observed_paths
        }.values()
    )
    nonvirtual_nodes = {
        node["id"] for node in graph["nodes"] if not node.get("virtual")
    }
    return {
        "path_set": path_set,
        "labels": labels,
        "observed_paths": sorted(
            deduplicated_observed,
            key=lambda item: (item["test_id"], item["method_id"], item["id"]),
        ),
        "evidence_issues": sorted(
            {
                (item["test_id"], item.get("method_id", ""), item["reason"]): item
                for item in evidence_issues
            }.values(),
            key=lambda item: (item["test_id"], item.get("method_id", ""), item["reason"]),
        ),
        "coverage": {
            "covered_nodes": sorted(covered_nodes),
            "unobserved_nodes": sorted(nonvirtual_nodes - covered_nodes),
            "covered_edges": [
                {"source": source, "target": target} for source, target in sorted(covered_edges)
            ],
            "unobserved_edges": [
                {"source": source, "target": target}
                for source, target in sorted(graph_edges - covered_edges)
            ],
        },
    }
