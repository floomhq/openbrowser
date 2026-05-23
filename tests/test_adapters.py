from __future__ import annotations

import subprocess

from ax_browser_broker import adapters


def test_browser_use_adapter_releases_on_process_failure(monkeypatch) -> None:
    released = []
    monkeypatch.setattr(
        adapters,
        "_lease",
        lambda owner, identity_id=None: {
            "lease_id": "lease-1",
            "name": "pool-a",
            "cdp": "http://127.0.0.1:9223",
            "port": 9223,
            "profile_dir": "/tmp/profile",
        },
    )
    monkeypatch.setattr(adapters, "_release", lambda lease_id: released.append(lease_id))
    monkeypatch.setattr(subprocess, "call", lambda command, env=None: 17)

    assert adapters.run_browser_use(["--json", "state"]) == 17
    assert released == ["lease-1"]
