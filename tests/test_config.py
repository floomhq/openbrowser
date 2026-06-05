from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_config_probe(env: dict[str, str], code: str) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    merged["PYTHONPATH"] = str(Path.cwd())
    return subprocess.run([sys.executable, "-c", code], env=merged, text=True, capture_output=True, check=True)


def test_slots_can_be_isolated_with_port_start_and_count(tmp_path) -> None:
    result = _run_config_probe(
        {
            "OPENBROWSER_BROKER_ROOT": str(tmp_path / "root"),
            "OPENBROWSER_BROWSER_POOL_DIR": str(tmp_path / "pool"),
            "OPENBROWSER_SLOT_PORT_START": "19323",
            "OPENBROWSER_SLOT_COUNT": "2",
        },
        "import json; from ax_browser_broker.config import SLOTS; print(json.dumps([(s.name, s.port) for s in SLOTS]))",
    )

    assert json.loads(result.stdout) == [["pool-a", 19323], ["pool-b", 19324]]


def test_slots_can_be_isolated_with_explicit_slot_list(tmp_path) -> None:
    result = _run_config_probe(
        {
            "OPENBROWSER_BROKER_ROOT": str(tmp_path / "root"),
            "OPENBROWSER_BROWSER_POOL_DIR": str(tmp_path / "pool"),
            "OPENBROWSER_SLOTS": "qa:21001,worker:21002",
        },
        "import json; from ax_browser_broker.config import SLOTS; print(json.dumps([(s.name, s.port) for s in SLOTS]))",
    )

    assert json.loads(result.stdout) == [["qa", 21001], ["worker", 21002]]


def test_ensure_dirs_installs_packaged_pool_scripts(tmp_path) -> None:
    result = _run_config_probe(
        {
            "OPENBROWSER_BROKER_ROOT": str(tmp_path / "root"),
            "OPENBROWSER_BROWSER_POOL_DIR": str(tmp_path / "pool"),
        },
        """
import json
from ax_browser_broker.config import BROWSER_POOL_DIR, ensure_dirs
ensure_dirs()
launch = BROWSER_POOL_DIR / 'bin' / 'launch_chrome.sh'
supervisor = BROWSER_POOL_DIR / 'bin' / 'supervisor.sh'
print(json.dumps({
    'launch_exists': launch.exists(),
    'launch_executable': bool(launch.stat().st_mode & 0o111),
    'supervisor_exists': supervisor.exists(),
    'supervisor_executable': bool(supervisor.stat().st_mode & 0o111),
}))
""",
    )

    assert json.loads(result.stdout) == {
        "launch_exists": True,
        "launch_executable": True,
        "supervisor_exists": True,
        "supervisor_executable": True,
    }
