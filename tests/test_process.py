import sys
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
