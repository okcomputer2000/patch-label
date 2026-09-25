from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Phase = Literal["buggy", "patched"]


@dataclass(frozen=True, slots=True)
class Example:
    dataset_version: str
    project: str
    bug_id: int
    directory: Path
    patch_file: Path

    @property
    def key(self) -> str:
        return f"{self.project}-{self.bug_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "project": self.project,
            "bug_id": self.bug_id,
            "key": self.key,
            "directory": str(self.directory),
            "patch_file": str(self.patch_file),
        }


@dataclass(frozen=True, slots=True)
class TestCase:
    selector: str
    class_name: str
    method_name: str | None
    display_name: str
    granularity: Literal["method", "class"] = "method"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TestResult:
    test: TestCase
    status: Literal["true", "false"]
    execution_status: Literal["passed", "failed", "timed_out", "error"]
    return_code: int
    duration_seconds: float
    failing_tests: list[str] = field(default_factory=list)
    trace_files: list[str] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    trace_truncated: bool = False
    dropped_events: int = 0
    run_fingerprint: str = ""
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["test"] = self.test.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

