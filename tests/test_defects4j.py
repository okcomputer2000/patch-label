from pathlib import Path

from patch_label.defects4j import Defects4J


def test_prepare_legacy_dependencies_uses_matching_local_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "defects4j"
    (root / "framework" / "bin").mkdir(parents=True)
    (root / "framework" / "bin" / "defects4j").touch()
    state = tmp_path / "state"
    dependencies = state / "dependencies"
    dependencies.mkdir(parents=True)
    for filename in ("commons-logging-1.0.3.jar", "commons-logging-1.0.4.jar",
                     "commons-discovery-0.2.jar"):
        (dependencies / filename).write_bytes(filename.encode())
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "build.xml").write_text(
        "commons-logging-1.0.4.jar\ncommons-discovery-0.2.jar\n",
        encoding="utf-8",
    )
    (checkout / "build.properties").write_text("custom=value\n", encoding="utf-8")

    Defects4J(root, state_dir=state).prepare_legacy_dependencies(checkout)

    properties = (checkout / "build.properties").read_text(encoding="utf-8")
    assert "custom=value\n" in properties
    assert f"commons-logging.jar={(dependencies / 'commons-logging-1.0.4.jar').as_uri()}\n" in properties
    assert f"commons-discovery.jar={(dependencies / 'commons-discovery-0.2.jar').as_uri()}\n" in properties
    assert "commons-logging-1.0.3.jar" not in properties
