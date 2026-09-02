"""Run the Stations overlay regression tests (Node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tests" / "js" / "test_stations_overlay.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_stations_overlay_hides_generic_missing_software_banner():
    result = subprocess.run(
        ["node", str(SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "overlay JS tests failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
