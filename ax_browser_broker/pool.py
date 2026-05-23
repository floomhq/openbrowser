from __future__ import annotations

import fcntl
import json
import time
import uuid
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import LEASE_TTL_SECONDS, POOL_STATE_FILE, SLOTS, Slot, ensure_dirs


class LeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    lease_id: str
    name: str
    port: int
    owner: str
    created_at: int
    heartbeat_at: int
    expires_at: int
    cdp: str
    profile_dir: str


def healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


@contextmanager
def locked_state() -> Any:
    ensure_dirs()
    POOL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not POOL_STATE_FILE.exists():
        POOL_STATE_FILE.write_text(json.dumps({"leases": {}}, indent=2), encoding="utf-8")
    with POOL_STATE_FILE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        raw = handle.read().strip()
        state = json.loads(raw) if raw else {"leases": {}}
        state.setdefault("leases", {})
        yield state
        handle.seek(0)
        handle.truncate(0)
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _slot_by_name(name: str) -> Slot | None:
    return next((slot for slot in SLOTS if slot.name == name), None)


def _lease_from_state(lease_id: str, lease: dict[str, Any]) -> Lease:
    slot = _slot_by_name(str(lease["name"]))
    if slot is None:
        raise LeaseError(f"Unknown browser slot in lease: {lease['name']}")
    created_at = int(lease.get("created_at", lease.get("ts", time.time())))
    heartbeat_at = int(lease.get("heartbeat_at", lease.get("ts", created_at)))
    expires_at = heartbeat_at + LEASE_TTL_SECONDS
    return Lease(
        lease_id=lease_id,
        name=slot.name,
        port=slot.port,
        owner=str(lease.get("owner", "unknown")),
        created_at=created_at,
        heartbeat_at=heartbeat_at,
        expires_at=expires_at,
        cdp=slot.cdp,
        profile_dir=str(slot.profile_dir),
    )


def gc_leases(state: dict[str, Any]) -> list[str]:
    now = int(time.time())
    expired: list[str] = []
    for lease_id, lease in list(state["leases"].items()):
        heartbeat_at = int(lease.get("heartbeat_at", lease.get("ts", now)))
        if now - heartbeat_at > LEASE_TTL_SECONDS:
            expired.append(lease_id)
            del state["leases"][lease_id]
    return expired


def lease(owner: str, ttl_seconds: int = LEASE_TTL_SECONDS) -> Lease:
    now = int(time.time())
    effective_ttl = max(60, min(int(ttl_seconds), LEASE_TTL_SECONDS))
    with locked_state() as state:
        gc_leases(state)
        in_use = {str(item["name"]) for item in state["leases"].values()}
        for slot in SLOTS:
            if slot.name in in_use or not healthy(slot.port):
                continue
            lease_id = str(uuid.uuid4())
            state["leases"][lease_id] = {
                "name": slot.name,
                "port": slot.port,
                "owner": owner,
                "ts": now,
                "created_at": now,
                "heartbeat_at": now,
                "ttl_seconds": effective_ttl,
            }
            return _lease_from_state(lease_id, state["leases"][lease_id])
    raise LeaseError("No healthy free browser slots")


def release(lease_id: str) -> dict[str, str | None]:
    with locked_state() as state:
        if lease_id in state["leases"]:
            slot = str(state["leases"][lease_id]["name"])
            del state["leases"][lease_id]
            return {"released": lease_id, "slot": slot}
    return {"released": None, "slot": None}


def heartbeat(lease_id: str) -> Lease:
    now = int(time.time())
    with locked_state() as state:
        gc_leases(state)
        if lease_id not in state["leases"]:
            raise LeaseError("Lease not found")
        state["leases"][lease_id]["heartbeat_at"] = now
        state["leases"][lease_id]["ts"] = now
        return _lease_from_state(lease_id, state["leases"][lease_id])


def require_lease(lease_id: str) -> Lease:
    with locked_state() as state:
        gc_leases(state)
        if lease_id not in state["leases"]:
            raise LeaseError("Lease not found")
        lease_obj = _lease_from_state(lease_id, state["leases"][lease_id])
        state["leases"][lease_id]["heartbeat_at"] = int(time.time())
        state["leases"][lease_id]["ts"] = int(time.time())
    if not healthy(lease_obj.port):
        raise LeaseError(f"Browser slot {lease_obj.name} is not healthy")
    return lease_obj


def status() -> dict[str, Any]:
    with locked_state() as state:
        expired = gc_leases(state)
        leases = {lease_id: asdict(_lease_from_state(lease_id, lease)) for lease_id, lease in state["leases"].items()}
    leased_names = {lease["name"] for lease in leases.values()}
    return {
        "slots": [
            {
                "name": slot.name,
                "port": slot.port,
                "cdp": slot.cdp,
                "profile_dir": str(slot.profile_dir),
                "healthy": healthy(slot.port),
                "leased": slot.name in leased_names,
            }
            for slot in SLOTS
        ],
        "leases": leases,
        "expired": expired,
    }


def slot_profile(name: str) -> Path:
    slot = _slot_by_name(name)
    if slot is None:
        raise LeaseError(f"Unknown browser slot: {name}")
    return slot.profile_dir
