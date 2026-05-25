from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ax_browser_broker import pool


def test_status_shape() -> None:
    data = pool.status()
    assert "slots" in data
    assert "leases" in data
    assert {slot["name"] for slot in data["slots"]} == {"pool-a", "pool-b", "pool-c"}


def test_lease_release_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: None)
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


def test_generic_lease_skips_identity_active_slots(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-depontefede", "pool-b": None, "pool-c": "linkedin-main"}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    lease = pool.lease("generic-public-task")
    try:
        assert lease.name == "pool-b"
        assert lease.identity_id is None
    finally:
        pool.release(lease.lease_id)


def test_generic_lease_fails_when_only_identity_slots_are_free(tmp_path, monkeypatch) -> None:
    active = {"pool-a": "chrome-depontefede", "pool-b": None, "pool-c": "linkedin-main"}

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "active_identity_id", lambda slot_name: active.get(slot_name))
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

    neutral = pool.lease("neutral-task")
    try:
        try:
            pool.lease("generic-task")
        except pool.LeaseError as error:
            assert "No healthy free browser slots" in str(error)
        else:
            raise AssertionError("expected generic lease to fail instead of using identity-active slot")
    finally:
        pool.release(neutral.lease_id)


def test_identity_lease_is_exclusive(tmp_path, monkeypatch) -> None:
    class Identity:
        slot = "pool-c"
        profile_dir = "/tmp/linkedin-main"

    monkeypatch.setattr(pool, "POOL_STATE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(pool, "require_identity", lambda _identity_id: Identity())
    monkeypatch.setattr(pool, "active_identity_id", lambda _slot_name: "linkedin-main")
    monkeypatch.setattr(pool, "healthy", lambda _port: True)

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


def test_auto_identity_skips_unhealthy_slot_after_activation(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "leases.json"
    active = {"pool-a": None, "pool-b": None, "pool-c": "linkedin-main"}
    healthy_calls = {"pool-a": 0, "pool-b": 0, "pool-c": 0}

    class Identity:
        def __init__(self, identity_id: str, slot: str, profile_dir: str, proxy_ref: str | None = None) -> None:
            self.identity_id = identity_id
            self.slot = slot
            self.profile_dir = profile_dir
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-one": Identity("chrome-one", "auto", str(tmp_path / "chrome-one")),
        "linkedin-main": Identity("linkedin-main", "pool-c", str(tmp_path / "linkedin-main"), "proxy"),
    }

    port_to_slot = {slot.port: slot.name for slot in pool.SLOTS}

    def healthy(port: int) -> bool:
        slot_name = port_to_slot[port]
        healthy_calls[slot_name] += 1
        if slot_name == "pool-a":
            return False
        return True

    def activate(identity_id: str, slot_name: str, check_leases: bool = True):
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

    def activate(identity_id: str, slot_name: str, check_leases: bool = True):
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

    def activate(identity_id: str, slot_name: str, check_leases: bool = True):
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
    active = {"pool-a": "chrome-busy", "pool-b": None, "pool-c": "linkedin-main"}

    class Identity:
        def __init__(self, identity_id: str, slot: str, profile_dir: str, proxy_ref: str | None = None) -> None:
            self.identity_id = identity_id
            self.slot = slot
            self.profile_dir = profile_dir
            self.proxy_ref = proxy_ref

    identities = {
        "chrome-busy": Identity("chrome-busy", "auto", str(tmp_path / "chrome-busy")),
        "chrome-free": Identity("chrome-free", "auto", str(tmp_path / "chrome-free")),
        "linkedin-main": Identity("linkedin-main", "pool-c", str(tmp_path / "linkedin-main"), "proxy"),
    }

    def activate(identity_id: str, slot_name: str, check_leases: bool = True):
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
        assert active["pool-c"] == "linkedin-main"
    finally:
        pool.release(busy.lease_id)
        pool.release(free.lease_id)
