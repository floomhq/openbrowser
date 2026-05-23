from __future__ import annotations

from ax_browser_broker import pool


def test_status_shape() -> None:
    data = pool.status()
    assert "slots" in data
    assert "leases" in data
    assert {slot["name"] for slot in data["slots"]} == {"pool-a", "pool-b", "pool-c"}


def test_lease_release_round_trip() -> None:
    lease = pool.lease("test-pool")
    try:
        assert lease.lease_id
        assert lease.cdp.startswith("http://127.0.0.1:")
        refreshed = pool.heartbeat(lease.lease_id)
        assert refreshed.lease_id == lease.lease_id
    finally:
        released = pool.release(lease.lease_id)
    assert released["released"] == lease.lease_id
