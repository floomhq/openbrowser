from __future__ import annotations

import json

from ax_browser_broker import auth


def test_auth_request_lifecycle_uses_state_file(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)

    request = auth.create_auth_request("tester", "https://example.com/login")
    assert request["status"] == "pending"
    assert request["portal_url"].endswith("/auth/" + request["token"])

    listed = auth.list_auth_requests()
    assert request["token"] in listed["requests"]

    complete = auth.complete_auth_request(request["token"])
    assert complete["status"] == "complete"

    raw = json.loads(state_file.read_text())
    assert raw["requests"][request["token"]]["status"] == "complete"


def test_stop_auth_vnc_removes_password_file(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    password_file = tmp_path / "vnc.passwd"
    password_file.write_text("secret\n", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {"password_file": str(password_file)}
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.stop_auth_vnc(request["token"])
    assert result["stopped"] == []
    assert not password_file.exists()


def test_stop_auth_vnc_terminates_recorded_process_groups(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    terminated = []
    monkeypatch.setattr(auth, "_terminate_process_group", lambda pid: terminated.append(pid) or True)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {"x11vnc_pid": 123, "websockify_pid": 456}
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.stop_auth_vnc(request["token"])

    assert terminated == [123, 456]
    assert result["stopped"] == [123, 456]


def test_auth_request_can_target_identity(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "require_identity", lambda identity_id: identity_id)

    request = auth.create_auth_request("tester", "https://accounts.google.com", identity_id="chrome-openpaper")

    assert request["identity_id"] == "chrome-openpaper"
    raw = json.loads(state_file.read_text())
    assert raw["requests"][request["token"]]["identity_id"] == "chrome-openpaper"


def test_stop_auth_vnc_terminates_identity_auth_process_groups(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    terminated = []
    monkeypatch.setattr(auth, "_terminate_process_group", lambda pid: terminated.append(pid) or True)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {
        "x11vnc_pid": 123,
        "websockify_pid": 456,
        "chrome_pid": 789,
        "xvfb_pid": 987,
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.stop_auth_vnc(request["token"])

    assert terminated == [123, 456, 789, 987]
    assert result["stopped"] == [123, 456, 789, 987]


def test_identity_auth_refuses_active_identity_lease(tmp_path, monkeypatch) -> None:
    class Identity:
        identity_id = "chrome-openpaper"
        profile_dir = tmp_path / "chrome-openpaper"

    monkeypatch.setattr(auth, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(auth.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        auth,
        "pool_status",
        lambda: {"leases": {"lease-1": {"identity_id": "chrome-openpaper"}}},
    )

    try:
        auth._start_identity_auth_vnc(
            {"identity_id": "chrome-openpaper", "url": "https://example.com"},
            6081,
            5901,
            tmp_path / "passwd",
            tmp_path / "auth.log",
        )
    except auth.AuthError as error:
        assert "actively leased" in str(error)
    else:
        raise AssertionError("expected active identity lease to be refused")


def test_start_auth_vnc_removes_password_file_when_identity_start_fails(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "require_identity", lambda identity_id: identity_id)
    monkeypatch.setattr(auth.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        auth,
        "_start_identity_auth_vnc",
        lambda *args, **kwargs: (_ for _ in ()).throw(auth.AuthError("refused")),
    )

    request = auth.create_auth_request("tester", "https://example.com", identity_id="chrome-openpaper")
    password_file = auth.Path("/root/ax-browser-broker/state/vnc") / f"{request['token']}.passwd"

    try:
        auth.start_auth_vnc(request["token"])
    except auth.AuthError as error:
        assert "refused" in str(error)
    else:
        raise AssertionError("expected identity start failure")

    assert not password_file.exists()
