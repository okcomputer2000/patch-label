from __future__ import annotations

import os
import re
from pathlib import Path

from .models import TestCase
from .process import CommandError, run_command

PARAMETER_SUFFIX_RE = re.compile(r"\[[^]]*]$")


class TestDiscoveryError(RuntimeError):
    pass


def discover_test_cases(
    helper_jar: Path,
    checkout: Path,
    test_classes: list[str],
    *,
    binary_classes_dir: str,
    binary_tests_dir: str,
    test_classpath: str,
    timeout: float,
) -> tuple[list[TestCase], list[dict[str, str]]]:
    class_list = checkout / ".patch-label-test-classes.txt"
    class_list.write_text("\n".join(test_classes) + "\n", encoding="utf-8")
    classpath_entries = [
        str(helper_jar),
        str(checkout / binary_tests_dir),
        str(checkout / binary_classes_dir),
        test_classpath,
    ]
    classpath = os.pathsep.join(entry for entry in classpath_entries if entry)
    try:
        result = run_command(
            ["java", "-cp", classpath, "patchlabel.discovery.TestDiscovery", str(class_list)],
            cwd=checkout,
            timeout=timeout,
        )
    except CommandError as exc:
        raise TestDiscoveryError(exc.result.output[-8000:]) from exc
    finally:
        class_list.unlink(missing_ok=True)

    cases: dict[str, TestCase] = {}
    errors: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if not parts:
            continue
        if parts[0] == "TEST" and len(parts) == 4:
            class_name, raw_method, display = parts[1:]
            method_name = PARAMETER_SUFFIX_RE.sub("", raw_method)
            selector = f"{class_name}::{method_name}"
            cases.setdefault(
                selector,
                TestCase(
                    selector=selector,
                    class_name=class_name,
                    method_name=method_name,
                    display_name=display,
                ),
            )
        elif parts[0] == "ERROR" and len(parts) >= 3:
            errors.append({"class_name": parts[1], "message": parts[2]})

    discovered_classes = {case.class_name for case in cases.values()}
    for class_name in test_classes:
        if class_name not in discovered_classes:
            selector = class_name
            cases[selector] = TestCase(
                selector=selector,
                class_name=class_name,
                method_name=None,
                display_name=class_name,
                granularity="class",
            )
            errors.append(
                {
                    "class_name": class_name,
                    "message": "No leaf test methods discovered; using class-level fallback",
                }
            )
    return sorted(cases.values(), key=lambda case: case.selector), errors
