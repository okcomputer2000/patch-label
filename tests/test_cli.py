import json
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
            pass

        def run(self, selected, phases):
            assert selected == example
            return [result]

    monkeypatch.setattr(cli, "discover_examples", lambda dataset_dir: [example])
    monkeypatch.setattr(cli, "ExperimentRunner", FakeRunner)
    args = cli.build_parser().parse_args(["run", "--repo-root", str(tmp_path)])

    assert cli.command_run(args) == 1
    failures = json.loads((tmp_path / "results" / "failures.json").read_text(encoding="utf-8"))
    assert failures[0]["example"] == "Compress-44"
    assert "1 test error" in failures[0]["error"]
