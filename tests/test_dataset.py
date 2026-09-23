from pathlib import Path

from patch_label.dataset import discover_examples, select_examples
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
