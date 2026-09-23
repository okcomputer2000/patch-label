from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .defects4j import Defects4J, split_exported_list
from .graph import enumerate_static_paths, label_graph
from .io import read_json_gz, write_json, write_json_gz
from .java_helper import build_helper
from .models import Example, Phase, TestCase, TestResult
from .patching import apply_context_patch, class_source_candidates
from .process import CommandError, run_command
from .test_discovery import discover_test_cases


@dataclass(slots=True)
class ExperimentConfig:
    repo_root: Path
    dataset_dir: Path
    defects4j_dir: Path
    state_dir: Path
    output_dir: Path
    test_scope: Literal["all", "relevant", "trigger"] = "all"
    max_tests: int | None = None
    compile_timeout: float = 1800
    test_timeout: float = 600
    discovery_timeout: float = 600
    max_loop_visits: int = 2
    max_paths_per_method: int = 1000
    fresh: bool = False
    resume: bool = True


class ExperimentError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    digest = hashlib.sha1(value.encode()).hexdigest()[:12]
    readable = "".join(character if character.isalnum() else "_" for character in value)[:80]
    return f"{readable}-{digest}"


def _write_log(path: Path, stdout: str, stderr: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stdout + ("\n--- STDERR ---\n" if stderr else "") + stderr, encoding="utf-8")


