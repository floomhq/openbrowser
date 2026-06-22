from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from ax_browser_broker import pool


@pytest.fixture(autouse=True)
def three_slot_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(pool, "SLOTS", pool.SLOTS[:3])
    monkeypatch.setattr(pool, "read_slot_config", lambda _slot_name: {})
    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "BROWSER_POOL_MAINTENANCE_DIR", tmp_path / "maintenance")
    monkeypatch.setattr(pool, "slot_cdp_healthy", lambda slot, timeout=1.5: pool.healthy(slot.port))


def test_status_shape() -> None:
    data = pool.status()
    assert "slots" in data
    assert "leases" in data
    assert {slot["name"] for slot in data["slots"]} == {"pool-a", "pool-b", "pool-c"}
    assert all(slot["maintenance"] is False for slot in data["slots"])


def test_status_reports_active_maintenance(tmp_path, monkeypatch) -> None:
    maintenance_dir = tmp_path / "maintenance"
    maintenance_dir.mkdir()
    (maintenance_dir / "pool-a.json").write_text('{"expires_at": 9999999999}\n', encoding="utf-8")
    monkeypatch.setattr(pool, "BROWSER_POOL_MAINTENANCE_DIR", maintenance_dir)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: None)

    data = pool.status()

    by_name = {slot["name"]: slot for slot in data["slots"]}
    assert by_name["pool-a"]["maintenance"] is True
    assert by_name["pool-b"]["maintenance"] is False


def test_lease_release_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: None)
    monkeypatch.setattr(pool, "load_identities", lambda: {})
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("test-pool")
    try:
        assert lease.lease_id
        assert lease.cdp.startswith("http://127.0.0.1:")
        refreshed = pool.heartbeat(lease.lease_id)
        assert refreshed.lease_id == lease.lease_id
    finally:
        released = pool.release(lease.lease_id)
    assert released["released"] == lease.lease_id


def test_release_self_heals_released_slot_when_cdp_is_wedged(tmp_path, monkeypatch) -> None:
    calls = {"pool-a": 0}
    restarts = []

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: None)
    monkeypatch.setattr(pool, "load_identities", lambda: {})
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("test-self-heal")

    def fake_slot_cdp_healthy(slot, timeout=1.5):
        if slot.name != lease.name:
            return True
        calls[slot.name] = calls.get(slot.name, 0) + 1
        return calls[slot.name] >= 2

    def fake_run(args, check):
        restarts.append(args)

    monkeypatch.setattr(pool, "slot_cdp_healthy", fake_slot_cdp_healthy)
    monkeypatch.setattr(pool.subprocess, "run", fake_run)

    released = pool.release(lease.lease_id)

    assert released["released"] == lease.lease_id
    assert released["slot"] == lease.name
    assert released["self_heal"]["restarted"] is True
    assert released["self_heal"]["cdp_healthy"] is True
    assert restarts == [[str(pool.BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), lease.name, str(lease.port)]]


def test_reconcile_active_leases_self_heals_wedged_active_slot(tmp_path, monkeypatch) -> None:
    calls = {"pool-a": 0}
    restarts = []

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: None)
    monkeypatch.setattr(pool, "load_identities", lambda: {})
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("test-startup-heal", identity_id=None)

    def fake_slot_cdp_healthy(slot, timeout=1.5):
        if slot.name != lease.name:
            return True
        calls[slot.name] = calls.get(slot.name, 0) + 1
        return calls[slot.name] >= 2

    def fake_run(args, check):
        restarts.append(args)

    monkeypatch.setattr(pool, "slot_cdp_healthy", fake_slot_cdp_healthy)
    monkeypatch.setattr(pool.subprocess, "run", fake_run)

    results = pool.reconcile_active_leases()

    assert results == [
        {
            "lease_id": lease.lease_id,
            "slot": lease.name,
            "cdp_healthy": True,
            "restarted": True,
        }
    ]
    assert restarts == [[str(pool.BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), lease.name, str(lease.port)]]


