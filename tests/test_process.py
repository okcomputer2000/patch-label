import sys
import os
import time
from pathlib import Path

import pytest

from patch_label.process import CommandError, run_command


def test_timeout_keeps_text_output(tmp_path: Path) -> None:
    with pytest.raises(CommandError) as caught:
        run_command(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(10)"],
            cwd=tmp_path,
            timeout=0.5,
        )
    assert caught.value.result.return_code == 124
    assert "ready" in caught.value.result.output
    assert "Timed out" in caught.value.result.output


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_timeout_kills_descendant_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)"
    )
    with pytest.raises(CommandError):
        run_command([sys.executable, "-c", script, str(pid_file)], cwd=tmp_path, timeout=0.5)
    pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(20):
        status = Path(f"/proc/{pid}/stat")
        try:
            process_state = status.read_text().split()[2]
        except FileNotFoundError:
            break
        if process_state == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("grandchild survived the command timeout")
