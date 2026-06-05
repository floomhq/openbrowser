from __future__ import annotations

import io
import json
import urllib.error

from ax_browser_broker import cli


def test_cli_loads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENBROWSER_API_KEY", "env-key")

    assert cli._load_api_key() == "env-key"


def test_cli_loads_api_key_from_tokens_file(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "openbrowser_api_keys.json"
    key_file.write_text(json.dumps({"tokens": {"default": "file-key"}}), encoding="utf-8")
    monkeypatch.delenv("OPENBROWSER_API_KEY", raising=False)
    monkeypatch.delenv("AX_OPENBROWSER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "OPENBROWSER_API_KEYS_FILE", key_file)

    assert cli._load_api_key() == "file-key"


def test_cli_auth_uses_identity_id_not_profile(monkeypatch, capsys) -> None:
    calls = []

    def fake_request(method, path, body=None, auth=False):
        calls.append((method, path, body, auth))
        return {"ok": True}

    monkeypatch.setattr(cli, "_request", fake_request)

    assert cli.main(["auth", "https://lovable.dev", "--identity", "work-main", "--owner", "pytest"]) == 0

    assert calls == [
        (
            "POST",
            "/openbrowser/v1/auth/request",
            {
                "owner": "pytest",
                "identity_id": "work-main",
                "url": "https://lovable.dev",
                "reason": "login_required",
            },
            True,
        )
    ]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_cli_open_control_sends_one_step_handoff_payload(monkeypatch, capsys) -> None:
    calls = []

    def fake_request(method, path, body=None, auth=False):
        calls.append((method, path, body, auth))
        if path == "/openbrowser/v1/open":
            return {"lease": {"lease_id": "lease-cli"}, "navigation": {"url": "https://lovable.dev/dashboard"}}
        if path == "/openbrowser/v1/browser/snapshot":
            return {"title": "Home | Lovable", "url": "https://lovable.dev/dashboard", "bodyText": "ok", "elements": [], "slot": "pool-b"}
        if path == "/openbrowser/v1/browser/screenshot":
            return {"path": "/tmp/shot.png", "base64": "image-data"}
        if path == "/openbrowser/v1/lease-control/request":
            return {"portal_url": "https://browser.example.com/auth/lease-control/tok"}
        raise AssertionError(path)

    monkeypatch.setattr(cli, "_request", fake_request)

    assert (
        cli.main(
            [
                "open",
                "https://lovable.dev",
                "--identity",
                "chrome-work",
                "--owner",
                "pytest-open",
                "--control",
                "--screenshot",
            ]
        )
        == 0
    )

    assert calls == [
        (
            "POST",
            "/openbrowser/v1/open",
            {
                "owner": "pytest-open",
                "identity_id": "chrome-work",
                "url": "https://lovable.dev",
                "ttl_seconds": 900,
            },
            True,
        ),
        ("POST", "/openbrowser/v1/browser/snapshot", {"lease_id": "lease-cli"}, True),
        ("POST", "/openbrowser/v1/browser/screenshot", {"lease_id": "lease-cli", "full_page": False}, True),
        (
            "POST",
            "/openbrowser/v1/lease-control/request",
            {"owner": "pytest-open", "lease_id": "lease-cli", "ttl_seconds": 900},
            True,
        ),
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["portal_url"].endswith("/tok")
    assert output["snapshot"]["title"] == "Home | Lovable"
    assert "base64" not in output["screenshot"]


def test_cli_prints_compact_http_errors(monkeypatch, capsys) -> None:
    def fake_request(_request, timeout=None):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8767/openbrowser/v1/auth/request",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"detail":"Identity not found: work-main"}'),
        )

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_request)
    monkeypatch.setattr(cli, "_load_api_key", lambda: "test-key")

    assert cli.main(["auth", "https://example.com", "--identity", "work-main"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "HTTP 400: Identity not found: work-main"
