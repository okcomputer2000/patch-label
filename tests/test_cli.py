import json
import threading
import time
from pathlib import Path

from patch_label import cli
from patch_label.models import Example


def test_run_reports_test_execution_errors_as_failure(tmp_path: Path, monkeypatch) -> None:
    example = Example("D4JV2.0", "Compress", 44, tmp_path, tmp_path / "thinkrepair.patch")
    result = tmp_path / "results" / "D4JV2.0" / "Compress-44" / "experiment.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"summary": {
        "errored_test_count": 1,
        "timed_out_test_count": 0,
    }}), encoding="utf-8")

    class FakeRunner:
        def __init__(self, config):
            assert config.expected_defects4j_tag == "v2.0.0"
            assert config.java_major == 8
            assert config.defects4j_dir == tmp_path / "tools/defects4j-v2.0"

        def run(self, selected, phases):
            assert selected == example
            return [result]

    monkeypatch.setattr(cli, "discover_examples", lambda dataset_dir: [example])
    monkeypatch.setattr(cli, "missing_versioned_bugs", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "missing_versioned_revisions", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "ExperimentRunner", FakeRunner)
    args = cli.build_parser().parse_args(["run", "--repo-root", str(tmp_path)])

    assert cli.command_run(args) == 1
    failures = json.loads((tmp_path / "results" / "failures.json").read_text(encoding="utf-8"))
    assert failures[0]["example"] == "Compress-44"
    assert "1 test error" in failures[0]["error"]


def test_run_executes_examples_concurrently_with_version_routing(tmp_path: Path, monkeypatch) -> None:
    examples = [
        Example("D4JV2.0", "Compress", index, tmp_path, tmp_path / f"{index}.patch")
        for index in range(1, 5)
    ]
    lock = threading.Lock()
    active = 0
    maximum = 0

    class FakeRunner:
        def __init__(self, config):
            assert config.expected_defects4j_tag == "v2.0.0"
            assert config.java_major == 8

        def run(self, example, phases):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            result = tmp_path / f"{example.key}.json"
            result.write_text(json.dumps({"summary": {}}), encoding="utf-8")
            return [result]

    monkeypatch.setattr(cli, "discover_examples", lambda dataset_dir: examples)
    monkeypatch.setattr(cli, "missing_versioned_bugs", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "missing_versioned_revisions", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "ExperimentRunner", FakeRunner)
    args = cli.build_parser().parse_args(["run", "--repo-root", str(tmp_path), "--jobs", "2"])

    assert cli.command_run(args) == 0
    assert maximum == 2


def test_math_uses_java8_without_changing_defects4j_release() -> None:
    math = Example("D4JV1.2", "Math", 91, Path("."), Path("patch"))
    closure = Example("D4JV1.2", "Closure", 122, Path("."), Path("patch"))
    assert cli._runtime_for_example(math) == ("v1.2.0", 8)
    assert cli._runtime_for_example(closure) == ("v1.2.0", 7)


def test_run_routes_math_and_closure_to_distinct_jdks(tmp_path: Path, monkeypatch) -> None:
    examples = [
        Example("D4JV1.2", "Math", 91, tmp_path, tmp_path / "math.patch"),
        Example("D4JV1.2", "Closure", 122, tmp_path, tmp_path / "closure.patch"),
    ]
    routed = []

    class FakeRunner:
        def __init__(self, config):
            assert config.expected_defects4j_tag == "v1.2.0"
            self.java_major = config.java_major

        def run(self, example, phases):
            routed.append((example.key, self.java_major))
            output = tmp_path / f"{example.key}.json"
            output.write_text(json.dumps({"summary": {}}), encoding="utf-8")
            return [output]

    monkeypatch.setattr(cli, "discover_examples", lambda dataset_dir: examples)
    monkeypatch.setattr(cli, "missing_versioned_bugs", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "missing_versioned_revisions", lambda examples, dirs: [])
    monkeypatch.setattr(cli, "ExperimentRunner", FakeRunner)
    args = cli.build_parser().parse_args(["run", "--repo-root", str(tmp_path)])
    assert cli.command_run(args) == 0
    assert routed == [("Math-91", 8), ("Closure-122", 7)]