def test_gc_leases_records_expiry_telemetry(tmp_path, monkeypatch) -> None:
    events = []
    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "LEASE_TTL_SECONDS", 60)
    monkeypatch.setattr(pool.time, "time", lambda: 200)

    def fake_record_event(**kwargs):
        events.append(kwargs)
        return {"id": "event"}

    import ax_browser_broker.telemetry as telemetry

    monkeypatch.setattr(telemetry, "record_event", fake_record_event)
    state = {
        "leases": {
            "expired-lease": {
                "name": "pool-a",
                "owner": "agent-a",
                "created_at": 1,
                "heartbeat_at": 100,
                "identity_id": "chrome-one",
            }
        }
    }

    expired = pool.gc_leases(state)

    assert expired == ["expired-lease"]
    assert state["leases"] == {}
    assert events[0]["message"] == "Lease expired"
    assert events[0]["lease_id"] == "expired-lease"
    assert events[0]["data"]["slot"] == "pool-a"


def test_generic_lease_skips_identity_active_slots(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-work", "pool-b": None, "pool-c": "work-main"}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "load_identities", lambda: {})
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("generic-public-task")
    try:
        assert lease.name == "pool-b"
        assert lease.identity_id is None
    finally:
        pool.release(lease.lease_id)


def test_generic_lease_reclaims_idle_proxied_identity_slot(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-work", "pool-b": None, "pool-c": "work-main"}
    reclaimed = []

    class Identity:
        def __init__(self, proxy_ref: str | None) -> None:
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-work": Identity(None),
        "work-main": Identity("residential:work-main"),
    }

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    def activate_neutral(slot):
        reclaimed.append(slot.name)
        active[slot.name] = None
        return True

    monkeypatch.setattr(pool, "_activate_neutral_slot", activate_neutral)

    neutral = pool.lease("neutral-task")
    try:
        reclaimed_lease = pool.lease("generic-task")
        try:
            assert reclaimed_lease.name == "pool-c"
            assert reclaimed_lease.identity_id is None
            assert reclaimed == ["pool-c"]
        finally:
            pool.release(reclaimed_lease.lease_id)
    finally:
        pool.release(neutral.lease_id)


def test_generic_lease_does_not_reclaim_personal_non_proxy_identity(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-work", "pool-b": None, "pool-c": "chat-main"}

    class Identity:
        proxy_ref = None

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-work": Identity(), "chat-main": Identity()})
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    neutral = pool.lease("neutral-task")
    try:
        try:
            pool.lease("generic-task")
        except pool.LeaseError as error:
            assert "No healthy free browser slots" in str(error)
        else:
            raise AssertionError("expected generic lease to fail instead of reclaiming a non-proxy identity")
    finally:
        pool.release(neutral.lease_id)


def test_identity_lease_is_exclusive(tmp_path, monkeypatch) -> None:
    class Identity:
        slot = "pool-c"
        profile_dir = "/tmp/work-main"

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: "work-main")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("test-identity", identity_id="work-main")
    try:
        assert lease.identity_id == "work-main"
        assert lease.name == "pool-c"
        try:
            pool.lease("test-identity-2", identity_id="work-main")
        except pool.LeaseError as error:
            assert "Identity already leased" in str(error)
        else:
            raise AssertionError("expected duplicate identity lease to fail")
    finally:
        pool.release(lease.lease_id)


def test_parallel_identity_leases_use_replica_profile(tmp_path, monkeypatch) -> None:
    active = {"pool-a": None, "pool-b": None, "pool-c": None}
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = tmp_path / "chrome-one"
        proxy_ref = None
        max_parallel_sessions = 2

    identity = Identity()

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **kwargs):
        activations.append((identity_id, slot_name, kwargs))
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)
    monkeypatch.setattr(pool, "identity_replica_profile_dir", lambda _identity, slot_name: tmp_path / "replicas" / slot_name)

    first = pool.lease("parallel-1", identity_id="chrome-one")
    second = pool.lease("parallel-2", identity_id="chrome-one")
    try:
        assert first.name == "pool-a"
        assert first.profile_dir == str(identity.profile_dir)
        assert second.name == "pool-b"
        assert second.profile_dir == str(tmp_path / "replicas" / "pool-b")
        assert activations[0][2]["clear_existing"] is False
        assert activations[0][2]["profile_dir_override"] == identity.profile_dir
        assert activations[1][2]["clear_existing"] is False
        assert activations[1][2]["profile_dir_override"] == tmp_path / "replicas" / "pool-b"
    finally:
        pool.release(first.lease_id)
        pool.release(second.lease_id)


