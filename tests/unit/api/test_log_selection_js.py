"""The exchange log's selection block in app.js (gui-m2-implementation.md, M3b main-thread fix,
Task 42), checked by ``scripts/gui_log_check.js`` under node: the window, the four kinds of
absent rows' counts, the clear / pause boundaries and the Older / Newer / filter-change helpers,
plus a seeded randomized comparison with a brute-force reference.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
CHECK = REPO / "scripts" / "gui_log_check.js"


def test_the_log_selection_block_passes_its_node_check():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    run = subprocess.run([node, str(CHECK)], capture_output=True, text=True, timeout=120, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "LOGCHECK PASS" in run.stdout, run.stdout
