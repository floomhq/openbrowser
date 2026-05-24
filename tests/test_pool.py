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
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: "linkedin-main")

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


def test_auto_identity_uses_free_non_reserved_slot(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": "linkedin-main"}
    activations = []

    class Identity:
        def __init__(self, identity_id: str, slot: str, profile_dir: str, proxy_ref: str | None = None) -> None:
            self.identity_id = identity_id
            self.slot = slot
            self.profile_dir = profile_dir
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-one": Identity("chrome-one", "auto", str(tmp_path / "chrome-one")),
        "chrome-two": Identity("chrome-two", "auto", str(tmp_path / "chrome-two")),
        "linkedin-main": Identity("linkedin-main", "pool-c", str(tmp_path / "linkedin-main"), "proxy"),
    }

    def activate(identity_id: str, slot_name: str, check_leases: bool = True):
        activations.append((identity_id, slot_name, check_leases))
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    first = pool.lease("test-auto-1", identity_id="chrome-one")
    second = pool.lease("test-auto-2", identity_id="chrome-two")

    try:
        assert first.name == "pool-a"
        assert second.name == "pool-b"
        assert ("chrome-two", "pool-b", False) in activations
        assert all(item[1] != "pool-c" for item in activations)
    finally:
        pool.release(first.lease_id)
        pool.release(second.lease_id)
