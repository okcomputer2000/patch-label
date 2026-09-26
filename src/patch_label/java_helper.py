from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

from .process import run_command

ASM_VERSION = "9.8"
JUNIT_VERSION = "4.13.2"
HAMCREST_VERSION = "1.3"

DEPENDENCIES = {
    f"asm-{ASM_VERSION}.jar": f"https://repo1.maven.org/maven2/org/ow2/asm/asm/{ASM_VERSION}/asm-{ASM_VERSION}.jar",
    f"asm-tree-{ASM_VERSION}.jar": f"https://repo1.maven.org/maven2/org/ow2/asm/asm-tree/{ASM_VERSION}/asm-tree-{ASM_VERSION}.jar",
    f"junit-{JUNIT_VERSION}.jar": f"https://repo1.maven.org/maven2/junit/junit/{JUNIT_VERSION}/junit-{JUNIT_VERSION}.jar",
    f"hamcrest-core-{HAMCREST_VERSION}.jar": f"https://repo1.maven.org/maven2/org/hamcrest/hamcrest-core/{HAMCREST_VERSION}/hamcrest-core-{HAMCREST_VERSION}.jar",
}


class JavaHelperError(RuntimeError):
    pass


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "patch-label/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def build_helper(repo_root: Path, state_dir: Path, *, force: bool = False) -> Path:
    java_root = repo_root / "java"
    source_root = java_root / "src" / "main" / "java"
    source_files = sorted(source_root.rglob("*.java"))
    if not source_files:
        raise JavaHelperError(f"No Java helper sources found below {source_root}")

    cache_dir = state_dir / "java"
    lib_dir = cache_dir / "lib"
    classes_dir = cache_dir / "classes"
    helper_jar = cache_dir / "patch-label-agent.jar"
    lib_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = hashlib.sha256()
    for source_file in source_files:
        fingerprint.update(source_file.relative_to(repo_root).as_posix().encode())
        fingerprint.update(source_file.read_bytes())
    for name, url in DEPENDENCIES.items():
        fingerprint.update(name.encode())
        fingerprint.update(url.encode())
    fingerprint_file = cache_dir / "fingerprint"
    current = fingerprint.hexdigest()
    if not force and helper_jar.is_file() and fingerprint_file.read_text(encoding="utf-8").strip() == current:
        return helper_jar

    jars: list[Path] = []
    for name, url in DEPENDENCIES.items():
        jar = lib_dir / name
        if not jar.is_file():
            _download(url, jar)
        jars.append(jar)

    if classes_dir.exists():
        shutil.rmtree(classes_dir)
    classes_dir.mkdir(parents=True)
    classpath = _classpath(jars)
    run_command(
        [
            "javac",
            "--release",
            "8",
            "-encoding",
            "UTF-8",
            "-cp",
            classpath,
            "-d",
            str(classes_dir),
            *(str(path) for path in source_files),
        ],
        cwd=repo_root,
    )

    manifest = (
        "Manifest-Version: 1.0\n"
        "Premain-Class: patchlabel.trace.TraceAgent\n"
        "Agent-Class: patchlabel.trace.TraceAgent\n"
        "Can-Redefine-Classes: false\n"
        "Can-Retransform-Classes: false\n\n"
    )
    with zipfile.ZipFile(helper_jar, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("META-INF/MANIFEST.MF", manifest)
        seen = {"META-INF/MANIFEST.MF"}
        for class_file in sorted(classes_dir.rglob("*.class")):
            archive_name = class_file.relative_to(classes_dir).as_posix()
            output.write(class_file, archive_name)
            seen.add(archive_name)
        for jar in jars:
            with zipfile.ZipFile(jar) as dependency:
                for item in dependency.infolist():
                    name = item.filename
                    upper_name = name.upper()
                    if item.is_dir() or name in seen:
                        continue
                    if name == "module-info.class" or name.startswith("META-INF/versions/"):
                        continue
                    if upper_name.startswith("META-INF/") and upper_name.endswith((".SF", ".RSA", ".DSA")):
                        continue
                    output.writestr(name, dependency.read(item))
                    seen.add(name)
    fingerprint_file.write_text(current + "\n", encoding="utf-8")
    return helper_jar


def build_bootstrap_support(helper_jar: Path, destination: Path) -> Path:
    recorder_prefix = "patchlabel/trace/Recorder"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(helper_jar) as source:
        entries = [
            item for item in source.infolist()
            if item.filename == f"{recorder_prefix}.class"
            or item.filename.startswith(f"{recorder_prefix}$")
        ]
        if not entries:
            raise JavaHelperError(f"Recorder classes missing from helper JAR: {helper_jar}")
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
            for item in entries:
                output.writestr(item.filename, source.read(item))
    return destination


def install_runtime_support(helper_jar: Path, classes_dir: Path) -> list[Path]:
    recorder_prefix = "patchlabel/trace/Recorder"
    installed: list[Path] = []
    with zipfile.ZipFile(helper_jar) as source:
        entries = [
            item for item in source.infolist()
            if item.filename == f"{recorder_prefix}.class"
            or item.filename.startswith(f"{recorder_prefix}$")
        ]
        if not entries:
            raise JavaHelperError(f"Recorder classes missing from helper JAR: {helper_jar}")
        for item in entries:
            destination = classes_dir / item.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read(item))
            installed.append(destination)
    return installed


def _classpath(paths: list[Path]) -> str:
    import os

    return os.pathsep.join(str(path) for path in paths)
