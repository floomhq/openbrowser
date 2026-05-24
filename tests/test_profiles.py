from __future__ import annotations

import shutil

from ax_browser_broker import profiles


def test_rsync_profile_excludes_sensitive_and_lock_files(tmp_path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    (source / ".totp-secret").write_text("secret", encoding="utf-8")
    (source / ".com.google.Chrome.abc").write_text("lock", encoding="utf-8")
    (source / "keep.txt").write_text("ok", encoding="utf-8")

    profiles._rsync_profile(source, dest)

    assert (dest / "keep.txt").read_text(encoding="utf-8") == "ok"
    assert not (dest / ".totp-secret").exists()
    assert not (dest / ".com.google.Chrome.abc").exists()


def test_seed_identity_uses_identity_profile_and_port(tmp_path, monkeypatch) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "Cookies").write_text("cookie-db", encoding="utf-8")
    dest = tmp_path / "linkedin-main"
    calls = []

    class Identity:
        identity_id = "linkedin-main"
        slot = "pool-c"
        profile_dir = dest

    def fake_run(args, check=False, **_kwargs):
        calls.append(args)

        class Result:
            returncode = 1

        return Result()

    monkeypatch.setattr(profiles, "GOLDEN_PROFILE_DIR", golden)
    monkeypatch.setattr(profiles, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(
        profiles,
        "status",
        lambda: {
            "leases": {},
            "slots": [{"name": "pool-c", "port": 9225, "profile_dir": str(tmp_path / "old-pool-c")}],
        },
    )
    monkeypatch.setattr(profiles.subprocess, "run", fake_run)
    monkeypatch.setattr(profiles.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        profiles,
        "_rsync_profile",
        lambda source, target: shutil.copytree(source, target, dirs_exist_ok=True),
    )

    result = profiles.seed_identity("linkedin-main")

    assert result["identity_id"] == "linkedin-main"
    assert result["profile_dir"] == str(dest)
    assert (dest / "Cookies").read_text(encoding="utf-8") == "cookie-db"
    assert ["pkill", "-f", "--", "--remote-debugging-port=9225"] in calls