def _parse_trace_files(trace_dir: Path) -> tuple[list[str], list[list[str]]]:
    paths = sorted(trace_dir.glob("trace-*.tsv"))
    traces: list[list[str]] = []
    for path in paths:
        events: list[tuple[int, str]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            try:
                sequence = int(parts[0])
            except ValueError:
                continue
            events.append((sequence, parts[2]))
        if events:
            traces.append([node_id for _, node_id in sorted(events)])
    return [path.name for path in paths], traces


class ExperimentRunner:
    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.d4j = Defects4J(config.defects4j_dir)
        if not self.d4j.initialized:
            raise ExperimentError(
                "Defects4J is cloned but not initialized. Run `uv run patch-label init-defects4j` first."
            )
        self.helper_jar = build_helper(config.repo_root, config.state_dir)

    def run(self, example: Example, phases: list[Phase]) -> list[Path]:
        outputs: list[Path] = []
        for phase in phases:
            outputs.append(self.run_phase(example, phase))
        return outputs

    def run_phase(self, example: Example, phase: Phase) -> Path:
        output_dir = self.config.output_dir / example.dataset_version / example.key / phase
        final_file = output_dir / "experiment.json"
        if final_file.is_file() and self.config.resume and not self.config.fresh:
            return final_file
        if self.config.fresh and output_dir.exists():
            if not output_dir.resolve().is_relative_to(self.config.output_dir.resolve()):
                raise ExperimentError(f"Refusing to delete unmanaged output directory: {output_dir}")
            shutil.rmtree(output_dir)

        checkout = self.config.state_dir / "worktrees" / example.dataset_version / example.key / phase
        self.d4j.checkout(example, checkout, fresh=self.config.fresh)
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._metadata(checkout)

        patch_manifest: dict[str, Any] | None = None
        if phase == "patched":
            candidates = class_source_candidates(
                checkout,
                metadata["dir.src.classes"],
                metadata["classes.modified"],
            )
            patch_manifest = apply_context_patch(example.patch_file, candidates)
            write_json(output_dir / "patch-application.json", patch_manifest)

        compile_result = self.d4j.compile(checkout, timeout=self.config.compile_timeout)
        _write_log(output_dir / "compile.log", compile_result.stdout, compile_result.stderr)
        graph = self._build_graph(checkout, metadata, output_dir)
        tests, discovery_errors = self._discover_tests(checkout, metadata)
        if self.config.max_tests is not None:
            tests = tests[: self.config.max_tests]
        write_json(
            output_dir / "test-manifest.json",
            {
                "scope": self.config.test_scope,
                "count": len(tests),
                "tests": [test.to_dict() for test in tests],
                "discovery_errors": discovery_errors,
            },
        )

        runtime_dir = Path(tempfile.mkdtemp(prefix=f"patch-label-{example.key}-{phase}-"))
        try:
            runtime_jar = runtime_dir / "patch-label-agent.jar"
            shutil.copy2(self.helper_jar, runtime_jar)
            includes_file = runtime_dir / "includes.txt"
            includes_file.write_text("\n".join(metadata["classes.modified"]) + "\n", encoding="utf-8")
            test_results = [
                self._run_or_load_test(
                    checkout,
                    output_dir,
                    runtime_dir,
                    runtime_jar,
                    includes_file,
                    test,
                )
                for test in tests
            ]
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)

        static_paths, truncations = enumerate_static_paths(
            graph,
            max_loop_visits=self.config.max_loop_visits,
            max_paths_per_method=self.config.max_paths_per_method,
        )
        labels = label_graph(graph, static_paths, test_results)
        document = {
            "schema_version": "1.0",
            "generated_at_epoch_seconds": time.time(),
            "example": example.to_dict(),
            "phase": phase,
            "configuration": {
                "test_scope": self.config.test_scope,
                "max_tests": self.config.max_tests,
                "max_loop_visits": self.config.max_loop_visits,
                "max_paths_per_method": self.config.max_paths_per_method,
            },
            "metadata": metadata,
            "patch_application": patch_manifest,
            "graph": graph,
            **labels,
            "path_enumeration_truncations": truncations,
            "tests": test_results,
            "test_discovery_errors": discovery_errors,
            "summary": {
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
                "static_path_count": len(static_paths),
                "label_count": len(labels["labels"]),
                "test_count": len(test_results),
                "passing_test_count": sum(result["status"] == "true" for result in test_results),
                "failing_test_count": sum(result["status"] == "false" for result in test_results),
                "untested_node_count": len(labels["coverage"]["untested_nodes"]),
                "untested_edge_count": len(labels["coverage"]["untested_edges"]),
            },
        }
        write_json(final_file, document)
        return final_file

    def _metadata(self, checkout: Path) -> dict[str, Any]:
        names = [
            "classes.modified",
            "dir.src.classes",
            "dir.bin.classes",
            "dir.bin.tests",
            "cp.test",
            "tests.all",
            "tests.relevant",
            "tests.trigger",
        ]
        values = {name: self.d4j.export(checkout, name) for name in names}
        for name in ("classes.modified", "tests.all", "tests.relevant", "tests.trigger"):
            values[name] = split_exported_list(values[name])
        return values

    def _build_graph(
        self,
        checkout: Path,
        metadata: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        include_file = checkout / ".patch-label-classes.txt"
        graph_file = checkout / ".patch-label-graph.json"
        include_file.write_text("\n".join(metadata["classes.modified"]) + "\n", encoding="utf-8")
        try:
            result = run_command(
                [
                    "java",
                    "-cp",
                    str(self.helper_jar),
                    "patchlabel.cfg.GraphCli",
                    str(graph_file),
                    str(include_file),
                    metadata["dir.bin.classes"],
                ],
                cwd=checkout,
                timeout=600,
            )
            _write_log(output_dir / "graph.log", result.stdout, result.stderr)
            graph = json.loads(graph_file.read_text(encoding="utf-8"))
        finally:
            include_file.unlink(missing_ok=True)
            graph_file.unlink(missing_ok=True)
        if graph.get("missing_classes"):
            raise ExperimentError(
                "Compiled class files are missing for: " + ", ".join(graph["missing_classes"])
            )
        write_json(output_dir / "graph.json", graph)
        return graph

    def _discover_tests(
        self, checkout: Path, metadata: dict[str, Any]
    ) -> tuple[list[TestCase], list[dict[str, str]]]:
        property_name = {
            "all": "tests.all",
            "relevant": "tests.relevant",
            "trigger": "tests.trigger",
        }[self.config.test_scope]
        if property_name == "tests.trigger":
            tests: list[TestCase] = []
            for selector in metadata[property_name]:
                class_name, separator, method_name = selector.partition("::")
                tests.append(
                    TestCase(
                        selector=selector,
                        class_name=class_name,
                        method_name=method_name if separator else None,
                        display_name=selector,
                        granularity="method" if separator else "class",
                    )
                )
            return sorted(tests, key=lambda test: test.selector), []
        return discover_test_cases(
            self.helper_jar,
            checkout,
            metadata[property_name],
            binary_classes_dir=metadata["dir.bin.classes"],
            binary_tests_dir=metadata["dir.bin.tests"],
            test_classpath=metadata["cp.test"],
            timeout=self.config.discovery_timeout,
        )

    def _run_or_load_test(
        self,
        checkout: Path,
        output_dir: Path,
        runtime_dir: Path,
        runtime_jar: Path,
        includes_file: Path,
        test: TestCase,
    ) -> dict[str, Any]:
        stem = _safe_name(test.selector)
        result_file = output_dir / "tests" / f"{stem}.json.gz"
        if result_file.is_file() and self.config.resume and not self.config.fresh:
            return read_json_gz(result_file)

        trace_dir = runtime_dir / "traces" / stem
        if trace_dir.exists():
            shutil.rmtree(trace_dir)
        trace_dir.mkdir(parents=True)
        properties = runtime_dir / f"agent-{stem}.properties"
        properties.write_text(
            f"outputDir={trace_dir}\nincludesFile={includes_file}\nmaxEvents=5000000\n",
            encoding="utf-8",
        )
        option = f"-javaagent:{runtime_jar}={properties}"
        try:
            command_result, failures = self.d4j.test(
                checkout,
                test.selector,
                java_tool_options=option,
                timeout=self.config.test_timeout,
            )
        except CommandError as exc:
            command_result = exc.result
            failures = []
        trace_names, traces = _parse_trace_files(trace_dir)
        status: Literal["true", "false"] = (
            "true" if command_result.return_code == 0 and not failures else "false"
        )
        result = TestResult(
            test=test,
            status=status,
            return_code=command_result.return_code,
            duration_seconds=command_result.duration_seconds,
            failing_tests=failures,
            trace_files=trace_names,
            traces=traces,
            output_tail=command_result.output[-12000:],
        ).to_dict()
        _write_log(
            output_dir / "test-logs" / f"{stem}.log",
            command_result.stdout,
            command_result.stderr,
        )
        write_json_gz(result_file, result)
        return result
