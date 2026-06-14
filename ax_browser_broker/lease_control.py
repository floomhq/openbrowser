from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from contextlib import contextmanager
from typing import Any

from .auth import _url_from_base
from .config import BROKER_PORT, LEASE_CONTROL_STATE_FILE, LEASE_CONTROL_TTL_SECONDS, PUBLIC_AUTH_BASE_URL, ensure_dirs


class LeaseControlError(RuntimeError):
    pass


@contextmanager
def locked_control_state() -> Any:
    ensure_dirs()
    if not LEASE_CONTROL_STATE_FILE.exists():
        LEASE_CONTROL_STATE_FILE.write_text(json.dumps({"sessions": {}}, indent=2), encoding="utf-8")
        os.chmod(LEASE_CONTROL_STATE_FILE, 0o600)
    with LEASE_CONTROL_STATE_FILE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        raw = handle.read().strip()
        state = json.loads(raw) if raw else {"sessions": {}}
        state.setdefault("sessions", {})
        yield state
        handle.seek(0)
        handle.truncate(0)
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _local_control_url(token: str) -> str:
    return f"http://127.0.0.1:{BROKER_PORT}/auth/lease-control/{token}"


def control_portal_url(token: str) -> str:
    if PUBLIC_AUTH_BASE_URL:
        return _url_from_base(PUBLIC_AUTH_BASE_URL, f"auth/lease-control/{token}")
    return _local_control_url(token)


def gc_control_sessions(state: dict[str, Any]) -> list[str]:
    now = int(time.time())
    expired = []
    for token, session in list(state["sessions"].items()):
        if now > int(session.get("expires_at", 0)):
            expired.append(token)
            del state["sessions"][token]
    return expired


def create_control_session(
    owner: str,
    lease_id: str,
    ttl_seconds: int = LEASE_CONTROL_TTL_SECONDS,
    *,
    identity_id: str | None = None,
    url: str | None = None,
    reason: str | None = None,
    slot: str | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    ttl = max(60, min(int(ttl_seconds), 60 * 60))
    token = secrets.token_urlsafe(24)
    session = {
        "token": token,
        "owner": owner,
        "lease_id": lease_id,
        "created_at": now,
        "expires_at": now + ttl,
        "ttl_seconds": ttl,
        "portal_url": control_portal_url(token),
        "local_portal_url": _local_control_url(token),
    }
    if identity_id:
        session["identity_id"] = identity_id
    if url:
        session["url"] = url
    if reason:
        session["reason"] = reason
    if slot:
        session["slot"] = slot
    with locked_control_state() as state:
        gc_control_sessions(state)
        state["sessions"][token] = session
    return session


def get_control_session(token: str) -> dict[str, Any]:
    with locked_control_state() as state:
        gc_control_sessions(state)
        session = state["sessions"].get(token)
        if not session:
            raise LeaseControlError("Lease control session not found or expired")
        return dict(session)


def _redact_control_session(session: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in session.items() if key not in {"token", "portal_url", "local_portal_url"}}


def list_control_sessions(limit: int = 200, include_sensitive: bool = False) -> dict[str, Any]:
    bounded = max(0, min(int(limit), 500))
    with locked_control_state() as state:
        expired = gc_control_sessions(state)
        sessions = list(state["sessions"].values())
    sessions.sort(key=lambda item: int(item.get("created_at", 0)), reverse=True)
    visible = [dict(item) if include_sensitive else _redact_control_session(dict(item)) for item in sessions[:bounded]]
    return {"sessions": visible, "count": len(visible), "total_count": len(sessions), "expired": expired}


def complete_control_session(token: str) -> dict[str, Any]:
    with locked_control_state() as state:
        gc_control_sessions(state)
        session = state["sessions"].pop(token, None)
        if not session:
            raise LeaseControlError("Lease control session not found or expired")
        session["completed_at"] = int(time.time())
        return dict(session)
