from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _escape(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _compact_node(node_id: str, nodes: dict[str, dict[str, Any]]) -> str:
    name = node_id.rsplit(":", 1)[-1]
    node = nodes.get(node_id, {})
    start = node.get("start_line", -1)
    end = node.get("end_line", -1)
    if not isinstance(start, int) or start < 0:
        return name
    return f"{name}[L{start}]" if start == end else f"{name}[L{start}-{end}]"


def path_rows(document: dict[str, Any]) -> list[dict[str, str]]:
    nodes = {node["id"]: node for node in document["graph"]["nodes"]}
    labels_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label in document["labels"]:
        labels_by_path[label["path_id"]].append(label)

    rows: list[dict[str, str]] = []
    for path in document["path_set"]:
        whole_path = " -> ".join(_compact_node(node_id, nodes) for node_id in path["nodes"])
        labels = labels_by_path[path["id"]]
        for label in labels:
            rows.append(
                {
                    "path_id": path["id"],
                    "method_id": path["method_id"],
                    "whole_path": whole_path,
                    "label": label["label"],
                    "observation": label["observation"],
                    "test_outcome": label.get("test_outcome") or "",
                    "test_id": label.get("test_id") or "",
                }
            )
    return rows


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend("| " + " | ".join(_escape(value) for value in row) + " |" for row in rows)
    return output


def _group_rows_by_path(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["path_id"]].append(row)
    return grouped


def _label_summary(path_rows: list[dict[str, str]]) -> str:
    counts = Counter(row["label"] for row in path_rows)
    return ", ".join(
        f"{label} x{counts[label]}" for label in ("true", "false", "unknown") if counts[label]
    )


def _test_summary(path_rows: list[dict[str, str]], *, limit: int = 3) -> str:
    tests = sorted({row["test_id"] for row in path_rows if row["test_id"]})
    visible = tests[:limit]
    suffix = f" (+{len(tests) - limit} more)" if len(tests) > limit else ""
    return ", ".join(visible) + suffix


def _display_path(whole_path: str, *, limit: int = 14) -> str:
    nodes = whole_path.split(" -> ")
    if len(nodes) <= limit:
        return whole_path
    omitted = len(nodes) - 12
    return " -> ".join([
        *nodes[:8],
        f"... ({omitted} nodes omitted; {len(nodes)} total) ...",
        *nodes[-4:],
    ])


def _dot_escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_cfg_dot(document: dict[str, Any]) -> str:
    graph = document["graph"]
    example = document["example"]
    nodes_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in graph["nodes"]:
        nodes_by_method[node["method_id"]].append(node)
    dot_ids = {node["id"]: f"n{index}" for index, node in enumerate(graph["nodes"])}
    title = f"{example['dataset_version']}/{example['key']} ({document['phase']})"
    lines = [
        "digraph CFG {",
        "  graph [rankdir=TB, compound=true, fontname=\"Helvetica\", "
        f"label=\"{_dot_escape(title)}\", labelloc=t];",
        "  node [shape=box, fontname=\"Helvetica\", fontsize=10];",
        "  edge [fontname=\"Helvetica\", fontsize=9];",
    ]
    for method_index, method_id in enumerate(sorted(nodes_by_method)):
        lines.extend([
            f"  subgraph cluster_{method_index} {{",
            f"    label=\"{_dot_escape(method_id)}\";",
            "    color=\"#b8c2cc\";",
        ])
        for node in nodes_by_method[method_id]:
            short_id = node["id"].rsplit(":", 1)[-1]
            if node.get("virtual"):
                label = short_id
                attributes = 'shape=oval, style="filled", fillcolor="#eef2f7"'
            else:
                start_line = node.get("start_line", -1)
                end_line = node.get("end_line", -1)
                source = (
                    f"L{start_line}" if start_line == end_line
                    else f"L{start_line}-{end_line}"
                )
                label = (
                    f"{short_id}\nsource {source}\n"
                    f"bytecode {node.get('start_instruction', -1)}-"
                    f"{node.get('end_instruction', -1)}"
                )
                attributes = 'shape=box, style="rounded"'
            lines.append(
                f'    {dot_ids[node["id"]]} [label="{_dot_escape(label)}", '
                f'tooltip="{_dot_escape(node["id"])}", {attributes}];'
            )
        lines.append("  }")
    for edge in graph["edges"]:
        lines.append(
            f'  {dot_ids[edge["source"]]} -> {dot_ids[edge["target"]]} '
            f'[label="{_dot_escape(edge["kind"])}"];'
        )
    lines.extend(["}", ""])
    return "\n".join(lines)


def render_markdown(document: dict[str, Any], rows: list[dict[str, str]]) -> str:
    summary = document["summary"]
    example = document["example"]
    issues = document.get("evidence_issues", [])
    label_counts = Counter(row["label"] for row in rows)
    grouped = _group_rows_by_path(rows)
    labeled_paths = [
        path_rows for path_rows in grouped.values()
        if any(row["label"] != "untested" for row in path_rows)
    ]
    labeled_paths.sort(key=lambda grouped_rows: (
        0 if any(row["label"] == "false" for row in grouped_rows) else
        1 if any(row["label"] == "unknown" for row in grouped_rows) else 2,
        grouped_rows[0]["method_id"],
        grouped_rows[0]["path_id"],
    ))

    lines = [
        "# Patch-label experiment report",
        "",
        "> **Path-level semantics:** every `true`, `false`, or `unknown` result below applies to the complete `ENTRY -> ... -> EXIT` path. CFG nodes are never labeled individually.",
        "> Long paths are abbreviated only in this Markdown report; `paths.csv` retains every node and every path-test label record.",
        "",
        "## Run summary",
        "",
        *_table(
            ["Field", "Value"],
            [
                ["Example", f"{example['dataset_version']}/{example['key']}"],
                ["Phase", document["phase"]],
                ["Test scope", document["configuration"]["test_scope"]],
                ["Evidence", "INCOMPLETE" if issues else "COMPLETE"],
                ["Tests", summary["test_count"]],
                ["CFG", f"{summary['node_count']} nodes / {summary['edge_count']} edges"],
                ["Labeled complete paths", len(labeled_paths)],
                [
                    "Path-test label records",
                    ", ".join(f"{name}={label_counts[name]}" for name in ("true", "false", "unknown")),
                ],
            ],
        ),
        "",
        "## Complete CFG",
        "",
        "The complete CFG is available in two equivalent forms:",
        "",
        "- [`cfg.dot`](cfg.dot) contains every CFG node and edge as a Graphviz directed graph, grouped by method.",
        "- [`graph.json`](graph.json) contains the same complete node and edge sets with bytecode and source-line metadata.",
        "",
        "## Paths and labels",
        "",
        "`true` means a passing test observed the complete path; `false` means a failing test observed it; `unknown` means relevant evidence was incomplete or the covering test timed out/errored. Paths without one of these labels are omitted here and remain available in `paths.csv`.",
        "",
    ]
    if labeled_paths:
        lines.extend(
            _table(
                ["Path", "Method", "Whole path preview", "Path-test labels", "Covering tests"],
                [
                    [
                        path_rows[0]["path_id"],
                        path_rows[0]["method_id"],
                        f"`{_display_path(path_rows[0]['whole_path'])}`",
                        _label_summary(path_rows),
                        _test_summary(path_rows),
                    ]
                    for path_rows in labeled_paths
                ],
            )
        )
    else:
        lines.append("No `true`, `false`, or `unknown` complete path was produced.")
    lines.extend(
        [
            "",
            "See [`paths.csv`](paths.csv) for the complete, unabridged path-label table.",
            "",
        ]
    )
    return "\n".join(lines)


def write_human_reports(document: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    rows = path_rows(document)
    csv_path = output_dir / "paths.csv"
    report_path = output_dir / "report.md"
    cfg_path = output_dir / "cfg.dot"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("path_id", "method_id", "whole_path", "label", "observation", "test_outcome", "test_id"),
        )
        writer.writeheader()
        writer.writerows(rows)
    report_path.write_text(render_markdown(document, rows), encoding="utf-8")
    cfg_path.write_text(render_cfg_dot(document), encoding="utf-8")
    return report_path, cfg_path, csv_path
