from __future__ import annotations

import json
import stat

from ax_browser_broker import mac_chrome


def test_inventory_reads_local_state_metadata_only(tmp_path) -> None:
    chrome_dir = tmp_path / "Chrome"
    profile_dir = chrome_dir / "Profile 10"
    profile_dir.mkdir(parents=True)
    (chrome_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Profile 10": {
                            "name": "Rocketlist",
                            "user_name": "tech@example.com",
                            "gaia_name": "Rocketlist Tech",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = mac_chrome.inventory(chrome_dir)

    assert len(profiles) == 1
    assert profiles[0].profile_dir_name == "Profile 10"
    assert profiles[0].label == "Rocketlist"
    assert profiles[0].account_email == "tech@example.com"
    assert profiles[0].exists is True


def test_inventory_prefers_preferences_account_info(tmp_path) -> None:
    chrome_dir = tmp_path / "Chrome"
    profile_dir = chrome_dir / "Profile 3"
    profile_dir.mkdir(parents=True)
    (chrome_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Profile 3": {
                            "name": "Federico",
                            "user_name": "",
                            "gaia_name": "",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / "Preferences").write_text(
        json.dumps(
            {
                "profile": {"name": "Person 2"},
                "account_info": [{"email": "depontefede@example.com", "full_name": "Federico De Ponte"}],
            }
        ),
        encoding="utf-8",
    )

    profiles = mac_chrome.inventory(chrome_dir)

    assert profiles[0].label == "Person 2"
    assert profiles[0].account_email == "depontefede@example.com"
    assert profiles[0].gaia_name == "Federico De Ponte"


def test_import_profiles_creates_secret_free_identity_config(tmp_path, monkeypatch) -> None:
    chrome_dir = tmp_path / "Chrome"
    (chrome_dir / "Profile 1").mkdir(parents=True)
    (chrome_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Profile 1": {
                            "name": "OpenPaper",
                            "user_name": "openpaper@example.com",
                            "gaia_name": "Open Paper",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    identity_file = tmp_path / "identities.json"
    monkeypatch.setattr(mac_chrome, "BROWSER_POOL_DIR", tmp_path / "browser-pool")

    result = mac_chrome.import_profiles(chrome_dir, identity_file, slot="pool-b")

    assert result["created_count"] == 1
    assert result["safety"]["copied_raw_cookies"] is False
    data = json.loads(identity_file.read_text(encoding="utf-8"))
    identity = data["identities"]["chrome-openpaper"]
    assert identity["slot"] == "pool-b"
    assert identity["source"]["profile_dir_name"] == "Profile 1"
    assert identity["policy"]["secret_copying"] == "disabled"
    assert stat.S_IMODE(identity_file.stat().st_mode) == 0o600


def test_import_profiles_defaults_to_auto_slot(tmp_path, monkeypatch) -> None:
    chrome_dir = tmp_path / "Chrome"
    (chrome_dir / "Profile 1").mkdir(parents=True)
    (chrome_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "OpenPaper", "user_name": "openpaper@example.com"}}}}),
        encoding="utf-8",
    )
    identity_file = tmp_path / "identities.json"
    monkeypatch.setattr(mac_chrome, "BROWSER_POOL_DIR", tmp_path / "browser-pool")

    mac_chrome.import_profiles(chrome_dir, identity_file)

    data = json.loads(identity_file.read_text(encoding="utf-8"))
    assert data["identities"]["chrome-openpaper"]["slot"] == "auto"


def test_import_profiles_does_not_copy_raw_secret_databases(tmp_path, monkeypatch) -> None:
    chrome_dir = tmp_path / "Chrome"
    profile_dir = chrome_dir / "Profile 1"
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True)
    for filename in ("Cookies", "Login Data", "Login Data For Account"):
        (default_dir / filename).write_text("secret-db", encoding="utf-8")
    (chrome_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "OpenPaper", "user_name": "openpaper@example.com"}}}}),
        encoding="utf-8",
    )
    identity_file = tmp_path / "identities.json"
    browser_pool = tmp_path / "browser-pool"
    monkeypatch.setattr(mac_chrome, "BROWSER_POOL_DIR", browser_pool)

    mac_chrome.import_profiles(chrome_dir, identity_file)

    dest_profile = browser_pool / "profiles" / "chrome-openpaper"
    assert not dest_profile.exists()
    assert "secret-db" not in identity_file.read_text(encoding="utf-8")


def test_mirror_profiles_copies_safe_profile_data_without_secret_databases(tmp_path, monkeypatch) -> None:
    chrome_dir = tmp_path / "Chrome"
    profile_dir = chrome_dir / "Profile 1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Bookmarks").write_text("bookmarks", encoding="utf-8")
    (profile_dir / "Cookies").write_text("cookie-secret", encoding="utf-8")
    (profile_dir / "Login Data").write_text("password-secret", encoding="utf-8")
    (chrome_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "OpenPaper", "user_name": "openpaper@example.com"}}}}),
        encoding="utf-8",
    )
    identity_file = tmp_path / "identities.json"
    browser_pool = tmp_path / "browser-pool"
    monkeypatch.setattr(mac_chrome, "BROWSER_POOL_DIR", browser_pool)

    result = mac_chrome.mirror_profiles(chrome_dir, identity_file)

    assert result["mirrored_count"] == 1
    assert result["safety"]["copied_raw_cookies"] is False
    dest = browser_pool / "profiles" / "chrome-openpaper"
    assert (dest / "Bookmarks").read_text(encoding="utf-8") == "bookmarks"
    assert not (dest / "Cookies").exists()
    assert not (dest / "Login Data").exists()
    stamp = json.loads((dest / ".mac-profile-mirror.json").read_text(encoding="utf-8"))
    assert stamp["copied_raw_passwords"] is False


def test_redacted_inventory_masks_email(tmp_path) -> None:
    chrome_dir = tmp_path / "Chrome"
    (chrome_dir / "Default").mkdir(parents=True)
    (chrome_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Default": {"name": "Federico", "user_name": "federico@example.com"}}}}),
        encoding="utf-8",
    )

    rows = mac_chrome.redacted_inventory(chrome_dir)

    assert rows[0]["account_email"] == "fe***@example.com"


def test_read_json_falls_back_to_mac_ssh_for_mount_permission_error(monkeypatch) -> None:
    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def exists(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            raise PermissionError("blocked by mount")

        def __str__(self) -> str:
            return self.value

    monkeypatch.setattr(
        mac_chrome.subprocess,
        "check_output",
        lambda args, text, timeout=None: '{"profile":{"info_cache":{}}}',
    )

    data = mac_chrome._read_json(FakePath("/Users/federicodeponte/Library/Application Support/Google/Chrome/Local State"), {})

    assert data == {"profile": {"info_cache": {}}}
