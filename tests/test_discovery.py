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
    cases, errors = test_discovery.discover_test_cases(
        tmp_path / "helper.jar", tmp_path, ["sample.NoMethods"],
        binary_classes_dir="classes", binary_tests_dir="tests",
        test_classpath="", timeout=1,
    )

    assert cases == []
    assert errors[0]["class_name"] == "sample.NoMethods"
