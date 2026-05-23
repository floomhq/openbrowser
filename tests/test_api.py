from __future__ import annotations

from fastapi.testclient import TestClient

from ax_browser_broker import api, auth


def test_auth_portal_escapes_request_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "AUTH_STATE_FILE", tmp_path / "auth.json")

    request = auth.create_auth_request("<owner>", "https://example.com/?x=<script>")
    client = TestClient(api.app)
    response = client.get("/auth/" + request["token"])

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;owner&gt;" in response.text
