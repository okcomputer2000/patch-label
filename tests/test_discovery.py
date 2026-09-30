from pathlib import Path

from patch_label import test_discovery
from patch_label.models import CommandResult


def test_missing_methods_do_not_become_invalid_class_selector(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        test_discovery,
        "run_command",
        lambda *args, **kwargs: CommandResult(("java",), 0, "", "", 0.01),
    )
    cases, errors, skips = test_discovery.discover_test_cases(
        tmp_path / "helper.jar", tmp_path, ["sample.NoMethods"],
        binary_classes_dir="classes", binary_tests_dir="tests",
        test_classpath="", timeout=1,
    )

    assert cases == []
    assert errors[0]["class_name"] == "sample.NoMethods"
    assert skips == []


def test_ignored_class_is_skipped_without_invalid_method(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        test_discovery,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            ("java",), 0, "SKIP\tsample.Ignored\tJUnit @Ignore\n", "", 0.01
        ),
    )
    cases, errors, skips = test_discovery.discover_test_cases(
        tmp_path / "helper.jar", tmp_path, ["sample.Ignored"],
        binary_classes_dir="classes", binary_tests_dir="tests",
        test_classpath="", timeout=1,
    )
    assert cases == []
    assert errors == []
    assert skips == [{"class_name": "sample.Ignored", "reason": "JUnit @Ignore"}]


def test_invalid_method_is_reported_not_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        test_discovery,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            ("java",), 0, "TEST\tsample.Bad\tsample.Bad\tsample.Bad\n", "", 0.01
        ),
    )
    cases, errors, skips = test_discovery.discover_test_cases(
        tmp_path / "helper.jar", tmp_path, ["sample.Bad"],
        binary_classes_dir="classes", binary_tests_dir="tests",
        test_classpath="", timeout=1,
    )
    assert cases == []
    assert any("Invalid discovered method" in error["message"] for error in errors)
    assert skips == []
