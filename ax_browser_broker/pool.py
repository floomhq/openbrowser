from __future__ import annotations

import fcntl
import json
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import (
    BROWSER_POOL_DIR,
    BROWSER_POOL_MAINTENANCE_DIR,
    LEASE_TTL_SECONDS,
    POOL_CONFIG_DIR,
    POOL_STATE_FILE,
    SLOTS,
    Slot,
    ensure_dirs,
)
from .identities import (
    IdentityError,
    active_identity_id,
    activate_identity,
    identity_replica_profile_dir,
    load_identities,
    read_slot_config,
    require_identity,
)


class LeaseError(RuntimeError):
    pass


_STATE_THREAD_LOCK = threading.RLock()


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
    identity_id: str | None = None
    headed: bool = False


def healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _load_cdp_json(port: int, path: str, timeout: float = 1.5) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        if response.status != 200:
            raise LeaseError(f"CDP {path} returned HTTP {response.status}")
        raw = response.read().decode("utf-8").strip()
        if not raw:
            raise LeaseError(f"CDP {path} returned an empty response")
        return json.loads(raw)


def slot_cdp_healthy(slot: Slot, timeout: float = 1.5) -> bool:
    try:
        version = _load_cdp_json(slot.port, "/json/version", timeout=timeout)
        tabs = _load_cdp_json(slot.port, "/json/list", timeout=timeout)
    except Exception:
        return False
    return bool(version.get("Browser") and version.get("webSocketDebuggerUrl") and isinstance(tabs, list))


def _slot_proxy_ready(slot_name: str, timeout: float = 0.3) -> bool:
    config = read_slot_config(slot_name)
    if not config.get("PROXY_REF"):
        return True
    try:
        local_port = int(config.get("PROXY_LOCAL_PORT") or 0)
    except ValueError:
        return False
    if local_port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", local_port), timeout=timeout):
            return True
    except OSError:
        return False


def _slot_ready(slot: Slot) -> bool:
    return slot_cdp_healthy(slot) and _slot_proxy_ready(slot.name)


