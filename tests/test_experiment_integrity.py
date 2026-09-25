from pathlib import Path

import pytest

from patch_label.experiment import ExperimentConfig, ExperimentError, ExperimentRunner, _parse_trace_files
from patch_label.models import Example
from patch_label.models import CommandResult
from patch_label.models import TestCase as SelectedTest


def test_trace_parser_keeps_threads_separate_and_reports_dropped_events(tmp_path: Path) -> None:
    trace = tmp_path / "trace-1.tsv"
    trace.write_text(
        "# sequence\tthread\tnode_id\n"
        "# trace_truncated\ttrue\n"
        "# dropped_events\t2\n"
        "1\t10\tM:ENTRY\n"
        "2\t11\tM:ENTRY\n"
        "3\t10\tM:EXIT\n"
        "4\t11\tM:EXIT\n",
        encoding="utf-8",
    )
    names, traces, dropped = _parse_trace_files(tmp_path)
    assert names == [trace.name]
    assert dropped == 2
    assert [(item["thread_id"], item["nodes"]) for item in traces] == [
        ("10", ["M:ENTRY", "M:EXIT"]),
        ("11", ["M:ENTRY", "M:EXIT"]),
    ]


def test_trace_parser_rejects_missing_integrity_metadata(tmp_path: Path) -> None:
    (tmp_path / "trace-1.tsv").write_text("1\t10\tM:ENTRY\n", encoding="utf-8")
    with pytest.raises(ExperimentError, match="Missing trace integrity metadata"):
        _parse_trace_files(tmp_path)


def test_invalid_limits_fail_before_running_tools(tmp_path: Path) -> None:
    config = ExperimentConfig(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path,
                              max_tests=0)
    with pytest.raises(ExperimentError, match="max_tests must be at least 1"):
        ExperimentRunner(config)


def test_run_fingerprint_changes_with_config_patch_and_source(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "java" / "src").mkdir(parents=True)
    (root / "src" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    patch = root / "thinkrepair.patch"
    patch.write_text("first", encoding="utf-8")
    helper = root / "helper.jar"
    helper.write_bytes(b"helper")
    config = ExperimentConfig(root, root, root, root, root)
    runner = object.__new__(ExperimentRunner)
    runner.config = config
    runner.helper_jar = helper
    runner.d4j = type("FakeD4J", (), {"java_home": None})()
    monkeypatch.setattr("patch_label.experiment.run_command", lambda *args, **kwargs:
                        CommandResult((), 0, "revision-a\n", "", 0))
    example = Example("D4JV2.0", "Compress", 44, root, patch)
    baseline = runner._run_fingerprint(example, "buggy")
    config.test_scope = "trigger"
    assert runner._run_fingerprint(example, "buggy") != baseline
    config.test_scope = "all"
    patch.write_text("second", encoding="utf-8")
    assert runner._run_fingerprint(example, "buggy") != baseline
    patch.write_text("first", encoding="utf-8")
    (root / "src" / "module.py").write_text("x = 2\n", encoding="utf-8")
    assert runner._run_fingerprint(example, "buggy") != baseline


def test_new_run_preserves_legacy_result(tmp_path: Path, monkeypatch) -> None:
    config = ExperimentConfig(tmp_path, tmp_path, tmp_path, tmp_path / "state", tmp_path / "results")
    runner = object.__new__(ExperimentRunner)
    runner.config = config
    helper = tmp_path / "helper.jar"
    helper.write_bytes(b"helper")
    runner.helper_jar = helper
    runner.d4j = type("FakeD4J", (), {
        "checkout": lambda self, *args, **kwargs: None,
        "compile": lambda self, *args, **kwargs: CommandResult((), 0, "", "", 0),
    })()
    monkeypatch.setattr(runner, "_run_fingerprint", lambda *args: "a" * 64)
    monkeypatch.setattr(runner, "_metadata", lambda *args: {"classes.modified": []})
    monkeypatch.setattr(runner, "_build_graph", lambda *args: {"nodes": [], "edges": []})
    monkeypatch.setattr(runner, "_discover_tests", lambda *args: ([], []))
    example = Example("D4JV2.0", "Compress", 44, tmp_path, tmp_path / "patch")
    legacy = config.output_dir / "D4JV2.0" / "Compress-44" / "buggy" / "experiment.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"schema_version":"1.0"}', encoding="utf-8")
    new_result = runner.run_phase(example, "buggy")
    assert new_result.parent.name == "a" * 64
    assert legacy.read_text(encoding="utf-8") == '{"schema_version":"1.0"}'
    assert runner.run_phase(example, "buggy") == new_result
    monkeypatch.setattr(runner, "_run_fingerprint", lambda *args: "b" * 64)
    second_result = runner.run_phase(example, "buggy")
    assert second_result != new_result
    assert new_result.is_file() and second_result.is_file()


def test_command_failure_without_failing_tests_is_an_error(tmp_path: Path) -> None:
    runner = object.__new__(ExperimentRunner)
    runner.config = ExperimentConfig(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path)
    runner.d4j = type("FakeD4J", (), {
        "test": lambda self, *args, **kwargs: (CommandResult((), 2, "", "command failed", 0), []),
    })()
    test = SelectedTest("Sample::test", "Sample", "test", "Sample::test")
    result = runner._run_or_load_test(tmp_path, tmp_path / "out", tmp_path / "runtime",
                                      tmp_path / "agent.jar", tmp_path / "includes.txt", test,
                                      "fingerprint")
    assert result["execution_status"] == "error"
    assert result["status"] == "false"
