from __future__ import annotations

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