def _pid_alive(pid_file: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return Path(f"/proc/{pid}").exists()


def _slot_chrome_is_headed(slot: Slot) -> bool:
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            cmdline_path = proc / "cmdline"
            try:
                raw = cmdline_path.read_bytes()
            except OSError:
                continue
            if not raw:
                continue
            args = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
            if f"--remote-debugging-port={slot.port}" not in args:
                continue
            if "--headless" in args or "--ozone-platform=headless" in args:
                return False
            return True
    except OSError:
        return False
    return False


def _slot_headed_ready(slot: Slot) -> bool:
    config = read_slot_config(slot.name)
    if config.get("CHROME_HEADLESS") != "0":
        return False
    display_path = BROWSER_POOL_DIR / "state" / f"{slot.name}.display"
    try:
        display = display_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not display:
        return False
    if not _pid_alive(BROWSER_POOL_DIR / "state" / f"{slot.name}.xvfb.pid"):
        return False
    return _slot_chrome_is_headed(slot)


def slot_in_maintenance(slot_name: str) -> bool:
    path = BROWSER_POOL_MAINTENANCE_DIR / f"{slot_name}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    expires_at = int(data.get("expires_at", 0) or 0)
    if expires_at and time.time() > expires_at:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return True
        return False
    return True


@contextmanager
def locked_state() -> Any:
    ensure_dirs()
    POOL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_THREAD_LOCK:
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
    profile_dir = str(lease.get("profile_dir") or slot.profile_dir)
    return Lease(
        lease_id=lease_id,
        name=slot.name,
        port=slot.port,
        owner=str(lease.get("owner", "unknown")),
        created_at=created_at,
        heartbeat_at=heartbeat_at,
        expires_at=expires_at,
        cdp=slot.cdp,
        profile_dir=profile_dir,
        identity_id=str(lease["identity_id"]) if lease.get("identity_id") else None,
        headed=bool(lease.get("headed")),
    )


def gc_leases(state: dict[str, Any]) -> list[str]:
    now = int(time.time())
    expired: list[str] = []
    for lease_id, lease in list(state["leases"].items()):
        heartbeat_at = int(lease.get("heartbeat_at", lease.get("ts", now)))
        if now - heartbeat_at > LEASE_TTL_SECONDS:
            expired.append(lease_id)
            _record_expired_lease(lease_id, lease)
            del state["leases"][lease_id]
    return expired


def _record_expired_lease(lease_id: str, lease: dict[str, Any]) -> None:
    try:
        from .telemetry import record_event

        record_event(
            source=str(lease.get("owner", "broker-api")),
            event_type="lease",
            message="Lease expired",
            severity="warning",
            lease_id=lease_id,
            tags=["lease", "expired"],
            data={
                "slot": str(lease.get("name", "")),
                "identity_id": lease.get("identity_id"),
                "created_at": int(lease.get("created_at", lease.get("ts", 0))),
                "heartbeat_at": int(lease.get("heartbeat_at", lease.get("ts", 0))),
            },
        )
    except Exception:
        return


def _can_reclaim_for_generic(active_identity: str | None, identities: dict[str, Any]) -> bool:
    if not active_identity:
        return False
    identity = identities.get(active_identity)
    return bool(identity and getattr(identity, "proxy_ref", None))


def _wait_healthy(port: int, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if healthy(port):
            return True
        time.sleep(0.2)
    return False


def _wait_cdp_healthy(slot: Slot, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if slot_cdp_healthy(slot):
            return True
        time.sleep(0.2)
    return False


def _wait_slot_ready(slot: Slot, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _slot_ready(slot):
            return True
        time.sleep(0.2)
    return False


def _activate_neutral_slot(slot: Slot) -> bool:
    if slot_in_maintenance(slot.name):
        return False
    config_path = POOL_CONFIG_DIR / f"{slot.name}.env"
    if config_path.exists():
        config_path.unlink()
    subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), slot.name, str(slot.port)], check=True)
    return _wait_slot_ready(slot)


def _restart_slot(slot: Slot) -> bool:
    if slot_in_maintenance(slot.name):
        return False
    subprocess.run([str(BROWSER_POOL_DIR / "bin" / "launch_chrome.sh"), slot.name, str(slot.port)], check=True)
    return _wait_cdp_healthy(slot)


def _heal_slot_after_failed_cdp_check(
    slot: Slot,
    *,
    lease_id: str | None,
    owner: str,
    identity_id: str | None,
    context: str,
) -> dict[str, Any]:
    before_healthy = slot_cdp_healthy(slot, timeout=1.0)
    result: dict[str, Any] = {"slot": slot.name, "cdp_healthy": before_healthy, "restarted": False}
    if before_healthy:
        return result
    try:
        restarted_healthy = _restart_slot(slot)
        result.update({"cdp_healthy": restarted_healthy, "restarted": True})
        try:
            from .telemetry import record_event

            record_event(
                source=owner or "broker-api",
                event_type="lease",
                message=f"{context} slot self-healed after failed CDP health check",
                severity="warning",
                lease_id=lease_id,
                tags=["lease", context, "self-heal", slot.name],
                data={"slot": slot.name, "identity_id": identity_id, "cdp_healthy": restarted_healthy},
            )
        except Exception:
            pass
    except Exception as error:
        result.update({"error": str(error), "restarted": False})
        try:
            from .telemetry import record_event

            record_event(
                source=owner or "broker-api",
                event_type="error",
                message=f"{context} slot self-heal failed",
                severity="error",
                lease_id=lease_id,
                tags=["lease", context, "self-heal", "failure", slot.name],
                data={"slot": slot.name, "identity_id": identity_id, "error": str(error)},
            )
        except Exception:
            pass
    return result


def _heal_released_slot(slot: Slot, lease_id: str, owner: str, identity_id: str | None) -> dict[str, Any]:
    return _heal_slot_after_failed_cdp_check(
        slot,
        lease_id=lease_id,
        owner=owner,
        identity_id=identity_id,
        context="release",
    )


def reconcile_active_leases() -> list[dict[str, Any]]:
    with locked_state() as state:
        active_leases = [(lease_id, dict(lease_data)) for lease_id, lease_data in state["leases"].items()]
    results: list[dict[str, Any]] = []
    for lease_id, lease_data in active_leases:
        slot = _slot_by_name(str(lease_data["name"]))
        if slot is None:
            continue
        heal_result = _heal_slot_after_failed_cdp_check(
            slot,
            lease_id=lease_id,
            owner=str(lease_data.get("owner") or "broker-api"),
            identity_id=str(lease_data["identity_id"]) if lease_data.get("identity_id") else None,
            context="startup",
        )
        results.append({"lease_id": lease_id, **heal_result})
    return results


def _identity_lease_count(state: dict[str, Any], identity_id: str) -> int:
    return sum(1 for item in state["leases"].values() if item.get("identity_id") == identity_id)


def _identity_profile_for_slot(identity_id: str, identity: Any, slot: Slot, existing_count: int, active_identity: str | None) -> str:
    config = read_slot_config(slot.name)
    configured_profile = config.get("PROFILE_DIR")
    if active_identity == identity_id and configured_profile:
        return configured_profile
    if getattr(identity, "max_parallel_sessions", 1) > 1 and existing_count > 0:
        return str(identity_replica_profile_dir(identity, slot.name))
    return str(identity.profile_dir)


def _profile_cookie_score(profile_dir: Path) -> int:
    score = 0
    for relative in ("Default/Cookies", "Default/Network/Cookies"):
        db_path = profile_dir / relative
        if not db_path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=1)
            try:
                row = connection.execute("select count(*), count(distinct host_key) from cookies").fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            continue
        if row:
            score += int(row[0] or 0) + int(row[1] or 0)
    return score


