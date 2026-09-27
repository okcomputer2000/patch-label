from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

from .models import Example

EXAMPLE_RE = re.compile(r"^(?P<project>[A-Za-z][A-Za-z0-9]*)-(?P<bug_id>[0-9]+)$")
PROJECT_REPOS = {
    "Chart": "jfreechart", "Cli": "commons-cli.git", "Closure": "closure-compiler.git",
    "Codec": "commons-codec.git", "Collections": "commons-collections.git",
    "Compress": "commons-compress.git", "Csv": "commons-csv.git", "Gson": "gson.git",
    "JacksonCore": "jackson-core.git", "JacksonDatabind": "jackson-databind.git",
    "JacksonXml": "jackson-dataformat-xml.git", "Jsoup": "jsoup.git",
    "JxPath": "commons-jxpath.git", "Lang": "commons-lang.git",
    "Math": "commons-math.git", "Mockito": "mockito.git", "Time": "joda-time.git",
}


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


def missing_versioned_bugs(examples: list[Example], version_dirs: dict[str, Path]) -> list[str]:
    """Check that each input bug is active in its matching Defects4J release."""
    catalog_cache: dict[tuple[str, str], set[int]] = {}
    missing: list[str] = []
    for example in examples:
        key = (example.dataset_version, example.project)
        if key not in catalog_cache:
            root = version_dirs.get(example.dataset_version)
            filename = "commit-db" if example.dataset_version == "D4JV1.2" else "active-bugs.csv"
            path = root / "framework" / "projects" / example.project / filename if root else None
            if path is None or not path.is_file():
                catalog_cache[key] = set()
            else:
                with path.open(encoding="utf-8", newline="") as stream:
                    catalog_cache[key] = {
                        int(row[0]) for row in csv.reader(stream)
                        if row and row[0].isdigit()
                    }
        if example.bug_id not in catalog_cache[key]:
            missing.append(f"{example.dataset_version}/{example.key}")
    return missing


def missing_versioned_revisions(examples: list[Example], version_dirs: dict[str, Path]) -> list[str]:
    """Verify both historic source revisions exist in the initialized repos."""
    rows_cache: dict[tuple[str, str], dict[int, tuple[str, str]]] = {}
    missing: list[str] = []
    for example in examples:
        key = (example.dataset_version, example.project)
        root = version_dirs.get(example.dataset_version)
        if root is None or example.project not in PROJECT_REPOS:
            missing.append(f"{example.dataset_version}/{example.key}: unsupported project")
            continue
        if key not in rows_cache:
            filename = "commit-db" if example.dataset_version == "D4JV1.2" else "active-bugs.csv"
            path = root / "framework" / "projects" / example.project / filename
            if not path.is_file():
                rows_cache[key] = {}
            else:
                with path.open(encoding="utf-8", newline="") as stream:
                    rows_cache[key] = {
                        int(row[0]): (row[1], row[2]) for row in csv.reader(stream)
                        if len(row) >= 3 and row[0].isdigit()
                    }
        revisions = rows_cache[key].get(example.bug_id)
        repo = root / "project_repos" / PROJECT_REPOS[example.project]
        if revisions is None or not repo.is_dir():
            missing.append(f"{example.dataset_version}/{example.key}: no revisions")
            continue
        for phase, revision in zip(("buggy", "fixed"), revisions):
            command = (["svnlook", "date", "-r", revision, str(repo)]
                       if example.project == "Chart" else
                       ["git", f"--git-dir={repo}", "cat-file", "-e", f"{revision}^{{commit}}"])
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            except OSError:
                result = None
            if result is None or result.returncode:
                missing.append(f"{example.dataset_version}/{example.key}: {phase} revision {revision}")
    return missing

