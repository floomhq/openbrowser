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
