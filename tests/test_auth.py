from __future__ import annotations

import json

from ax_browser_broker import auth


def test_auth_request_lifecycle_uses_state_file(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "PUBLIC_AUTH_BASE_URL", "")

    request = auth.create_auth_request("tester", "https://example.com/login")
    assert request["status"] == "pending"
    assert request["mode"] == "vnc"
    assert request["portal_url"].endswith("/auth/" + request["token"])
    assert request["portal_url"] == request["local_portal_url"]

    listed = auth.list_auth_requests()
    assert request["token"] in listed["requests"]

    complete = auth.complete_auth_request(request["token"])
    assert complete["status"] == "complete"

    raw = json.loads(state_file.read_text())
    assert raw["requests"][request["token"]]["status"] == "complete"
    assert raw["requests"][request["token"]]["mode"] == "vnc"


def test_auth_request_uses_public_portal_url_when_configured(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "PUBLIC_AUTH_BASE_URL", "https://browser.example.com/")

    request = auth.create_auth_request("tester", "https://example.com/login")

    assert request["portal_url"] == f"https://browser.example.com/auth/{request['token']}"
    assert request["local_portal_url"] == f"http://127.0.0.1:{auth.BROKER_PORT}/auth/{request['token']}"


def test_novnc_url_uses_public_url_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(auth, "PUBLIC_NOVNC_BASE_URL", "https://browser.example.com")

    assert auth.novnc_url(6081) == "https://browser.example.com/vnc.html?autoconnect=1&resize=remote"


def test_novnc_url_falls_back_to_localhost(monkeypatch) -> None:
    monkeypatch.setattr(auth, "PUBLIC_NOVNC_BASE_URL", "")

    assert auth.novnc_url(6081) == "http://127.0.0.1:6081/vnc.html?autoconnect=1&resize=remote"


