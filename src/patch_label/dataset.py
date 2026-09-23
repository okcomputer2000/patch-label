from __future__ import annotations

import re
from pathlib import Path

from .models import Example

EXAMPLE_RE = re.compile(r"^(?P<project>[A-Za-z][A-Za-z0-9]*)-(?P<bug_id>[0-9]+)$")


class DatasetError(RuntimeError):
    pass


def discover_examples(dataset_dir: Path) -> list[Example]:
    dataset_dir = dataset_dir.resolve()
    if not dataset_dir.is_dir():
        raise DatasetError(f"Dataset directory does not exist: {dataset_dir}")

    examples: list[Example] = []
    for patch_file in dataset_dir.glob("*/*/thinkrepair.patch"):
        match = EXAMPLE_RE.fullmatch(patch_file.parent.name)
        if match is None:
            raise DatasetError(f"Invalid example directory name: {patch_file.parent}")
        examples.append(
            Example(
                dataset_version=patch_file.parent.parent.name,
                project=match.group("project"),
                bug_id=int(match.group("bug_id")),
                directory=patch_file.parent.resolve(),
                patch_file=patch_file.resolve(),
            )
        )

    if not examples:
        raise DatasetError(f"No thinkrepair.patch files found below {dataset_dir}")
    return sorted(examples, key=lambda item: (item.dataset_version, item.project, item.bug_id))


def select_examples(examples: list[Example], selectors: list[str]) -> list[Example]:
    if not selectors:
        return examples
    wanted = {selector.casefold() for selector in selectors}
    selected = [
        example
        for example in examples
        if example.key.casefold() in wanted
        or f"{example.dataset_version}/{example.key}".casefold() in wanted
    ]
    found = {
        value
        for example in selected
        for value in (example.key.casefold(), f"{example.dataset_version}/{example.key}".casefold())
    }
    missing = sorted(wanted - found)
    if missing:
        raise DatasetError(f"Unknown example selector(s): {', '.join(missing)}")
    return selected

