from __future__ import annotations

import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

from patch_label.cli import build_parser, command_run
from patch_label.models import Example


def test_run_parser_defaults_to_one_job() -> None:
    args = build_parser().parse_args(["run"])

    assert args.jobs == 1


def test_run_parser_accepts_multiple_jobs() -> None:
    args = build_parser().parse_args(["run", "--jobs", "4"])

    assert args.jobs == 4


def test_command_run_executes_examples_concurrently(tmp_path: Path, monkeypatch) -> None:
    examples = [
        Example("sample", "Project", index, tmp_path, tmp_path / f"{index}.patch")
        for index in range(1, 5)
    ]
    state_lock = threading.Lock()
    state = {"active": 0, "maximum": 0}

    class FakeRunner:
        def __init__(self, config: object):
            pass

        def run(self, example: Example, phases: list[str]) -> list[Path]:
            with state_lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            with state_lock:
                state["active"] -= 1
            return [tmp_path / f"{example.key}.json"]

    monkeypatch.setattr("patch_label.cli.discover_examples", lambda path: examples)
    monkeypatch.setattr("patch_label.cli.select_examples", lambda values, selectors: values)
    monkeypatch.setattr("patch_label.cli.ExperimentRunner", FakeRunner)
    args = Namespace(
        repo_root=tmp_path,
        dataset_dir=Path("dataset"),
        defects4j_dir=Path("defects4j"),
        state_dir=Path("state"),
        output_dir=Path("results"),
        example=[],
        phase="both",
        test_scope="all",
        max_tests=None,
        compile_timeout=1800,
        test_timeout=600,
        discovery_timeout=600,
        max_loop_visits=2,
        max_paths_per_method=1000,
        jobs=2,
        fresh=False,
        resume=True,
        keep_going=True,
    )

    assert command_run(args) == 0
    assert state["maximum"] == 2


def test_command_run_rejects_invalid_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("patch_label.cli.discover_examples", lambda path: [])
    monkeypatch.setattr("patch_label.cli.select_examples", lambda values, selectors: values)
    args = Namespace(
        repo_root=tmp_path,
        dataset_dir=Path("dataset"),
        defects4j_dir=Path("defects4j"),
        state_dir=Path("state"),
        output_dir=Path("results"),
        example=[],
        phase="both",
        test_scope="all",
        max_tests=None,
        compile_timeout=1800,
        test_timeout=600,
        discovery_timeout=600,
        max_loop_visits=2,
        max_paths_per_method=1000,
        jobs=0,
        fresh=False,
        resume=True,
        keep_going=True,
    )

    with pytest.raises(RuntimeError, match="--jobs must be at least 1"):
        command_run(args)
