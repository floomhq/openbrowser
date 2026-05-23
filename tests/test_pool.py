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


def test_identity_lease_is_exclusive(monkeypatch) -> None:
    class Identity:
        slot = "pool-c"
        profile_dir = "/tmp/linkedin-main"

    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: Identity())

    lease = pool.lease("test-identity", identity_id="linkedin-main")
    try:
        assert lease.identity_id == "linkedin-main"
        assert lease.name == "pool-c"
        try:
            pool.lease("test-identity-2", identity_id="linkedin-main")
        except pool.LeaseError as error:
            assert "Identity already leased" in str(error)
        else:
            raise AssertionError("expected duplicate identity lease to fail")
    finally:
        pool.release(lease.lease_id)
