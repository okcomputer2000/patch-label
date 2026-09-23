from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import CommandResult, Example
from .process import run_command


class Defects4JError(RuntimeError):
    pass


class Defects4J:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.executable = self.root / "framework" / "bin" / "defects4j"
        if not self.executable.is_file():
            raise Defects4JError(f"Defects4J executable not found: {self.executable}")

    @property
    def initialized(self) -> bool:
        project_repos = self.root / "project_repos"
        if not project_repos.is_dir():
            return False
        return any(
            child.is_dir() and ((child / "HEAD").is_file() or (child / ".git").exists())
            for child in project_repos.iterdir()
        )

    @property
    def java_home(self) -> Path | None:
        configured = os.environ.get("PATCH_LABEL_JAVA_HOME")
        candidates = [Path(configured)] if configured else []
        javac = shutil.which("javac")
        if javac:
            candidates.append(Path(os.path.realpath(javac)).parent.parent)
        existing = os.environ.get("JAVA_HOME")
        if existing:
            candidates.append(Path(existing))
        for candidate in candidates:
            release = candidate / "release"
            if release.is_file() and 'JAVA_VERSION="11' in release.read_text(encoding="utf-8", errors="replace"):
                return candidate
        return None

    def _base_env(self) -> dict[str, str]:
        environment = {
            "TZ": "America/Los_Angeles",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "D4J_HOME": str(self.root),
        }
        java_home = self.java_home
        if java_home is not None:
            environment["JAVA_HOME"] = str(java_home)
            environment["PATH"] = str(java_home / "bin") + os.pathsep + os.environ.get("PATH", "")
        local_perl = Path(os.environ.get("HOME", "")) / "perl5" / "lib" / "perl5"
        if local_perl.is_dir():
            stable_perl = self.root.parent.parent / ".patch-label" / "perl5"
            stable_perl.parent.mkdir(parents=True, exist_ok=True)
            if not stable_perl.exists():
                try:
                    stable_perl.symlink_to(local_perl, target_is_directory=True)
                except OSError:
                    shutil.copytree(local_perl, stable_perl)
            environment["PATCH_LABEL_PERL_ROOT"] = str(stable_perl)
        return environment

    def invoke(
        self,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> CommandResult:
        merged = self.environment(cwd, env)
        return run_command(
            [str(self.executable), *args],
            cwd=cwd,
            env=merged,
            timeout=timeout,
            check=check,
        )

    def environment(
        self, cwd: Path, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        merged = self._base_env()
        if extra:
            merged.update(extra)
        perl_root = merged.pop("PATCH_LABEL_PERL_ROOT", None)
        if perl_root:
            relative_perl = os.path.relpath(perl_root, cwd)
            architecture_perl = os.path.join(relative_perl, "x86_64-linux-gnu-thread-multi")
            merged["PERL5LIB"] = os.pathsep.join((relative_perl, architecture_perl))
        return merged

    def checkout(self, example: Example, destination: Path, *, fresh: bool) -> None:
        destination = destination.resolve()
        if fresh and destination.exists():
            self._assert_managed_checkout(destination)
            shutil.rmtree(destination)
        if destination.exists() and (destination / ".defects4j.config").is_file():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.invoke(
            ["checkout", "-p", example.project, "-v", f"{example.bug_id}b", "-w", str(destination)],
            cwd=destination.parent,
            timeout=1800,
        )

    @staticmethod
    def _assert_managed_checkout(destination: Path) -> None:
        normalized = str(destination).replace("\\", "/")
        if "/.patch-label/" not in f"/{normalized.strip('/')}/" or not (destination / ".defects4j.config").is_file():
            raise Defects4JError(f"Refusing to delete unmanaged directory: {destination}")

    def export(self, checkout: Path, property_name: str) -> str:
        result = self.invoke(["export", "-p", property_name], cwd=checkout, timeout=300)
        return result.stdout.strip()

    def compile(self, checkout: Path, *, timeout: float) -> CommandResult:
        return self.invoke(["compile"], cwd=checkout, timeout=timeout)

    def test(
        self,
        checkout: Path,
        selector: str,
        *,
        java_tool_options: str,
        timeout: float,
    ) -> tuple[CommandResult, list[str]]:
        failing_file = checkout / "failing_tests"
        failing_file.unlink(missing_ok=True)
        existing = os.environ.get("JAVA_TOOL_OPTIONS", "").strip()
        options = " ".join(value for value in (existing, java_tool_options) if value)
        existing_ant = os.environ.get("ANT_OPTS", "").strip()
        ant_options = " ".join(value for value in (existing_ant, java_tool_options) if value)
        result = self.invoke(
            ["test", "-t", selector],
            cwd=checkout,
            env={"JAVA_TOOL_OPTIONS": options, "ANT_OPTS": ant_options},
            timeout=timeout,
            check=False,
        )
        failing_tests: list[str] = []
        if failing_file.is_file():
            for line in failing_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("--- "):
                    failing_tests.append(line[4:].strip())
        return result, failing_tests


def split_exported_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", "\n").splitlines() if item.strip()]


def split_classpath(value: str) -> list[str]:
    return [part for part in value.split(os.pathsep) if part]