def test_identity_lease_prefers_warm_active_replica_without_resync(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-one", "pool-b": "chrome-one", "pool-c": None}
    profile_dirs = {
        "pool-a": tmp_path / "canonical",
        "pool-b": tmp_path / "replica-b",
    }
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = tmp_path / "canonical"
        proxy_ref = None
        max_parallel_sessions = 2

    identity = Identity()

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(
        pool,
        "read_slot_config",
        lambda slot_name: {"PROFILE_DIR": str(profile_dirs[slot_name])} if slot_name in profile_dirs else {},
    )
    monkeypatch.setattr(pool, "_profile_cookie_score", lambda path: 50 if path == tmp_path / "replica-b" else 2)
    monkeypatch.setattr(pool, "activate_identity", lambda *args, **kwargs: activations.append((args, kwargs)))

    first = pool.lease("parallel-1", identity_id="chrome-one")
    second = pool.lease("parallel-2", identity_id="chrome-one")
    try:
        assert first.name == "pool-b"
        assert first.profile_dir == str(tmp_path / "replica-b")
        assert second.name == "pool-a"
        assert second.profile_dir == str(tmp_path / "canonical")
        assert activations == []
    finally:
        pool.release(first.lease_id)
        pool.release(second.lease_id)


def test_parallel_identity_limit_is_enforced(tmp_path, monkeypatch) -> None:
    active = {"pool-a": None, "pool-b": None, "pool-c": None}

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = tmp_path / "chrome-one"
        proxy_ref = None
        max_parallel_sessions = 2

    identity = Identity()

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)
    monkeypatch.setattr(pool, "identity_replica_profile_dir", lambda _identity, slot_name: tmp_path / "replicas" / slot_name)

    first = pool.lease("parallel-1", identity_id="chrome-one")
    second = pool.lease("parallel-2", identity_id="chrome-one")
    try:
        try:
            pool.lease("parallel-3", identity_id="chrome-one")
        except pool.LeaseError as error:
            assert "max parallel sessions" in str(error)
        else:
            raise AssertionError("expected identity parallel limit to be enforced")
    finally:
        pool.release(first.lease_id)
        pool.release(second.lease_id)


def test_identity_lease_reactivates_warm_slot_when_proxy_forwarder_is_missing(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": None}
    proxy_ready = {"pool-a": False, "pool-b": True, "pool-c": True}
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = tmp_path / "canonical"
        proxy_ref = "iproyal:main"
        max_parallel_sessions = 2

    identity = Identity()

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **kwargs):
        activations.append((identity_id, slot_name, kwargs))
        active[slot_name] = identity_id
        proxy_ready[slot_name] = True
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "_slot_proxy_ready", lambda slot_name: proxy_ready[slot_name])
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)
    monkeypatch.setattr(
        pool,
        "read_slot_config",
        lambda slot_name: {"PROFILE_DIR": str(identity.profile_dir), "PROXY_REF": "iproyal:main"}
        if slot_name == "pool-a"
        else {},
    )

    lease = pool.lease("parallel-1", identity_id="chrome-one")
    try:
        assert lease.name == "pool-a"
        assert activations == [("chrome-one", "pool-a", {"profile_dir_override": identity.profile_dir, "clear_existing": False})]
    finally:
        pool.release(lease.lease_id)


