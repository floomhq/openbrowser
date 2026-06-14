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
                    "work-main": {
                        "slot": "pool-c",
                        "profile_dir": str(profile_dir),
                        "proxy_ref": "residential:work-main",
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
                    "residential:work-main": {
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

    path = identities.write_slot_config("work-main", 18801)

    text = path.read_text(encoding="utf-8")
    assert "PROXY_REF='residential:work-main'" in text
    assert "PROXY_LOCAL_PORT=18801" in text
    assert "pass-secret" not in text
    assert "user-secret" not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_slot_config_enables_sync_for_imported_mac_profile(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    pool_config_dir = tmp_path / "pool-config"
    profile_dir = tmp_path / "chrome-work"
    identity_file.write_text(
        json.dumps(
            {
                "identities": {
                    "chrome-work": {
                        "slot": "pool-b",
                        "profile_dir": str(profile_dir),
                        "source": {"type": "mac-chrome-profile", "profile_dir_name": "Profile 3"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    proxy_file.write_text(json.dumps({"proxies": {}}), encoding="utf-8")
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)

    path = identities.write_slot_config("chrome-work")

    assert "CHROME_DISABLE_SYNC='0'" in path.read_text(encoding="utf-8")


def test_load_identity_rejects_unknown_slot(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    identity_file.write_text(
        json.dumps({"identities": {"bad": {"slot": "missing"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)

    with pytest.raises(identities.IdentityError):
        identities.load_identities()


def test_load_identity_accepts_auto_slot(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    identity_file.write_text(
        json.dumps({"identities": {"chrome-openpaper": {"slot": "auto"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)

    loaded = identities.load_identities()

    assert loaded["chrome-openpaper"].slot == "auto"


def test_load_identities_discovers_pool_slot_identity(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    identity_file.write_text(json.dumps({"identities": {}}), encoding="utf-8")
    pool_config_dir = tmp_path / "pool-config"
    pool_config_dir.mkdir()
    profile_dir = tmp_path / "profiles" / "chrome-floom"
    profile_dir.mkdir(parents=True)
    (pool_config_dir / "pool-c.env").write_text(
        "\n".join(
            [
                "IDENTITY_ID='chrome-floom'",
                f"PROFILE_DIR={str(profile_dir)!r}",
                "CHROME_LANG='en-US'",
                "TZ='Europe/Berlin'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", tmp_path / "pool")

    loaded = identities.load_identities()

    assert loaded["chrome-floom"].identity_id == "chrome-floom"
    assert loaded["chrome-floom"].slot == "auto"
    assert loaded["chrome-floom"].profile_dir == profile_dir
    assert loaded["chrome-floom"].timezone == "Europe/Berlin"


def test_write_slot_config_requires_concrete_slot_for_auto_identity(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    profile_dir = tmp_path / "chrome-openpaper"
    identity_file.write_text(
        json.dumps({"identities": {"chrome-openpaper": {"slot": "auto", "profile_dir": str(profile_dir)}}}),
        encoding="utf-8",
    )
    proxy_file.write_text(json.dumps({"proxies": {}}), encoding="utf-8")
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)

    with pytest.raises(identities.IdentityError):
        identities.write_slot_config("chrome-openpaper")


def test_save_proxy_writes_secret_file_0600(tmp_path) -> None:
    path = tmp_path / "proxies.json"

    identities.save_proxy(
        "residential:work-main",
        {"scheme": "http", "host": "proxy.example", "port": 1234, "username": "u", "password": "p"},
        path,
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["proxies"]["residential:work-main"]["host"] == "proxy.example"


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
    monkeypatch.setattr(identities.subprocess, "run", lambda args, check=False: calls.append((args, check)) or type("Result", (), {"returncode": 1})())

    result = identities.activate_identity("chrome-openpaper")

    assert result["identity_id"] == "chrome-openpaper"
    assert result["active"] is True
    assert profile_dir.exists()
    assert (pool_config_dir / "pool-a.env").exists()
    assert ([str(tmp_path / "browser-pool" / "bin" / "launch_chrome.sh"), "pool-a", "9223"], True) in calls


def test_activate_identity_waits_for_proxy_ready(tmp_path, monkeypatch) -> None:
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
                        "proxy_ref": "residential:work-main",
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
                    "residential:work-main": {
                        "scheme": "http",
                        "host": "proxy.example",
                        "port": 1234,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []
    proxy_checks = []
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", tmp_path / "browser-pool")
    monkeypatch.setattr(identities, "_healthy", lambda _port: True)
    monkeypatch.setattr(identities.subprocess, "run", lambda args, check=False: calls.append((args, check)) or type("Result", (), {"returncode": 1})())

    def proxy_ready(slot_name: str) -> bool:
        proxy_checks.append(slot_name)
        return len(proxy_checks) > 1

    monkeypatch.setattr(identities, "_slot_proxy_ready", proxy_ready)

    result = identities.activate_identity("chrome-openpaper")

    assert result["active"] is True
    assert proxy_checks == ["pool-a", "pool-a"]


def test_activate_identity_fails_when_proxy_never_ready(tmp_path, monkeypatch) -> None:
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
                        "proxy_ref": "residential:work-main",
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
                    "residential:work-main": {
                        "scheme": "http",
                        "host": "proxy.example",
                        "port": 1234,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", tmp_path / "browser-pool")
    monkeypatch.setattr(identities, "_healthy", lambda _port: True)
    monkeypatch.setattr(identities, "_slot_proxy_ready", lambda _slot_name: False)
    monkeypatch.setattr(identities.time, "sleep", lambda _seconds: None)
    start = [100.0]

    def fake_time() -> float:
        start[0] += 1.0
        return start[0]

    monkeypatch.setattr(identities.time, "time", fake_time)
    monkeypatch.setattr(identities.subprocess, "run", lambda _args, check=False: type("Result", (), {"returncode": 1})())

    with pytest.raises(identities.IdentityError, match="did not become healthy"):
        identities.activate_identity("chrome-openpaper")


def test_activate_auto_identity_clears_duplicate_slot_config(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    pool_config_dir = tmp_path / "pool-config"
    browser_pool = tmp_path / "browser-pool"
    profile_dir = tmp_path / "chat-main"
    pool_config_dir.mkdir()
    (pool_config_dir / "pool-a.env").write_text(
        f"IDENTITY_ID='chat-main'\nPROFILE_DIR={str(profile_dir)!r}\nCHROME_LANG='en-US'\n",
        encoding="utf-8",
    )
    identity_file.write_text(
        json.dumps({"identities": {"chat-main": {"slot": "auto", "profile_dir": str(profile_dir)}}}),
        encoding="utf-8",
    )
    proxy_file.write_text(json.dumps({"proxies": {}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", browser_pool)
    monkeypatch.setattr(identities, "_healthy", lambda _port: True)
    monkeypatch.setattr(identities.subprocess, "run", lambda args, check=False: calls.append((args, check)) or type("Result", (), {"returncode": 1})())

    result = identities.activate_identity("chat-main", "pool-b")

    assert result["slot"] == "pool-b"
    assert not (pool_config_dir / "pool-a.env").exists()
    assert (pool_config_dir / "pool-b.env").exists()
    assert ([str(browser_pool / "bin" / "launch_chrome.sh"), "pool-a", "9223"], True) in calls
    assert ([str(browser_pool / "bin" / "launch_chrome.sh"), "pool-b", "9224"], True) in calls


def test_activate_identity_refuses_active_slot(tmp_path, monkeypatch) -> None:
    identity_file = tmp_path / "identities.json"
    proxy_file = tmp_path / "proxies.json"
    pool_config_dir = tmp_path / "pool-config"
    browser_pool = tmp_path / "browser-pool"
    (browser_pool / "state").mkdir(parents=True)
    (browser_pool / "state" / "leases.json").write_text(
        json.dumps({"leases": {"lease-1": {"name": "pool-a"}}}),
        encoding="utf-8",
    )
    identity_file.write_text(
        json.dumps({"identities": {"chrome-openpaper": {"slot": "auto", "profile_dir": str(tmp_path / "profile")}}}),
        encoding="utf-8",
    )
    proxy_file.write_text(json.dumps({"proxies": {}}), encoding="utf-8")
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "PROXIES_FILE", proxy_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", browser_pool)

    with pytest.raises(identities.IdentityError):
        identities.activate_identity("chrome-openpaper", "pool-a")


def test_invalidate_identity_replicas_clears_unleased_skips_leased(tmp_path, monkeypatch):
    from pathlib import Path

    identity_file = tmp_path / "identities.json"
    pool_config_dir = tmp_path / "pool-config"
    pool_config_dir.mkdir()
    browser_pool = tmp_path / "browser-pool"
    base_profile = browser_pool / "profiles" / "chrome-one"
    base_profile.mkdir(parents=True)
    replica_root = browser_pool / "profiles" / ".replicas" / "chrome-one"
    replica_a = replica_root / "pool-a"
    replica_b = replica_root / "pool-b"
    replica_a.mkdir(parents=True)
    replica_b.mkdir(parents=True)
    (replica_a / "marker").write_text("a", encoding="utf-8")
    (replica_b / "marker").write_text("b", encoding="utf-8")

    identity_file.write_text(
        json.dumps(
            {
                "identities": {
                    "chrome-one": {
                        "slot": "auto",
                        "profile_dir": str(base_profile),
                        "policy": {"max_parallel_sessions": 2},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", browser_pool)

    # pool-a: replica config, leased -> must be skipped (left intact).
    # pool-b: replica config, NOT leased -> config cleared + replica removed.
    slot_configs = {
        "pool-a": {"IDENTITY_ID": "chrome-one", "PROFILE_DIR": str(replica_a)},
        "pool-b": {"IDENTITY_ID": "chrome-one", "PROFILE_DIR": str(replica_b)},
    }
    for slot_name in ("pool-a", "pool-b"):
        (pool_config_dir / f"{slot_name}.env").write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr(identities, "active_identity_id", lambda slot_name: slot_configs.get(slot_name, {}).get("IDENTITY_ID"))
    monkeypatch.setattr(identities, "read_slot_config", lambda slot_name: slot_configs.get(slot_name, {}))
    monkeypatch.setattr(identities, "_slot_has_active_lease", lambda slot_name: slot_name == "pool-a")

    result = identities.invalidate_identity_replicas("chrome-one")

    assert result["skipped_leased_slots"] == ["pool-a"]
    assert result["cleared_slot_configs"] == ["pool-b"]
    assert str(replica_b) in result["removed_replicas"]
    # Leased slot left untouched.
    assert (pool_config_dir / "pool-a.env").exists()
    assert replica_a.exists()
    # Unleased slot wiped so next lease re-syncs from base.
    assert not (pool_config_dir / "pool-b.env").exists()
    assert not replica_b.exists()


def test_invalidate_identity_replicas_leaves_base_profile_alone(tmp_path, monkeypatch):
    identity_file = tmp_path / "identities.json"
    pool_config_dir = tmp_path / "pool-config"
    pool_config_dir.mkdir()
    browser_pool = tmp_path / "browser-pool"
    base_profile = browser_pool / "profiles" / "chrome-one"
    base_profile.mkdir(parents=True)

    identity_file.write_text(
        json.dumps(
            {
                "identities": {
                    "chrome-one": {"slot": "auto", "profile_dir": str(base_profile)}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(identities, "IDENTITIES_FILE", identity_file)
    monkeypatch.setattr(identities, "POOL_CONFIG_DIR", pool_config_dir)
    monkeypatch.setattr(identities, "BROWSER_POOL_DIR", browser_pool)
    # Slot points at the BASE profile (not a replica) -> must not be cleared/removed.
    (pool_config_dir / "pool-a.env").write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(identities, "active_identity_id", lambda slot_name: "chrome-one" if slot_name == "pool-a" else None)
    monkeypatch.setattr(identities, "read_slot_config", lambda slot_name: {"IDENTITY_ID": "chrome-one", "PROFILE_DIR": str(base_profile)} if slot_name == "pool-a" else {})
    monkeypatch.setattr(identities, "_slot_has_active_lease", lambda slot_name: False)

    result = identities.invalidate_identity_replicas("chrome-one")
    assert result["cleared_slot_configs"] == []
    assert result["removed_replicas"] == []
    assert (pool_config_dir / "pool-a.env").exists()
    assert base_profile.exists()
