from __future__ import annotations

import json
import stat

import pytest

from ax_browser_broker import identities


def test_write_slot_config_uses_local_proxy_without_secret(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    pool_config_dir = tmp_path / "pool-config"
    profile_dir = tmp_path / "profile"
    identity_file.write_text(
        json.dumps(
            {
                "identities": {
                    "linkedin-main": {
                        "slot": "pool-c",
                        "profile_dir": str(profile_dir),
                        "proxy_ref": "iproyal:linkedin-main",
                        "timezone": "America/New_York",
                        "lang": "en-US",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    proxy_file.write_text(
        json.dumps(
            {
                "proxies": {
                    "iproyal:linkedin-main": {
                        "scheme": "http",
                        "host": "proxy.example",
                        "port": 1234,
                        "username": "user-secret",
                        "password": "pass-secret",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)

    path = identities.write_slot_config("linkedin-main", 18801)

    text = path.read_text(encoding="utf-8")
    assert "PROXY_REF='iproyal:linkedin-main'" in text
    assert "PROXY_LOCAL_PORT=18801" in text
    assert "pass-secret" not in text
    assert "user-secret" not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_identity_rejects_unknown_slot(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    identity_file.write_text(
        json.dumps({"identities": {"bad": {"slot": "missing"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)

    with pytest.raises(identities.IdentityError):
        identities.load_identities()


def test_save_proxy_writes_secret_file_0600(tmp_path) -> None:
    path = tmp_path / "proxies.json"

    identities.save_proxy(
        "iproyal:linkedin-main",
        {"scheme": "http", "host": "proxy.example", "port": 1234, "username": "u", "password": "p"},
        path,
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["proxies"]["iproyal:linkedin-main"]["host"] == "proxy.example"


def test_activate_identity_writes_slot_config_and_launches_profile(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    pool_config_dir = tmp_path / "pool-config"
    profile_dir = tmp_path / "chrome-openpaper"
    identity_file.write_text(
        json.dumps(
            {
                "identities": {
                    "chrome-openpaper": {
                        "slot": "pool-a",
                        "profile_dir": str(profile_dir),
                        "lang": "en-US",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    proxy_file.write_text(json.dumps({"proxies": {}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", tmp_path / "browser-pool")
    monkeypatch.setattr(identities, "_healthy", lambda _port: True)
    monkeypatch.setattr(identities.subprocess, "run", lambda args, check=False: calls.append(args) or type("Result", (), {"returncode": 1})())

    result = identities.activate_identity("chrome-openpaper")

    assert result["identity_id"] == "chrome-openpaper"
    assert result["active"] is True
    assert profile_dir.exists()
    assert (pool_config_dir / "pool-a.env").exists()
    assert [str(tmp_path / "browser-pool" / "bin" / "launch_chrome.sh"), "pool-a", "9223"] in calls
