"""The CLI's own node tests (`npm test` in cli/), so `uv run pytest` covers them.
Skips without node or cli/node_modules."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "cli"


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None
                    or not (CLI / "node_modules").is_dir(),
                    reason="needs node, npm and cli/node_modules (npm ci --prefix cli)")
def test_cli_node_tests_pass():
    done = subprocess.run(["npm", "test", "--silent"], cwd=CLI, capture_output=True,
                          text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "# fail 0" in done.stdout
