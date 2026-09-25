from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from .models import CommandResult


class CommandError(RuntimeError):
    def __init__(self, message: str, result: CommandResult):
        super().__init__(message)
        self.result = result


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> CommandResult:
    command = tuple(str(arg) for arg in args)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = CommandResult(
            args=command,
            return_code=124,
            stdout=stdout,
            stderr=stderr + f"\nTimed out after {timeout} seconds.",
            duration_seconds=time.monotonic() - started,
        )
        raise CommandError(f"Command timed out: {' '.join(command)}", result) from exc

    result = CommandResult(
        args=command,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.monotonic() - started,
    )
    if check and result.return_code != 0:
        raise CommandError(
            f"Command failed with exit code {result.return_code}: {' '.join(command)}",
            result,
        )
    return result

