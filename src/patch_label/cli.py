from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import re
import shutil
import sys
from pathlib import Path

from .dataset import DatasetError, discover_examples, select_examples
from .defects4j import Defects4J
from .experiment import ExperimentConfig, ExperimentRunner
from .io import read_json, write_json
from .java_helper import build_helper
from .process import CommandError, run_command
from .report import write_human_reports


def _default_root() -> Path:
    return Path.cwd()


def _common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, default=_default_root())
    parser.add_argument("--dataset-dir", type=Path, default=Path("thinkrepair-patch-diffs"))
    parser.add_argument("--defects4j-dir", type=Path, default=Path("tools/defects4j"))
    parser.add_argument("--state-dir", type=Path, default=Path(".patch-label"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patch-label",
        description="Build and label CFG paths for ThinkRepair Defects4J examples.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local experiment prerequisites")
    _common_paths(doctor)
    doctor.add_argument("--json", action="store_true", dest="as_json")

    catalog = subparsers.add_parser("catalog", help="List or export all patch examples")
    _common_paths(catalog)
    catalog.add_argument("--output", type=Path)

    init = subparsers.add_parser("init-defects4j", help="Initialize the cloned Defects4J checkout")
    _common_paths(init)
    init.add_argument("--skip-perl-deps", action="store_true")

    helper = subparsers.add_parser("build-helper", help="Build the ASM/JUnit Java helper")
    _common_paths(helper)
    helper.add_argument("--force", action="store_true")

    report = subparsers.add_parser(
        "report", help="Render the report, complete CFG, and path labels"
    )
    _common_paths(report)
    report.add_argument("experiment", type=Path)

    run = subparsers.add_parser("run", help="Run one or more experiment examples")
    _common_paths(run)
    run.add_argument("--example", action="append", default=[], help="Project-ID or VERSION/Project-ID")
    run.add_argument("--phase", choices=("buggy", "patched", "both"), default="both")
    run.add_argument("--test-scope", choices=("all", "relevant", "trigger"), default="all")
    run.add_argument("--max-tests", type=int)
    run.add_argument("--compile-timeout", type=float, default=1800)
    run.add_argument("--test-timeout", type=float, default=600)
    run.add_argument("--discovery-timeout", type=float, default=600)
    run.add_argument("--max-loop-visits", type=int, default=2)
    run.add_argument("--max-paths-per-method", type=int, default=1000)
    run.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Run this many examples concurrently; phases within one example stay sequential",
    )
    run.add_argument("--fresh", action="store_true")
    run.add_argument("--no-resume", action="store_false", dest="resume")
    run.add_argument("--keep-going", action="store_true")
    run.set_defaults(resume=True)
    return parser


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    root = args.repo_root.resolve()
    return {
        "repo_root": root,
        "dataset_dir": _resolve(root, args.dataset_dir).resolve(),
        "defects4j_dir": _resolve(root, args.defects4j_dir).resolve(),
        "state_dir": _resolve(root, args.state_dir).resolve(),
        "output_dir": _resolve(root, args.output_dir).resolve(),
    }


def command_doctor(args: argparse.Namespace) -> int:
    paths = _paths(args)
    checks: list[dict[str, object]] = []
    for executable in ("uv", "git", "java", "javac", "perl", "svn"):
        location = shutil.which(executable)
        checks.append({"name": executable, "ok": location is not None, "detail": location or "not found"})
    perl_installer = shutil.which("cpanm") or shutil.which("cpan")
    checks.append(
        {
            "name": "Perl module installer",
            "ok": perl_installer is not None,
            "detail": perl_installer or "neither cpanm nor cpan found",
        }
    )
    checks.extend(
        [
            {
                "name": "dataset",
                "ok": paths["dataset_dir"].is_dir(),
                "detail": str(paths["dataset_dir"]),
            },
            {
                "name": "defects4j clone",
                "ok": (paths["defects4j_dir"] / ".git").is_dir(),
                "detail": str(paths["defects4j_dir"]),
            },
        ]
    )
    try:
        d4j = Defects4J(paths["defects4j_dir"])
        initialized = d4j.initialized
    except Exception as exc:  # reported as a diagnostic, not hidden
        initialized = False
        detail = str(exc)
    else:
        detail = "project repositories available" if initialized else "run init-defects4j"
    checks.append({"name": "defects4j initialized", "ok": initialized, "detail": detail})
    if 'd4j' in locals():
        checks.append(
            {
                "name": "defects4j Java 11",
                "ok": d4j.java_home is not None,
                "detail": str(d4j.java_home) if d4j.java_home else "set PATCH_LABEL_JAVA_HOME",
            }
        )
    payload = {"ok": all(bool(check["ok"]) for check in checks), "checks": checks}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            marker = "OK" if check["ok"] else "MISSING"
            print(f"[{marker:7}] {check['name']}: {check['detail']}")
    return 0 if payload["ok"] else 1