def _ordered_slots_for_identity(identity_id: str, identity: Any) -> list[Slot]:
    def sort_key(slot: Slot) -> tuple[int, int, int]:
        active_identity = active_identity_id(slot.name)
        if active_identity != identity_id:
            return (1, 0, slot.port)
        profile_dir = read_slot_config(slot.name).get("PROFILE_DIR")
        score = _profile_cookie_score(Path(profile_dir)) if profile_dir else 0
        return (0, -score, slot.port)

    return sorted(SLOTS, key=sort_key)


def _is_replica_profile(profile_dir: str | None) -> bool:
    if not profile_dir:
        return False
    try:
        Path(profile_dir).resolve().relative_to((BROWSER_POOL_DIR / "profiles" / ".replicas").resolve())
    except ValueError:
        return False
    return True


def _cookie_db_mtime(profile_dir: Path) -> float:
    """Newest mtime across a profile's cookie databases (0.0 if none exist)."""
    newest = 0.0
    for relative in ("Default/Cookies", "Default/Network/Cookies"):
        db_path = profile_dir / relative
        try:
            mtime = db_path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            newest = mtime
    return newest


def _replica_is_stale_against_base(base_profile_dir: Path, replica_profile_dir: Path) -> bool:
    """True when the base profile has cookies newer than the warm replica copy.

    The auth portal logs the human in against the identity's BASE profile dir, but
    parallel-session leases are served from per-slot replicas under .replicas/.
    A replica synced before the human's login never picks up the new cookies, so the
    agent lease would see a logged-out session. When the base cookie DB is newer than
    the replica's, the replica must be re-synced from base before it is leased.
    """
    if base_profile_dir.resolve() == replica_profile_dir.resolve():
        return False
    base_mtime = _cookie_db_mtime(base_profile_dir)
    if base_mtime <= 0.0:
        return False
    replica_mtime = _cookie_db_mtime(replica_profile_dir)
    # Newer base cookies (with a small tolerance to avoid churning on equal stamps).
    return base_mtime > replica_mtime + 1.0


def _has_duplicate_profile_slot(identity_id: str, selected_slot: str, selected_profile_dir: str | None) -> bool:
    if not selected_profile_dir:
        return False
    for slot in SLOTS:
        if slot.name == selected_slot:
            continue
        if active_identity_id(slot.name) == identity_id:
            profile_dir = read_slot_config(slot.name).get("PROFILE_DIR")
            if profile_dir == selected_profile_dir:
                return True
    return False


