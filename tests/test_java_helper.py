import json
import shutil
from pathlib import Path

from patch_label.java_helper import build_helper
from patch_label.experiment import _parse_trace_files
from patch_label.process import run_command


def test_java_helper_builds_graph_and_records_ordered_trace(tmp_path: Path) -> None:
    repo_root = Path.cwd()
    helper = build_helper(repo_root, repo_root / ".patch-label")
    runtime_jar = tmp_path / "patch-label-agent.jar"
    shutil.copy2(helper, runtime_jar)
    classes = tmp_path / "classes"
    classes.mkdir()
    run_command(
        [
            "javac",
            "--release",
            "8",
            "-d",
            str(classes),
            "tests/fixtures/java/sample/Branchy.java",
            "tests/fixtures/java/sample/IsolatedLoader.java",
        ],
        cwd=repo_root,
    )

    includes = tmp_path / "includes.txt"
    includes.write_text("sample.Branchy\n", encoding="utf-8")
    graph_file = tmp_path / "graph.json"
    run_command(
        [
            "java",
            "-cp",
            str(runtime_jar),
            "patchlabel.cfg.GraphCli",
            str(graph_file),
            str(includes),
            str(classes),
        ],
        cwd=repo_root,
    )
    graph = json.loads(graph_file.read_text(encoding="utf-8"))
    classify_nodes = [
        node for node in graph["nodes"] if node["method_name"] == "classify"
    ]
    assert len([node for node in classify_nodes if not node["virtual"]]) == 3
    assert {edge["kind"] for edge in graph["edges"]} >= {"entry", "jump", "fallthrough", "exit"}

    trace_dir = tmp_path / "traces"
    properties = tmp_path / "agent.properties"
    properties.write_text(
        f"outputDir={trace_dir}\nincludesFile={includes}\nmaxEvents=10000\n",
        encoding="utf-8",
    )
    result = run_command(
        [
            "java",
            f"-javaagent:{runtime_jar}={properties}",
            "-cp",
            str(classes),
            "sample.Branchy",
            "2",
        ],
        cwd=repo_root,
    )
    assert result.stdout.strip() == "1"
    trace_text = "\n".join(
        trace.read_text(encoding="utf-8") for trace in trace_dir.glob("trace-*.tsv")
    )
    assert "sample.Branchy#classify(I)I:ENTRY" in trace_text
    assert "sample.Branchy#classify(I)I:EXIT" in trace_text

    isolated_dir = tmp_path / "isolated-traces"
    isolated_properties = tmp_path / "isolated-agent.properties"
    isolated_properties.write_text(
        f"outputDir={isolated_dir}\nincludesFile={includes}\nmaxEvents=10000\n",
        encoding="utf-8",
    )
    isolated = run_command(
        ["java", f"-javaagent:{runtime_jar}={isolated_properties}",
         "-cp", str(classes), "sample.IsolatedLoader", str(classes)],
        cwd=repo_root,
    )
    assert isolated.stdout.strip() == "1"
    isolated_traces = "\n".join(
        trace.read_text(encoding="utf-8") for trace in isolated_dir.glob("trace-*.tsv")
    )
    assert "sample.Branchy#classify(I)I:ENTRY" in isolated_traces

    compile_dir = tmp_path / "compile-traces"
    compile_properties = tmp_path / "compile-agent.properties"
    compile_properties.write_text(
        f"outputDir={compile_dir}\nincludesFile={includes}\nmaxEvents=10000\n",
        encoding="utf-8",
    )
    run_command(
        ["java", f"-javaagent:{runtime_jar}={compile_properties}",
         "-cp", str(classes), "sample.Branchy", "2", "compile.tests"],
        cwd=repo_root,
    )
    compile_traces = "\n".join(
        trace.read_text(encoding="utf-8") for trace in compile_dir.glob("trace-*.tsv")
    )
    assert "sample.Branchy#classify(I)I:ENTRY" not in compile_traces

    limited_dir = tmp_path / "limited-traces"
    limited_properties = tmp_path / "limited-agent.properties"
    limited_properties.write_text(
        f"outputDir={limited_dir}\nincludesFile={includes}\nmaxEvents=1\n",
        encoding="utf-8",
    )
    run_command(
        ["java", f"-javaagent:{runtime_jar}={limited_properties}", "-cp", str(classes),
         "sample.Branchy", "2"],
        cwd=repo_root,
    )
    names, traces, dropped = _parse_trace_files(limited_dir)
    assert names and traces
    assert dropped > 0