def command_catalog(args: argparse.Namespace) -> int:
    paths = _paths(args)
    examples = discover_examples(paths["dataset_dir"])
    payload = {"count": len(examples), "examples": [example.to_dict() for example in examples]}
    if args.output:
        output = _resolve(paths["repo_root"], args.output)
        write_json(output, payload)
        print(output)
    else:
        for example in examples:
            print(f"{example.dataset_version}/{example.key}")
        print(f"Total: {len(examples)}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    paths = _paths(args)
    d4j_dir = paths["defects4j_dir"]
    if not (d4j_dir / ".git").is_dir():
        raise RuntimeError(f"Defects4J clone not found: {d4j_dir}")
    if not args.skip_perl_deps:
        cpanm = shutil.which("cpanm")
        if cpanm is not None:
            dependency_command = [cpanm, "--installdeps", "."]
        else:
            cpan = shutil.which("cpan")
            if cpan is None:
                raise RuntimeError("Neither cpanm nor cpan is installed; cannot install Perl dependencies")
            cpanfile = (d4j_dir / "cpanfile").read_text(encoding="utf-8")
            modules = re.findall(r"requires\s+['\"]([^'\"]+)['\"]", cpanfile)
            dependency_command = [cpan, "-T", *modules]
        result = run_command(
            dependency_command,
            cwd=d4j_dir,
            env={"PERL_MM_USE_DEFAULT": "1", "NONINTERACTIVE": "1"},
            timeout=3600,
        )
        sys.stdout.write(result.output)
    d4j = Defects4J(d4j_dir)
    result = run_command(
        ["bash", "init.sh"],
        cwd=d4j_dir,
        env=d4j.environment(d4j_dir),
        timeout=7200,
    )
    sys.stdout.write(result.output)
    return 0


def command_build_helper(args: argparse.Namespace) -> int:
    paths = _paths(args)
    helper = build_helper(paths["repo_root"], paths["state_dir"], force=args.force)
    print(helper)
    return 0


def command_report(args: argparse.Namespace) -> int:
    paths = _paths(args)
    experiment = _resolve(paths["repo_root"], args.experiment).resolve()
    document = read_json(experiment)
    if document.get("schema_version") != "2.0":
        raise RuntimeError("Readable reports require a schema 2.0 experiment")
    report, cfg, path_table = write_human_reports(document, experiment.parent)
    print(report)
    print(cfg)
    print(path_table)
    return 0


def command_run(args: argparse.Namespace) -> int:
    paths = _paths(args)
    examples = select_examples(discover_examples(paths["dataset_dir"]), args.example)
    phases = ["buggy", "patched"] if args.phase == "both" else [args.phase]
    if args.jobs < 1:
        raise RuntimeError("--jobs must be at least 1")
    config = ExperimentConfig(
        **paths,
        test_scope=args.test_scope,
        max_tests=args.max_tests,
        compile_timeout=args.compile_timeout,
        test_timeout=args.test_timeout,
        discovery_timeout=args.discovery_timeout,
        max_loop_visits=args.max_loop_visits,
        max_paths_per_method=args.max_paths_per_method,
        fresh=args.fresh,
        resume=args.resume,
    )
    runner = ExperimentRunner(config)
    failures: list[dict[str, str]] = []

    def run_example(index: int, example: object) -> list[Path]:
        dataset_version = getattr(example, "dataset_version")
        key = getattr(example, "key")
        print(f"[START {index}/{len(examples)}] {dataset_version}/{key}", flush=True)
        return runner.run(example, phases)  # type: ignore[arg-type]

    def record_result(index: int, example: object, outputs: list[Path]) -> None:
        dataset_version = getattr(example, "dataset_version")
        key = getattr(example, "key")
        print(f"[DONE  {index}/{len(examples)}] {dataset_version}/{key}", flush=True)
        for output in outputs:
            print(f"  {output}", flush=True)

    def record_failure(example: object, exc: Exception) -> None:
        key = str(getattr(example, "key"))
        failure = {"example": key, "error": str(exc)}
        if isinstance(exc, CommandError):
            output_tail = exc.result.output[-12000:]
            if output_tail:
                failure["output_tail"] = output_tail
                print(output_tail, file=sys.stderr, flush=True)
        failures.append(failure)
        print(f"FAILED {key}: {exc}", file=sys.stderr, flush=True)

    if args.jobs == 1:
        for index, example in enumerate(examples, start=1):
            try:
                outputs = run_example(index, example)
            except Exception as exc:
                record_failure(example, exc)
                if not args.keep_going:
                    raise
            else:
                record_result(index, example, outputs)
    else:
        worker_count = min(args.jobs, len(examples))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="patch-label-example",
        ) as executor:
            futures: dict[Future[list[Path]], tuple[int, object]] = {
                executor.submit(run_example, index, example): (index, example)
                for index, example in enumerate(examples, start=1)
            }
            for future in as_completed(futures):
                index, example = futures[future]
                try:
                    outputs = future.result()
                except Exception as exc:
                    record_failure(example, exc)
                    if not args.keep_going:
                        for pending in futures:
                            pending.cancel()
                        raise
                else:
                    record_result(index, example, outputs)
    if failures:
        write_json(
            paths["output_dir"] / "failures.json",
            sorted(failures, key=lambda item: item["example"]),
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "doctor": command_doctor,
        "catalog": command_catalog,
        "init-defects4j": command_init,
        "build-helper": command_build_helper,
        "report": command_report,
        "run": command_run,
    }
    try:
        return commands[args.command](args)
    except (DatasetError, CommandError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, CommandError):
            print(exc.result.output[-12000:], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
