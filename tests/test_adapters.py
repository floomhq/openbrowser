from __future__ import annotations

import subprocess
import sqlite3
import urllib.error

from ax_browser_broker import adapters


def test_adapter_command_shape_redacts_freeform_args_and_flag_values() -> None:
    command = [
        "browser-use",
        "--password",
        "hunter2",
        "--prompt=login as alice with secret code",
        "navigate to private customer workspace",
    ]

    shaped = adapters._command_shape(command)

    assert shaped == ["browser-use", "--password", "[redacted]", "--prompt=[redacted]", "[redacted]"]
    assert "hunter2" not in " ".join(shaped)
    assert "customer" not in " ".join(shaped)


def test_lease_retries_transient_conflict(monkeypatch) -> None:
    attempts = []

    def fake_request(method, path, body):
        attempts.append((method, path, body))
        if len(attempts) < 3:
            raise urllib.error.HTTPError("http://127.0.0.1:8767/lease", 409, "Conflict", {}, None)
        return {"lease_id": "lease-1", "name": "pool-a"}

    monkeypatch.setattr(adapters, "_request", fake_request)
    monkeypatch.setattr(adapters.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(adapters.time, "monotonic", iter([0, 1, 2, 3]).__next__)

    assert adapters._lease("openbrowser")["lease_id"] == "lease-1"
    assert len(attempts) == 3


def test_lease_retries_broker_startup_connection_error(monkeypatch) -> None:
    attempts = []

    def fake_request(method, path, body):
        attempts.append((method, path, body))
        if len(attempts) < 2:
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return {"lease_id": "lease-2", "name": "pool-a"}

    monkeypatch.setattr(adapters, "_request", fake_request)
    monkeypatch.setattr(adapters.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(adapters.time, "monotonic", iter([0, 1, 2]).__next__)

    assert adapters._lease("openbrowser")["lease_id"] == "lease-2"
    assert len(attempts) == 2


def test_browser_use_adapter_releases_on_process_failure(monkeypatch) -> None:
    released = []
    events = []
    issues = []
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
    monkeypatch.setattr(adapters, "_safe_record_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(adapters, "_safe_report_issue", lambda **kwargs: issues.append(kwargs) or {"id": "issue-1"})
    monkeypatch.setattr(subprocess, "call", lambda command, env=None: 17)

    assert adapters.run_browser_use(["--json", "state"]) == 17
    assert released == ["lease-1"]
    assert [event["message"] for event in events] == [
        "browser-use adapter started",
        "browser-use adapter failed",
        "browser-use adapter issue filed",
    ]
    assert issues[0]["lease_id"] == "lease-1"


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
    events = []
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
    monkeypatch.setattr(adapters, "_safe_record_event", lambda **kwargs: events.append(kwargs))

    assert adapters.run_openbrowser(["--identity", "work-main", "status"]) == 0
    output = capsys.readouterr().out

    assert '"adapter": "openbrowser"' in output
    assert '"missing": []' in output
    assert released == ["lease-2"]
    assert [event["message"] for event in events] == ["OpenBrowser adapter started", "OpenBrowser status completed"]


def test_openbrowser_adapter_records_failure_and_issue(monkeypatch) -> None:
    released = []
    events = []
    issues = []
    monkeypatch.setattr(
        adapters,
        "_lease",
        lambda owner, identity_id=None: {
            "lease_id": "lease-3",
            "name": "pool-a",
            "identity_id": identity_id,
            "cdp": "http://127.0.0.1:9223",
            "port": 9223,
            "profile_dir": "/tmp/profile",
        },
    )
    monkeypatch.setattr(adapters, "_release", lambda lease_id: released.append(lease_id))
    monkeypatch.setattr(adapters, "_safe_record_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(adapters, "_safe_report_issue", lambda **kwargs: issues.append(kwargs) or {"id": "issue-2"})
    monkeypatch.setattr(subprocess, "call", lambda command, env=None: 9)

    assert adapters.run_openbrowser(["run"]) == 9

    assert released == ["lease-3"]
    assert events[0]["message"] == "OpenBrowser adapter started"
    assert events[1]["message"] == "OpenBrowser adapter failed"
    assert events[1]["data"]["exit_code"] == 9
    assert issues[0]["source"] == "openbrowser"
