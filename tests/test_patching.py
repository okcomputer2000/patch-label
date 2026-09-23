from pathlib import Path

import pytest

from patch_label.patching import PatchApplyError, apply_context_patch


def test_applies_placeholder_patch_by_unique_context(tmp_path: Path) -> None:
    source = tmp_path / "Actual.java"
    source.write_text(
        "class Actual {\n    int f(int value) {\n        return value;\n    }\n}\n",
        encoding="utf-8",
    )
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Fake.java\n"
        "+++ repaired/Fake.java\n"
        "@@ -1,3 +1,3 @@\n"
        "     int f(int value) {\n"
        "-        return value;\n"
        "+        return value + 1;\n"
        "     }\n",
        encoding="utf-8",
    )

    manifest = apply_context_patch(patch, [source])

    assert "return value + 1;" in source.read_text(encoding="utf-8")
    assert manifest["hunks"][0]["match_mode"] == "exact"


def test_rejects_ambiguous_context(tmp_path: Path) -> None:
    sources = []
    for name in ("One.java", "Two.java"):
        source = tmp_path / name
        source.write_text("class X {\n    int f() { return 1; }\n}\n", encoding="utf-8")
        sources.append(source)
    patch = tmp_path / "thinkrepair.patch"
    patch.write_text(
        "--- original/Fake.java\n"
        "+++ repaired/Fake.java\n"
        "@@ -1 +1 @@\n"
        "-    int f() { return 1; }\n"
        "+    int f() { return 2; }\n",
        encoding="utf-8",
    )

    with pytest.raises(PatchApplyError, match="found 2"):
        apply_context_patch(patch, sources)

