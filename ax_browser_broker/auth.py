from __future__ import annotations

import fcntl
import json
import os
import signal
import secrets
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import AUTH_REQUEST_TTL_SECONDS, AUTH_STATE_FILE, BROKER_PORT, ensure_dirs
from .identities import require_identity
from .pool import status as pool_status


class AuthError(RuntimeError):
    pass


@contextmanager
def locked_auth_state() -> Any:
    ensure_dirs()
    if not AUTH_STATE_FILE.exists():
        AUTH_STATE_FILE.write_text(json.dumps({"requests": {}}, indent=2), encoding="utf-8")
    with AUTH_STATE_FILE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        raw = handle.read().strip()
        state = json.loads(raw) if raw else {"requests": {}}
        state.setdefault("requests", {})
        yield state
        handle.seek(0)
        handle.truncate(0)
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def gc_auth_requests(state: dict[str, Any]) -> list[str]:
    now = int(time.time())
    expired = []
    for token, request in list(state["requests"].items()):
        if request.get("status") == "pending" and now > int(request.get("expires_at", 0)):
            request["status"] = "expired"
            expired.append(token)
    return expired


def create_auth_request(owner: str, url: str, reason: str = "login_required", identity_id: str | None = None) -> dict[str, Any]:
    now = int(time.time())
    if identity_id:
        require_identity(identity_id)
    token = secrets.token_urlsafe(24)
    request = {
        "token": token,
        "owner": owner,
        "url": url,
        "reason": reason,
        "status": "pending",
        "created_at": now,
        "expires_at": now + AUTH_REQUEST_TTL_SECONDS,
        "portal_url": f"http://127.0.0.1:{BROKER_PORT}/auth/{token}",
    }
    if identity_id:
        request["identity_id"] = identity_id
    with locked_auth_state() as state:
        gc_auth_requests(state)
        state["requests"][token] = request
    return request


def get_auth_request(token: str) -> dict[str, Any]:
    with locked_auth_state() as state:
        gc_auth_requests(state)
        request = state["requests"].get(token)
        if not request:
            raise AuthError("Auth request not found")
        return dict(request)


def list_auth_requests() -> dict[str, Any]:
    with locked_auth_state() as state:
        expired = gc_auth_requests(state)
        return {"requests": state["requests"], "expired": expired}


def complete_auth_request(token: str) -> dict[str, Any]:
    now = int(time.time())
    with locked_auth_state() as state:
        gc_auth_requests(state)
        request = state["requests"].get(token)
        if not request:
            raise AuthError("Auth request not found")
        request["status"] = "complete"
        request["completed_at"] = now
        return dict(request)


def _authenticated_x_display() -> tuple[str, str | None]:
    ps = subprocess.check_output(["ps", "-eo", "pid=,ppid=,args="], text=True)
    records = []
    for line in ps.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        records.append({"pid": parts[0], "ppid": parts[1], "args": parts[2]})
    chrome = next(
        (
            item
            for item in records
            if "--user-data-dir=/root/.config/authenticated-chrome" in item["args"]
            and "chrome" in item["args"]
        ),
        None,
    )
    if chrome:
        sibling_xvfb = next((item for item in records if item["ppid"] == chrome["ppid"] and "Xvfb :" in item["args"]), None)
        if sibling_xvfb:
            parts = sibling_xvfb["args"].split()
            display = next((part for part in parts if part.startswith(":")), None)
            auth_path = None
            for index, part in enumerate(parts):
                if part == "-auth" and index + 1 < len(parts):
                    auth_path = parts[index + 1]
            if display:
                return display, auth_path
    for item in records:
        line = item["args"]
        if "Xvfb :" not in line:
            continue
        parts = line.split()
        display = next((part for part in parts if part.startswith(":")), None)
        auth_path = None
        for index, part in enumerate(parts):
            if part == "-auth" and index + 1 < len(parts):
                auth_path = parts[index + 1]
        if display:
            return display, auth_path
    for line in ps.splitlines():
        if "Xvfb :" not in line:
            continue
        parts = line.split()
        display = next((part for part in parts if part.startswith(":")), None)
        auth_path = None
        for index, part in enumerate(parts):
            if part == "-auth" and index + 1 < len(parts):
                auth_path = parts[index + 1]
        if display:
            return display, auth_path
    raise AuthError("Authenticated Chrome X display not found")


