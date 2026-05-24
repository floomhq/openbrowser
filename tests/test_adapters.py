from __future__ import annotations

import subprocess
import sqlite3

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


def test_openbrowser_status_uses_broker_profile_and_releases(tmp_path, monkeypatch, capsys) -> None:
    profile = tmp_path / "profile"
    cookie_dir = profile / "Default"
    cookie_dir.mkdir(parents=True)
    with sqlite3.connect(cookie_dir / "Cookies") as connection:
        connection.execute("create table cookies (host_key text, name text)")
        connection.executemany(
            "insert into cookies (host_key, name) values (?, ?)",
            [
                (".www.linkedin.com", "li_at"),
                (".www.linkedin.com", "JSESSIONID"),
                (".linkedin.com", "bcookie"),
                (".www.linkedin.com", "bscookie"),
                (".linkedin.com", "lidc"),
            ],
        )
    released = []
    monkeypatch.setattr(
        adapters,
        "_lease",
        lambda owner, identity_id=None: {
            "lease_id": "lease-2",
            "name": "pool-c",
            "identity_id": identity_id,
            "cdp": "http://127.0.0.1:9225",
            "port": 9225,
            "profile_dir": str(profile),
        },
    )
    monkeypatch.setattr(adapters, "_release", lambda lease_id: released.append(lease_id))

    assert adapters.run_openbrowser(["--identity", "linkedin-main", "status"]) == 0
    output = capsys.readouterr().out

    assert '"adapter": "openbrowser"' in output
    assert '"missing": []' in output
    assert released == ["lease-2"]