def lease(owner: str, ttl_seconds: int = LEASE_TTL_SECONDS, identity_id: str | None = None, headed: bool = False) -> Lease:
    now = int(time.time())
    effective_ttl = max(60, min(int(ttl_seconds), LEASE_TTL_SECONDS))
    allowed_slot = None
    identity_profile_dir = None
    identity = None
    if identity_id:
        identity = require_identity(identity_id)
        allowed_slot = None if identity.slot == "auto" else identity.slot
    with locked_state() as state:
        gc_leases(state)
        identity_lease_count = _identity_lease_count(state, identity_id) if identity_id else 0
        if identity_id and identity and identity_lease_count >= getattr(identity, "max_parallel_sessions", 1):
            raise LeaseError(f"Identity already leased at max parallel sessions: {identity_id}")
        in_use = {str(item["name"]) for item in state["leases"].values()}
        identities = load_identities()
        reclaimable_slots: list[Slot] = []
        activation_errors: list[str] = []
        slots = _ordered_slots_for_identity(identity_id, identity) if identity_id and identity else list(SLOTS)
        for slot in slots:
            if allowed_slot and slot.name != allowed_slot:
                continue
            if slot.name in in_use:
                continue
            if slot_in_maintenance(slot.name):
                continue
            active_identity = active_identity_id(slot.name)
            if not identity_id and active_identity:
                if _can_reclaim_for_generic(active_identity, identities):
                    reclaimable_slots.append(slot)
                continue
            if identity_id and identity and identity.slot == "auto" and active_identity and active_identity != identity_id:
                active_config = identities.get(active_identity)
                if active_config and active_config.slot != "auto":
                    continue
            if identity_id and identity:
                identity_profile_dir = _identity_profile_for_slot(identity_id, identity, slot, identity_lease_count, active_identity)
            reconcile_stale_identity_slots = bool(
                identity_id
                and identity
                and identity.slot == "auto"
                and identity_lease_count == 0
                and _has_duplicate_profile_slot(identity_id, slot.name, identity_profile_dir)
            )
            # If this lease would be served from a warm replica whose cookies predate
            # the identity's base profile (e.g. a human just completed a login in the
            # auth portal, which writes to the base profile), force a re-activation so
            # the replica is re-synced from base and the agent sees the fresh session.
            replica_needs_refresh = bool(
                identity_id
                and identity
                and active_identity == identity_id
                and _is_replica_profile(identity_profile_dir)
                and _replica_is_stale_against_base(
                    identity.profile_dir, Path(identity_profile_dir)
                )
            )
            if replica_needs_refresh:
                try:
                    from .telemetry import record_event

                    record_event(
                        source=owner,
                        event_type="lease",
                        message="Refreshing stale identity replica from base before lease",
                        severity="info",
                        tags=["lease", "replica", "auth-sync"],
                        data={
                            "slot": slot.name,
                            "identity_id": identity_id,
                            "replica_profile_dir": identity_profile_dir,
                            "base_profile_dir": str(identity.profile_dir),
                        },
                    )
                except Exception:
                    pass
            if identity_id and (
                active_identity != identity_id
                or not _slot_ready(slot)
                or reconcile_stale_identity_slots
                or replica_needs_refresh
                or (headed and not _slot_headed_ready(slot))
            ):
                try:
                    activation_kwargs = {
                        "profile_dir_override": Path(identity_profile_dir) if identity_profile_dir else None,
                        "clear_existing": reconcile_stale_identity_slots
                        or getattr(identity, "max_parallel_sessions", 1) <= 1,
                    }
                    if headed:
                        activation_kwargs["headed"] = True
                    activate_identity(
                        identity_id,
                        slot.name,
                        check_leases=False,
                        **activation_kwargs,
                    )
                except (IdentityError, subprocess.CalledProcessError) as error:
                    activation_errors.append(f"{slot.name}: {error}")
                    continue
            if not _slot_ready(slot):
                continue
            if headed and not _slot_headed_ready(slot):
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
            if identity_id:
                state["leases"][lease_id]["identity_id"] = identity_id
            if identity_profile_dir:
                state["leases"][lease_id]["profile_dir"] = identity_profile_dir
            if headed:
                state["leases"][lease_id]["headed"] = True
            return _lease_from_state(lease_id, state["leases"][lease_id])
        if not identity_id:
            for slot in reclaimable_slots:
                if slot.name in in_use:
                    continue
                if not _activate_neutral_slot(slot):
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
                    "profile_dir": str(slot.profile_dir),
                }
                return _lease_from_state(lease_id, state["leases"][lease_id])
    if identity_id and activation_errors:
        raise LeaseError("No healthy free browser slots; activation failures: " + "; ".join(activation_errors))
    raise LeaseError("No healthy free browser slots")


def release(lease_id: str) -> dict[str, Any]:
    released_lease: dict[str, Any] | None = None
    with locked_state() as state:
        if lease_id in state["leases"]:
            released_lease = dict(state["leases"][lease_id])
            slot = str(released_lease["name"])
            del state["leases"][lease_id]
        else:
            return {"released": None, "slot": None}
    slot_obj = _slot_by_name(str(released_lease["name"]))
    heal_result = None
    if slot_obj is not None:
        heal_result = _heal_released_slot(
            slot_obj,
            lease_id,
            str(released_lease.get("owner") or "broker-api"),
            str(released_lease["identity_id"]) if released_lease.get("identity_id") else None,
        )
    return {
        "released": lease_id,
        "slot": slot,
        "port": slot_obj.port if slot_obj else released_lease.get("port"),
        "identity_id": released_lease.get("identity_id"),
        "self_heal": heal_result,
    }


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
    slot = _slot_by_name(lease_obj.name)
    if slot is None or not _slot_ready(slot):
        raise LeaseError(f"Browser slot {lease_obj.name} is not healthy")
    return lease_obj


def status() -> dict[str, Any]:
    with locked_state() as state:
        expired = gc_leases(state)
        leases = {lease_id: asdict(_lease_from_state(lease_id, lease)) for lease_id, lease in state["leases"].items()}
    leased_names = {lease["name"] for lease in leases.values()}
    slot_health = {
        slot.name: {
            "cdp_healthy": slot_cdp_healthy(slot),
            "proxy_ready": _slot_proxy_ready(slot.name),
        }
        for slot in SLOTS
    }
    return {
        "slots": [
            {
                "name": slot.name,
                "port": slot.port,
                "cdp": slot.cdp,
                "profile_dir": str(slot.profile_dir),
                "active_identity_id": active_identity_id(slot.name),
                "healthy": slot_health[slot.name]["cdp_healthy"] and slot_health[slot.name]["proxy_ready"],
                "cdp_healthy": slot_health[slot.name]["cdp_healthy"],
                "proxy_ready": slot_health[slot.name]["proxy_ready"],
                "leased": slot.name in leased_names,
                "maintenance": slot_in_maintenance(slot.name),
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