def test_require_lease_rejects_slot_with_dead_configured_proxy(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": None}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "_slot_proxy_ready", lambda slot_name: slot_name != "pool-a")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "load_identities", lambda: {})

    with pool.locked_state() as state:
        state["leases"]["lease-proxy-dead"] = {
            "name": "pool-a",
            "owner": "agent",
            "created_at": int(pool.time.time()),
            "heartbeat_at": int(pool.time.time()),
            "identity_id": "chrome-one",
        }

    with pytest.raises(pool.LeaseError, match="not healthy"):
        pool.require_lease("lease-proxy-dead")


def test_status_marks_configured_proxy_slot_unhealthy_when_forwarder_is_missing(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": None}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "_slot_proxy_ready", lambda slot_name: slot_name != "pool-a")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))

    data = pool.status()

    by_name = {slot["name"]: slot for slot in data["slots"]}
    assert by_name["pool-a"]["cdp_healthy"] is True
    assert by_name["pool-a"]["proxy_ready"] is False
    assert by_name["pool-a"]["healthy"] is False


def test_warm_replica_slot_is_reused_without_destructive_refresh(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-one", "pool-b": "chrome-one", "pool-c": None}
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = tmp_path / "chrome-one"
        proxy_ref = None
        max_parallel_sessions = 2

    identity = Identity()

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **kwargs):
        activations.append((identity_id, slot_name, kwargs))
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)
    monkeypatch.setattr(pool, "_is_replica_profile", lambda profile_dir: bool(profile_dir))
    monkeypatch.setattr(
        pool,
        "read_slot_config",
        lambda slot_name: {"PROFILE_DIR": str(tmp_path / "replicas" / slot_name)} if slot_name == "pool-b" else {},
    )

    canonical = pool.lease("canonical", identity_id="chrome-one")
    replica = pool.lease("replica", identity_id="chrome-one")
    try:
        assert canonical.name == "pool-a"
        assert replica.name == "pool-b"
        assert activations == []
    finally:
        pool.release(canonical.lease_id)
        pool.release(replica.lease_id)


def test_auto_identity_uses_free_non_reserved_slot(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": "work-main"}
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
        "work-main": Identity("work-main", "pool-c", str(tmp_path / "work-main"), "proxy"),
    }

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
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


def test_auto_identity_skips_unhealthy_slot_after_activation(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": None, "pool-b": None, "pool-c": "work-main"}
    healthy_calls = {"pool-a": 0, "pool-b": 0, "pool-c": 0}

    class Identity:
        def __init__(self, identity_id: str, slot: str, profile_dir: str, proxy_ref: str | None = None) -> None:
            self.identity_id = identity_id
            self.slot = slot
            self.profile_dir = profile_dir
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-one": Identity("chrome-one", "auto", str(tmp_path / "chrome-one")),
        "work-main": Identity("work-main", "pool-c", str(tmp_path / "work-main"), "proxy"),
    }

    port_to_slot = {slot.port: slot.name for slot in pool.SLOTS}

    def healthy(port: int) -> bool:
        slot_name = port_to_slot[port]
        healthy_calls[slot_name] += 1
        if slot_name == "pool-a":
            return False
        return True

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", healthy)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    lease = pool.lease("test-health-skip", identity_id="chrome-one")
    try:
        assert lease.name == "pool-b"
        assert active["pool-a"] == "chrome-one"
        assert active["pool-b"] == "chrome-one"
        assert healthy_calls["pool-a"] > 0
    finally:
        pool.release(lease.lease_id)


def test_auto_identity_skips_maintenance_slot(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    maintenance_dir = tmp_path / "maintenance"
    maintenance_dir.mkdir()
    (maintenance_dir / "pool-a.json").write_text('{"expires_at": 9999999999}\n', encoding="utf-8")
    active = {"pool-a": None, "pool-b": None, "pool-c": None}
    activations = []

    class Identity:
        def __init__(self, identity_id: str) -> None:
            self.identity_id = identity_id
            self.slot = "auto"
            self.profile_dir = str(tmp_path / identity_id)
            self.proxy_ref = None
            self.max_parallel_sessions = 1

    identities = {"chrome-one": Identity("chrome-one")}

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        activations.append(slot_name)
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "BROWSER_POOL_MAINTENANCE_DIR", maintenance_dir)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    lease = pool.lease("test-maintenance-skip", identity_id="chrome-one")
    try:
        assert lease.name == "pool-b"
        assert activations == ["pool-b"]
        assert active["pool-a"] is None
    finally:
        pool.release(lease.lease_id)


def test_auto_identity_skips_slot_when_activation_raises(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": None, "pool-b": None, "pool-c": None}
    activations = []

    class Identity:
        def __init__(self, identity_id: str) -> None:
            self.identity_id = identity_id
            self.slot = "auto"
            self.profile_dir = str(tmp_path / identity_id)
            self.proxy_ref = None
            self.max_parallel_sessions = 1

    identities = {"chrome-one": Identity("chrome-one")}

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        activations.append(slot_name)
        if slot_name == "pool-a":
            raise pool.IdentityError("slot failed to become healthy")
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    lease = pool.lease("test-activation-failure-skip", identity_id="chrome-one")
    try:
        assert lease.name == "pool-b"
        assert activations == ["pool-a", "pool-b"]
    finally:
        pool.release(lease.lease_id)


def test_auto_identity_first_lease_reconciles_stale_duplicate_slot(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": "chrome-one", "pool-b": "chrome-one", "pool-c": None}
    activations = []

    class Identity:
        def __init__(self, identity_id: str) -> None:
            self.identity_id = identity_id
            self.slot = "auto"
            self.profile_dir = str(tmp_path / identity_id)
            self.proxy_ref = None
            self.max_parallel_sessions = 2

    identities = {"chrome-one": Identity("chrome-one")}

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **kwargs):
        activations.append((slot_name, kwargs.get("clear_existing")))
        active[slot_name] = identity_id
        if kwargs.get("clear_existing"):
            for other_slot in active:
                if other_slot != slot_name and active[other_slot] == identity_id:
                    active[other_slot] = None
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(
        pool,
        "read_slot_config",
        lambda slot_name: {"PROFILE_DIR": str(tmp_path / "chrome-one")} if active.get(slot_name) == "chrome-one" else {},
    )
    monkeypatch.setattr(pool, "activate_identity", activate)

    lease = pool.lease("test-stale-duplicate-reconcile", identity_id="chrome-one")
    try:
        assert lease.name == "pool-a"
        assert activations == [("pool-a", True)]
        assert active["pool-b"] is None
    finally:
        pool.release(lease.lease_id)


def test_concurrent_auto_identity_leases_are_unique_under_lock(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": None, "pool-b": None, "pool-c": None}

    class Identity:
        def __init__(self, identity_id: str) -> None:
            self.identity_id = identity_id
            self.slot = "auto"
            self.profile_dir = str(tmp_path / identity_id)
            self.proxy_ref = None

    identities = {f"chrome-{index}": Identity(f"chrome-{index}") for index in range(4)}

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    outcomes = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(pool.lease, f"concurrent-{identity_id}", 120, identity_id)
            for identity_id in identities
        ]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except pool.LeaseError as error:
                outcomes.append(error)

    leases = [item for item in outcomes if isinstance(item, pool.Lease)]
    errors = [item for item in outcomes if isinstance(item, pool.LeaseError)]
    try:
        assert len(leases) == 3
        assert len(errors) == 1
        assert {lease.name for lease in leases} == {"pool-a", "pool-b", "pool-c"}
        assert "No healthy free browser slots" in str(errors[0])
    finally:
        for lease in leases:
            pool.release(lease.lease_id)


def test_sustained_auto_identity_contention_exhausts_slots_cleanly(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": None, "pool-b": None, "pool-c": None}

    class Identity:
        def __init__(self, identity_id: str) -> None:
            self.identity_id = identity_id
            self.slot = "auto"
            self.profile_dir = str(tmp_path / identity_id)
            self.proxy_ref = None

    identities = {f"chrome-{index}": Identity(f"chrome-{index}") for index in range(10)}

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    outcomes = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(pool.lease, f"sustained-{identity_id}", 120, identity_id)
            for identity_id in identities
        ]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result())
            except pool.LeaseError as error:
                outcomes.append(error)

    leases = [item for item in outcomes if isinstance(item, pool.Lease)]
    errors = [item for item in outcomes if isinstance(item, pool.LeaseError)]
    try:
        assert len(leases) == 3
        assert len(errors) == 7
        assert {lease.name for lease in leases} == {"pool-a", "pool-b", "pool-c"}
        assert all("No healthy free browser slots" in str(error) for error in errors)
        assert len({lease.identity_id for lease in leases}) == 3
    finally:
        for lease in leases:
            pool.release(lease.lease_id)


def test_auto_identity_respects_dynamic_in_use_and_reserved_slots(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": "chrome-busy", "pool-b": None, "pool-c": "work-main"}

    class Identity:
        def __init__(self, identity_id: str, slot: str, profile_dir: str, proxy_ref: str | None = None) -> None:
            self.identity_id = identity_id
            self.slot = slot
            self.profile_dir = profile_dir
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-busy": Identity("chrome-busy", "auto", str(tmp_path / "chrome-busy")),
        "chrome-free": Identity("chrome-free", "auto", str(tmp_path / "chrome-free")),
        "work-main": Identity("work-main", "pool-c", str(tmp_path / "work-main"), "proxy"),
    }

    def activate(identity_id: str, slot_name: str, check_leases: bool = True, **_kwargs):
        active[slot_name] = identity_id
        return {"active": True}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", state_file)
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda identity_id: identities[identity_id])
    monkeypatch.setattr(pool, "load_identities", lambda: identities)
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "activate_identity", activate)

    busy = pool.lease("dynamic-busy", identity_id="chrome-busy")
    free = pool.lease("dynamic-free", identity_id="chrome-free")
    try:
        assert busy.name == "pool-a"
        assert free.name == "pool-b"
        assert active["pool-c"] == "work-main"
    finally:
        pool.release(busy.lease_id)
        pool.release(free.lease_id)


