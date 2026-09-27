from pathlib import Path

from patch_label.dataset import discover_examples, missing_versioned_bugs, select_examples
from patch_label.models import Example
from patch_label.patching import parse_unified_patch


def test_discovers_all_repository_examples() -> None:
    examples = discover_examples(Path("thinkrepair-patch-diffs"))

    assert len(examples) == 205
    assert {example.dataset_version for example in examples} == {"D4JV1.2", "D4JV2.0"}
    assert any(example.key == "Compress-44" for example in examples)


def test_selects_versioned_example() -> None:
    examples = discover_examples(Path("thinkrepair-patch-diffs"))

    selected = select_examples(examples, ["D4JV2.0/Compress-44"])

    assert [example.key for example in selected] == ["Compress-44"]


def test_all_thinkrepair_patches_are_supported_unified_diffs() -> None:
    examples = discover_examples(Path("thinkrepair-patch-diffs"))

    hunk_counts = [len(parse_unified_patch(example.patch_file)) for example in examples]

    assert min(hunk_counts) >= 1


def test_versioned_catalog_rejects_missing_or_inactive_bug(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    v2 = tmp_path / "v2"
    (v1 / "framework/projects/Time").mkdir(parents=True)
    (v2 / "framework/projects/Compress").mkdir(parents=True)
    (v1 / "framework/projects/Time/commit-db").write_text("20,buggy,fixed\n", encoding="utf-8")
    (v2 / "framework/projects/Compress/active-bugs.csv").write_text(
        "bug.id,revision.id.buggy\n44,buggy\n", encoding="utf-8"
    )
    examples = [
        Example("D4JV1.2", "Time", 20, tmp_path, tmp_path / "patch"),
        Example("D4JV2.0", "Compress", 44, tmp_path, tmp_path / "patch"),
        Example("D4JV2.0", "Compress", 45, tmp_path, tmp_path / "patch"),
    ]
    assert missing_versioned_bugs(examples, {"D4JV1.2": v1, "D4JV2.0": v2}) == [
        "D4JV2.0/Compress-45"
    ]
