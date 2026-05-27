from __future__ import annotations

import subprocess

from ax_browser_broker import mac_tunnel
from ax_browser_broker.mac_tunnel import ProbeResult


def test_check_ssh_uses_bounded_batch_mode_probe(monkeypatch) -> None:
    calls = []

    def fake_run(args, text, capture_output, timeout, check):
        calls.append((args, timeout, check))
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(mac_tunnel.subprocess, "run", fake_run)

    result = mac_tunnel.check_ssh()

    assert result.ok is True
    assert result.detail == "ok"
    args, timeout, check = calls[0]
    assert timeout == 8
    assert check is False
    assert "BatchMode=yes" in args
    assert "ConnectTimeout=3" in args


def test_status_reports_expected_reverse_tunnel(monkeypatch) -> None:
    monkeypatch.setattr(mac_tunnel, "check_ssh", lambda: ProbeResult(False, "ssh mac echo ok", "Connection refused"))
    monkeypatch.setattr(mac_tunnel, "check_cdp", lambda: ProbeResult(False, "GET /json/version", "Connection refused"))

    result = mac_tunnel.status()

    assert result["ok"] is False
    assert result["ssh"]["detail"] == "Connection refused"
    assert result["expected_mac_reverse_tunnel"]["broker_listener"] == "127.0.0.1:2222"
    assert "ssh -N -R" in result["expected_mac_reverse_tunnel"]["mac_command"]


def test_sync_profiles_stops_before_mirror_when_ssh_is_down(monkeypatch) -> None:
    called = {"mirror": False}
    monkeypatch.setattr(mac_tunnel, "check_ssh", lambda: ProbeResult(False, "ssh mac echo ok", "Connection refused"))
    monkeypatch.setattr(mac_tunnel, "check_cdp", lambda: ProbeResult(False, "GET /json/version", "Connection refused"))

    def fake_mirror_profiles(dry_run=False):
        called["mirror"] = True
        return {"ok": True}

    monkeypatch.setattr(mac_tunnel, "mirror_profiles", fake_mirror_profiles)

    result = mac_tunnel.sync_profiles(dry_run=True)

    assert result["ok"] is False
    assert result["stage"] == "ssh"
    assert called["mirror"] is False


def test_sync_profiles_runs_mirror_after_ssh_and_cdp(monkeypatch) -> None:
    monkeypatch.setattr(mac_tunnel, "check_ssh", lambda: ProbeResult(True, "ssh mac echo ok", "ok"))
    monkeypatch.setattr(mac_tunnel, "ensure_cdp", lambda: ProbeResult(True, "GET /json/version", "Chrome/148"))
    monkeypatch.setattr(mac_tunnel, "mirror_profiles", lambda dry_run=False: {"ok": True, "mirrored_count": 16})

    result = mac_tunnel.sync_profiles(dry_run=True)

    assert result["ok"] is True
    assert result["mirror"]["mirrored_count"] == 16