def test_current_auth_vnc_returns_running_session_password(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    password_file = tmp_path / "vnc.passwd"
    password_file.write_text("secret-pass\n", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "PUBLIC_NOVNC_BASE_URL", "https://browser.example.com")

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {
        "mode": "identity",
        "identity_id": "work-main",
        "display": ":870",
        "websocket_port": 6081,
        "vnc_port": 5901,
        "password_file": str(password_file),
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.current_auth_vnc(request["token"])

    assert result is not None
    assert result["password"] == "secret-pass"
    assert result["websocket_url"] == "https://browser.example.com/vnc.html?autoconnect=1&resize=remote"
    assert result["identity_id"] == "work-main"


def test_current_auth_vnc_rejects_expired_request_and_removes_password(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    password_file = tmp_path / "vnc.passwd"
    password_file.write_text("secret-pass\n", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    terminated = []
    monkeypatch.setattr(auth, "_terminate_process_group", lambda pid: terminated.append(pid) or True)
    monkeypatch.setattr(auth, "_clear_auth_maintenance", lambda slots: slots)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["expires_at"] = 1
    data["requests"][request["token"]]["vnc"] = {
        "display": ":870",
        "websocket_port": 6081,
        "vnc_port": 5901,
        "password_file": str(password_file),
        "x11vnc_pid": 123,
        "websockify_pid": 456,
        "maintenance_slots": ["pool-a"],
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.current_auth_vnc(request["token"])

    assert result is None
    assert not password_file.exists()
    updated = json.loads(state_file.read_text())
    assert updated["requests"][request["token"]]["status"] == "expired"
    assert updated["requests"][request["token"]]["vnc"]["stopped_at"] >= 1
    assert updated["requests"][request["token"]]["vnc"]["stopped_pids"] == [123, 456]
    assert updated["requests"][request["token"]]["vnc"]["cleared_maintenance_slots"] == ["pool-a"]
    assert terminated == [123, 456]


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


def test_start_auth_vnc_recreates_password_after_restart_cleanup(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    old_password_file = tmp_path / "old.passwd"
    old_password_file.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "_authenticated_x_display", lambda: (":99", None))
    monkeypatch.setattr(auth.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth, "_terminate_process_group", lambda _pid: True)

    popen_calls = []

    class FakePopen:
        next_pid = 1000

        def __init__(self, args, **_kwargs):
            self.args = args
            self.pid = FakePopen.next_pid
            FakePopen.next_pid += 1
            popen_calls.append(args)
            if args[0] == "/usr/bin/x11vnc":
                password_path = args[args.index("-passwdfile") + 1]
                assert auth.Path(password_path).exists()

    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {
        "x11vnc_pid": 123,
        "websockify_pid": 456,
        "password_file": str(old_password_file),
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.start_auth_vnc(request["token"])

    assert not old_password_file.exists()
    assert result["password"]
    assert any(call[0] == "/usr/bin/x11vnc" for call in popen_calls)
    assert any(call[0] == "/usr/bin/websockify" for call in popen_calls)


def test_auth_request_can_target_identity(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "require_identity", lambda identity_id: identity_id)

    request = auth.create_auth_request(
        "tester", "https://accounts.google.com", identity_id="chrome-openpaper", mode="vnc"
    )

    assert request["identity_id"] == "chrome-openpaper"
    assert request["mode"] == "vnc"
    raw = json.loads(state_file.read_text())
    assert raw["requests"][request["token"]]["identity_id"] == "chrome-openpaper"
    assert raw["requests"][request["token"]]["mode"] == "vnc"


def test_auth_request_normalizes_and_validates_mode(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)

    request = auth.create_auth_request("tester", "https://example.com", mode="lease-control")

    assert request["mode"] == "lease_control"
    try:
        auth.create_auth_request("tester", "https://example.com", mode="invalid")
    except auth.AuthError as error:
        assert "mode must be lease_control or vnc" in str(error)
    else:
        raise AssertionError("invalid auth mode was accepted")


def test_stop_auth_vnc_terminates_identity_auth_process_groups(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    maintenance_dir = tmp_path / "maintenance"
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "BROWSER_POOL_MAINTENANCE_DIR", maintenance_dir)
    terminated = []
    monkeypatch.setattr(auth, "_terminate_process_group", lambda pid: terminated.append(pid) or True)

    request = auth.create_auth_request("tester", "https://example.com")
    marker = maintenance_dir / "pool-b.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}\n", encoding="utf-8")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {
        "x11vnc_pid": 123,
        "websockify_pid": 456,
        "chrome_pid": 789,
        "xvfb_pid": 987,
        "maintenance_slots": ["pool-b"],
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.stop_auth_vnc(request["token"])

    assert terminated == [123, 456, 789, 987]
    assert result["stopped"] == [123, 456, 789, 987]
    assert result["cleared_maintenance_slots"] == ["pool-b"]
    assert not marker.exists()


def test_stop_auth_vnc_removes_password_file_when_helper_termination_fails(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "auth_requests.json"
    password_file = tmp_path / "vnc.passwd"
    password_file.write_text("secret\n", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", state_file)
    monkeypatch.setattr(auth, "_terminate_process_group", lambda _pid: False)

    request = auth.create_auth_request("tester", "https://example.com")
    data = json.loads(state_file.read_text())
    data["requests"][request["token"]]["vnc"] = {
        "x11vnc_pid": 123,
        "websockify_pid": 456,
        "password_file": str(password_file),
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    result = auth.stop_auth_vnc(request["token"])

    assert result["stopped"] == []
    assert not password_file.exists()


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
    password_file = auth.ROOT / "state" / "vnc" / f"{request['token']}.passwd"

    try:
        auth.start_auth_vnc(request["token"])
    except auth.AuthError as error:
        assert "refused" in str(error)
    else:
        raise AssertionError("expected identity start failure")

    assert not password_file.exists()


def test_identity_auth_partial_start_failure_terminates_started_helpers(tmp_path, monkeypatch) -> None:
    class Identity:
        identity_id = "chrome-openpaper"
        profile_dir = tmp_path / "chrome-openpaper"
        lang = "en-US"
        slot = "pool-b"

    class FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    pids = iter([111, 222, 333])
    terminated = []
    killed_pool_pids = []

    def fake_popen(args, **_kwargs):
        if str(args[0]).endswith("websockify"):
            raise OSError("websockify failed")
        return FakeProc(next(pids))

    monkeypatch.setattr(auth, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(auth, "pool_status", lambda: {"leases": {}})
    monkeypatch.setattr(auth, "read_slot_config", lambda slot_name: {"IDENTITY_ID": "chrome-openpaper"} if slot_name == "pool-b" else {})
    monkeypatch.setattr(auth, "BROWSER_POOL_MAINTENANCE_DIR", tmp_path / "maintenance")
    monkeypatch.setattr(auth.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(auth, "_find_free_display", lambda: ":870")
    monkeypatch.setattr(
        auth,
        "_process_rows",
        lambda: [
            (444, f"/usr/bin/google-chrome-stable --headless=new --user-data-dir={tmp_path / 'chrome-openpaper'} --remote-debugging-port=9224"),
            (555, "python3 unrelated --user-data-dir=/tmp/chrome-openpaper --remote-debugging-port=9224"),
        ],
    )
    monkeypatch.setattr(auth, "_terminate_pids", lambda pids: killed_pool_pids.extend(pids))
    monkeypatch.setattr(auth.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth, "_terminate_process_group", lambda pid: terminated.append(pid) or True)

    try:
        auth._start_identity_auth_vnc(
            {"identity_id": "chrome-openpaper", "url": "https://example.com"},
            6081,
            5901,
            tmp_path / "passwd",
            tmp_path / "auth.log",
        )
    except OSError as error:
        assert "websockify failed" in str(error)
    else:
        raise AssertionError("expected partial start failure")

    assert terminated == [333, 222, 111]
    assert killed_pool_pids == [444, 444]
    assert not (tmp_path / "maintenance" / "pool-b.json").exists()


def test_identity_auth_starts_proxy_forwarder_for_proxied_identity(tmp_path, monkeypatch) -> None:
    class Identity:
        identity_id = "work-main"
        profile_dir = tmp_path / "work-main"
        lang = "en-US"
        slot = "pool-c"
        proxy_ref = "residential:work-main"

    class FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    popen_calls = []
    pids = iter([111, 222, 333, 444, 555])

    def fake_popen(args, **_kwargs):
        popen_calls.append(args)
        return FakeProc(next(pids))

    monkeypatch.setattr(auth, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(auth, "pool_status", lambda: {"leases": {}})
    monkeypatch.setattr(auth, "read_slot_config", lambda _slot_name: {})
    broker_root = tmp_path / "broker"
    (broker_root / "bin").mkdir(parents=True)
    (broker_root / "bin" / "ax-proxy-forwarder").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(auth, "ROOT", broker_root)
    monkeypatch.setattr(auth, "BROWSER_POOL_MAINTENANCE_DIR", tmp_path / "maintenance")
    monkeypatch.setattr(auth.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(auth, "_find_free_display", lambda: ":870")
    monkeypatch.setattr(auth, "_find_free_tcp_port", lambda: 18901)
    monkeypatch.setattr(auth, "_process_rows", lambda: [])
    monkeypatch.setattr(auth, "_terminate_pids", lambda _pids: None)
    monkeypatch.setattr(auth.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)

    result = auth._start_identity_auth_vnc(
        {"identity_id": "work-main", "url": "https://example.com"},
        6081,
        5901,
        tmp_path / "passwd",
        tmp_path / "auth.log",
    )

    assert result["proxy_pid"] == 222
    assert result["proxy_local_port"] == 18901
    assert any(str(call[0]).endswith("/bin/ax-proxy-forwarder") for call in popen_calls)
    chrome_call = next(call for call in popen_calls if call[0] == "/usr/bin/google-chrome-stable")
    assert "--proxy-server=http://127.0.0.1:18901" in chrome_call