def _find_free_display(start: int = 870, end: int = 899) -> str:
    for display_number in range(start, end + 1):
        if not Path(f"/tmp/.X11-unix/X{display_number}").exists():
            return f":{display_number}"
    raise AuthError("No free X display found for identity auth")


def _start_identity_auth_vnc(
    request: dict[str, Any],
    websocket_port: int,
    vnc_port: int,
    password_file: Path,
    log_path: Path,
) -> dict[str, Any]:
    identity = require_identity(str(request["identity_id"]))
    xvfb = shutil.which("Xvfb")
    x11vnc = shutil.which("x11vnc")
    websockify = shutil.which("websockify")
    chrome = shutil.which("google-chrome-stable") or shutil.which("google-chrome")
    if not xvfb or not x11vnc or not websockify or not chrome:
        raise AuthError("Xvfb, Chrome, x11vnc, or websockify is missing")
    active_leases = pool_status().get("leases", {})
    if any(item.get("identity_id") == identity.identity_id for item in active_leases.values()):
        raise AuthError(f"Identity is actively leased: {identity.identity_id}")
    if any(item.get("profile_dir") == str(identity.profile_dir) for item in active_leases.values()):
        raise AuthError(f"Identity profile is actively leased: {identity.identity_id}")
    identity.profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pkill", "-f", "--", f"--user-data-dir={identity.profile_dir}"], check=False)
    time.sleep(0.5)
    display = _find_free_display()
    env = os.environ.copy()
    env["DISPLAY"] = display
    started_pids: list[int] = []
    with log_path.open("ab") as log:
        try:
            xvfb_proc = subprocess.Popen(
                [xvfb, display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )
            started_pids.append(xvfb_proc.pid)
            time.sleep(0.4)
            chrome_proc = subprocess.Popen(
                [
                    chrome,
                    f"--user-data-dir={identity.profile_dir}",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-gpu-sandbox",
                    "--in-process-gpu",
                    "--use-gl=swiftshader",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    f"--lang={identity.lang}",
                    "--window-size=1280,800",
                    "--window-position=0,0",
                    str(request["url"]),
                ],
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )
            started_pids.append(chrome_proc.pid)
            time.sleep(0.7)
            x11vnc_proc = subprocess.Popen(
                [
                    x11vnc,
                    "-display",
                    display,
                    "-rfbport",
                    str(vnc_port),
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-passwdfile",
                    str(password_file),
                ],
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )
            started_pids.append(x11vnc_proc.pid)
            websockify_proc = subprocess.Popen(
                [
                    websockify,
                    "--web=/usr/share/novnc",
                    f"127.0.0.1:{websocket_port}",
                    f"127.0.0.1:{vnc_port}",
                ],
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )
        except Exception:
            for pid in reversed(started_pids):
                _terminate_process_group(pid)
            raise
    return {
        "mode": "identity",
        "identity_id": identity.identity_id,
        "display": display,
        "xvfb_pid": xvfb_proc.pid,
        "chrome_pid": chrome_proc.pid,
        "x11vnc_pid": x11vnc_proc.pid,
        "websockify_pid": websockify_proc.pid,
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "password_file": str(password_file),
        "started_at": int(time.time()),
        "profile_dir": str(identity.profile_dir),
    }


def start_auth_vnc(token: str, websocket_port: int = 6081, vnc_port: int = 5901) -> dict[str, Any]:
    request = get_auth_request(token)
    if request["status"] not in {"pending", "complete"}:
        raise AuthError(f"Auth request is {request['status']}")
    x11vnc = shutil.which("x11vnc")
    websockify = shutil.which("websockify")
    if not x11vnc or not websockify:
        raise AuthError("x11vnc or websockify is missing")
    display = ""
    auth_path = None
    if not request.get("identity_id"):
        display, auth_path = _authenticated_x_display()
    runtime_dir = Path("/root/ax-browser-broker/state/vnc")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    password_file = runtime_dir / f"{token}.passwd"
    password = secrets.token_urlsafe(12)
    password_file.write_text(password + "\n", encoding="utf-8")
    os.chmod(password_file, 0o600)
    log_path = runtime_dir / f"{token}.log"

    stop_auth_vnc(token, missing_ok=True)
    try:
        if request.get("identity_id"):
            vnc_state = _start_identity_auth_vnc(request, websocket_port, vnc_port, password_file, log_path)
            display = str(vnc_state["display"])
        else:
            env = os.environ.copy()
            env["DISPLAY"] = display
            if auth_path:
                env["XAUTHORITY"] = auth_path
            with log_path.open("ab") as log:
                x11vnc_proc = subprocess.Popen(
                    [
                        x11vnc,
                        "-display",
                        display,
                        "-rfbport",
                        str(vnc_port),
                        "-localhost",
                        "-forever",
                        "-shared",
                        "-passwdfile",
                        str(password_file),
                    ],
                    stdout=log,
                    stderr=log,
                    env=env,
                    start_new_session=True,
                )
                websockify_proc = subprocess.Popen(
                    [
                        websockify,
                        "--web=/usr/share/novnc",
                        f"127.0.0.1:{websocket_port}",
                        f"127.0.0.1:{vnc_port}",
                    ],
                    stdout=log,
                    stderr=log,
                    env=env,
                    start_new_session=True,
                )
            vnc_state = {
                "mode": "authenticated-chrome",
                "x11vnc_pid": x11vnc_proc.pid,
                "websockify_pid": websockify_proc.pid,
                "websocket_port": websocket_port,
                "vnc_port": vnc_port,
                "display": display,
                "password_file": str(password_file),
                "started_at": int(time.time()),
            }
    except Exception:
        try:
            password_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    time.sleep(0.5)
    with locked_auth_state() as state:
        state_request = state["requests"].get(token)
        if state_request is not None:
            state_request["vnc"] = vnc_state
    return {
        "token": token,
        "display": display,
        "websocket_url": f"http://127.0.0.1:{websocket_port}/vnc.html?autoconnect=1&resize=remote",
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "password": password,
        "log": str(log_path),
    }


def _process_gone(pid: int) -> bool:
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            parts = proc_stat.read_text(encoding="utf-8").split()
            if len(parts) > 2 and parts[2] == "Z":
                return True
        except OSError:
            pass
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def _terminate_process_group(pid: int, timeout_seconds: float = 2.0) -> bool:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _process_gone(pid):
            return True
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if _process_gone(pid):
            return True
        time.sleep(0.05)
    return _process_gone(pid)


def stop_auth_vnc(token: str, missing_ok: bool = False) -> dict[str, Any]:
    try:
        request = get_auth_request(token)
    except AuthError:
        if missing_ok:
            return {"token": token, "stopped": []}
        raise
    vnc = request.get("vnc") or {}
    stopped = []
    for key in ("x11vnc_pid", "websockify_pid", "chrome_pid", "xvfb_pid"):
        pid = vnc.get(key)
        if not pid:
            continue
        int_pid = int(pid)
        if _terminate_process_group(int_pid):
            stopped.append(int_pid)
    password_file = vnc.get("password_file")
    if password_file:
        try:
            Path(str(password_file)).unlink(missing_ok=True)
        except OSError:
            pass
    with locked_auth_state() as state:
        state_request = state["requests"].get(token)
        if state_request is not None and "vnc" in state_request:
            state_request["vnc"]["stopped_at"] = int(time.time())
            state_request["vnc"]["stopped_pids"] = stopped
    return {"token": token, "stopped": stopped}
