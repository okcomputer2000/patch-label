from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class PatchApplyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Hunk:
    header: str
    lines: tuple[str, ...]

    @property
    def old_lines(self) -> tuple[str, ...]:
        return tuple(line[1:] for line in self.lines if line[:1] in {" ", "-"})

    @property
    def new_lines(self) -> tuple[str, ...]:
        return tuple(line[1:] for line in self.lines if line[:1] in {" ", "+"})


def parse_unified_patch(patch_file: Path) -> list[Hunk]:
    text = patch_file.read_text(encoding="utf-8")
    hunks: list[Hunk] = []
    header: str | None = None
    lines: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        if raw_line.startswith("@@ "):
            if header is not None:
                hunks.append(Hunk(header, tuple(lines)))
            header = raw_line.rstrip("\r\n")
            if HUNK_RE.match(header) is None:
                raise PatchApplyError(f"Unsupported hunk header in {patch_file}: {header}")
            lines = []
        elif header is not None:
            if raw_line.startswith("\\ No newline at end of file"):
                continue
            if raw_line[:1] not in {" ", "+", "-"}:
                raise PatchApplyError(f"Unsupported patch line in {patch_file}: {raw_line!r}")
            lines.append(raw_line)
    if header is not None:
        hunks.append(Hunk(header, tuple(lines)))
    if not hunks:
        raise PatchApplyError(f"No hunks found in {patch_file}")
    return hunks


def _normalized(line: str) -> str:
    return " ".join(line.strip().split())


def _find_matches(source: list[str], needle: tuple[str, ...], normalized: bool) -> list[int]:
    if not needle:
        return []
    expected = [_normalized(line) for line in needle] if normalized else list(needle)
    matches: list[int] = []
    for start in range(0, len(source) - len(needle) + 1):
        candidate = source[start : start + len(needle)]
        actual = [_normalized(line) for line in candidate] if normalized else candidate
        if actual == expected:
            matches.append(start)
    return matches


def apply_context_patch(patch_file: Path, source_files: list[Path]) -> dict[str, object]:
    hunks = parse_unified_patch(patch_file)
    contents = {
        source_file: source_file.read_text(encoding="utf-8").splitlines(keepends=True)
        for source_file in source_files
        if source_file.is_file()
    }
    if not contents:
        raise PatchApplyError("No candidate Java source files were found")

    applied: list[dict[str, object]] = []
    touched: set[Path] = set()
    for hunk_number, hunk in enumerate(hunks, start=1):
        candidates: list[tuple[Path, int, str]] = []
        for source_file, source in contents.items():
            candidates.extend((source_file, start, "exact") for start in _find_matches(source, hunk.old_lines, False))
        if not candidates:
            for source_file, source in contents.items():
                candidates.extend(
                    (source_file, start, "whitespace-normalized")
                    for start in _find_matches(source, hunk.old_lines, True)
                )
        if len(candidates) != 1:
            detail = ", ".join(f"{path}:{start + 1} ({mode})" for path, start, mode in candidates)
            raise PatchApplyError(
                f"Hunk {hunk_number} must match exactly one location; found {len(candidates)}"
                + (f": {detail}" if detail else "")
            )
        source_file, start, mode = candidates[0]
        source = contents[source_file]
        source[start : start + len(hunk.old_lines)] = hunk.new_lines
        touched.add(source_file)
        applied.append(
            {
                "hunk": hunk_number,
                "header": hunk.header,
                "source_file": str(source_file),
                "line": start + 1,
                "match_mode": mode,
            }
        )

    for source_file in touched:
        source_file.write_text("".join(contents[source_file]), encoding="utf-8")
    return {
        "patch_file": str(patch_file),
        "touched_files": sorted(str(path) for path in touched),
        "hunks": applied,
    }


def class_source_candidates(checkout: Path, source_dir: str, class_names: list[str]) -> list[Path]:
    root = checkout / source_dir
    candidates: list[Path] = []
    for class_name in class_names:
        outer_name = class_name.split("$", 1)[0]
        candidate = root / Path(*outer_name.split(".")).with_suffix(".java")
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)
    if candidates:
        return candidates
    return sorted(root.rglob("*.java"))