def _write_cookie_db(profile_dir, host="api.slack.com", count=1):
    """Create a minimal Chrome-shaped Cookies sqlite db under Default/Cookies."""
    import sqlite3

    db_dir = profile_dir / "Default"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "Cookies"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("create table if not exists cookies (host_key text, name text)")
        connection.execute("delete from cookies")
        for index in range(count):
            connection.execute("insert into cookies (host_key, name) values (?, ?)", (host, f"c{index}"))
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_replica_is_stale_against_base_detects_newer_base(tmp_path):
    import os

    base = tmp_path / "base"
    replica = tmp_path / "replicas" / "pool-b"
    base_db = _write_cookie_db(base)
    replica_db = _write_cookie_db(replica)
    # Replica synced before the human login: older than base.
    os.utime(replica_db, (1000, 1000))
    os.utime(base_db, (2000, 2000))
    assert pool._replica_is_stale_against_base(base, replica) is True
    # When replica is at least as fresh as base, no refresh needed.
    os.utime(replica_db, (2000, 2000))
    assert pool._replica_is_stale_against_base(base, replica) is False
    # Same dir is never stale.
    assert pool._replica_is_stale_against_base(base, base) is False


def test_warm_replica_resynced_when_base_has_fresher_cookies(tmp_path, monkeypatch):
    """Regression: a warm replica that predates the auth login must be re-synced."""
    import os

    base = tmp_path / "chrome-one"
    replica = tmp_path / "profiles" / ".replicas" / "chrome-one" / "pool-a"
    base_db = _write_cookie_db(base, count=5)
    replica_db = _write_cookie_db(replica, count=0)
    os.utime(replica_db, (1000, 1000))  # stale replica
    os.utime(base_db, (5000, 5000))  # fresh base (human just logged in)

    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": None}
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = base
        proxy_ref = None
        max_parallel_sessions = 2

    identity = Identity()

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(
        pool,
        "read_slot_config",
        lambda slot_name: {"PROFILE_DIR": str(replica)} if slot_name == "pool-a" else {},
    )
    monkeypatch.setattr(pool, "_is_replica_profile", lambda profile_dir: "/.replicas/" in str(profile_dir or ""))
    monkeypatch.setattr(pool, "identity_replica_profile_dir", lambda _identity, slot_name: replica)
    monkeypatch.setattr(pool, "activate_identity", lambda *args, **kwargs: activations.append((args, kwargs)))

    leased = pool.lease("agent-1", identity_id="chrome-one")
    try:
        # The stale warm replica forced a re-activation (which re-syncs base->replica),
        # instead of the no-resync warm reuse path.
        assert leased.name == "pool-a"
        assert leased.profile_dir == str(replica)
        assert len(activations) == 1, "expected re-activation to re-sync stale replica"
        assert activations[0][0][1] == "pool-a"
    finally:
        pool.release(leased.lease_id)


def test_headed_identity_lease_reactivates_stale_headless_process(tmp_path, monkeypatch):
    base = tmp_path / "chrome-one"
    base.mkdir()
    active = {"pool-a": "chrome-one", "pool-b": None, "pool-c": None}
    activations = []

    class Identity:
        identity_id = "chrome-one"
        slot = "auto"
        profile_dir = base
        proxy_ref = None
        max_parallel_sessions = 1

    identity = Identity()

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: identity)
    monkeypatch.setattr(pool, "load_identities", lambda: {"chrome-one": identity})
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "read_slot_config", lambda _slot_name: {"PROFILE_DIR": str(base), "CHROME_HEADLESS": "0"})
    monkeypatch.setattr(pool, "_slot_headed_ready", lambda _slot: bool(activations))
    monkeypatch.setattr(pool, "activate_identity", lambda *args, **kwargs: activations.append((args, kwargs)))

    leased = pool.lease("agent-headed", identity_id="chrome-one", headed=True)
    try:
        assert leased.name == "pool-a"
        assert leased.headed is True
        assert len(activations) == 1
        assert activations[0][1]["headed"] is True
    finally:
        pool.release(leased.lease_id)
