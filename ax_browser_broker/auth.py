from __future__ import annotations

import fcntl
import json
import os
import signal
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import (
    AUTH_REQUEST_TTL_SECONDS,
    AUTH_STATE_FILE,
    AUTHENTICATED_PROFILE_DIR,
    BROWSER_POOL_DIR,
    BROWSER_POOL_MAINTENANCE_DIR,
    BROKER_PORT,
    PUBLIC_AUTH_BASE_URL,
    PUBLIC_NOVNC_BASE_URL,
    ROOT,
    SLOTS,
    ensure_dirs,
)
from .identities import read_slot_config, require_identity
from .pool import status as pool_status


class AuthError(RuntimeError):
    pass


def _url_from_base(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _local_auth_portal_url(token: str) -> str:
    return f"http://127.0.0.1:{BROKER_PORT}/auth/{token}"


def auth_portal_url(token: str) -> str:
    if PUBLIC_AUTH_BASE_URL:
        return _url_from_base(PUBLIC_AUTH_BASE_URL, f"auth/{token}")
    return _local_auth_portal_url(token)


def _local_novnc_url(websocket_port: int) -> str:
    return f"http://127.0.0.1:{websocket_port}/vnc.html?autoconnect=1&resize=remote"


def novnc_url(websocket_port: int) -> str:
    if PUBLIC_NOVNC_BASE_URL:
        return _url_from_base(PUBLIC_NOVNC_BASE_URL, "vnc.html?autoconnect=1&resize=remote")
    return _local_novnc_url(websocket_port)


def current_auth_vnc(token: str) -> dict[str, Any] | None:
    request = get_auth_request(token)
    if request.get("status") != "pending" or int(request.get("expires_at", 0)) <= int(time.time()):
        return None
    vnc = request.get("vnc") or {}
    if not vnc or vnc.get("stopped_at"):
        return None
    password_file = vnc.get("password_file")
    if not password_file:
        return None
    try:
        password = Path(str(password_file)).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    websocket_port = int(vnc.get("websocket_port", 6081))
    vnc_port = int(vnc.get("vnc_port", 5901))
    return {
        "token": token,
        "display": str(vnc.get("display", "")),
        "websocket_url": novnc_url(websocket_port),
        "local_websocket_url": _local_novnc_url(websocket_port),
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "password": password,
        "log": str(vnc.get("log", "")),
        "mode": vnc.get("mode"),
        "identity_id": vnc.get("identity_id"),
    }


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
            vnc = request.get("vnc") or {}
            if vnc and not vnc.get("stopped_at"):
                vnc["stopped_at"] = now
                vnc["expired_at"] = now
                stopped = []
                for key in ("x11vnc_pid", "websockify_pid", "chrome_pid", "xvfb_pid", "proxy_pid"):
                    pid = vnc.get(key)
                    if not pid:
                        continue
                    int_pid = int(pid)
                    if _terminate_process_group(int_pid):
                        stopped.append(int_pid)
                vnc["stopped_pids"] = stopped
                maintenance_slots = [str(item) for item in vnc.get("maintenance_slots", [])]
                vnc["cleared_maintenance_slots"] = _clear_auth_maintenance(maintenance_slots)
                password_file = vnc.get("password_file")
                if password_file:
                    try:
                        Path(str(password_file)).unlink(missing_ok=True)
                    except OSError:
                        pass
            expired.append(token)
    return expired


def create_auth_request(
    owner: str,
    url: str,
    reason: str = "login_required",
    identity_id: str | None = None,
    mode: str = "vnc",
    lease_id: str | None = None,
    slot: str | None = None,
    cdp: str | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    normalized_mode = mode.strip().lower().replace("-", "_")
    if normalized_mode not in {"lease_control", "same_lease", "vnc"}:
        raise AuthError("mode must be lease_control, same_lease, or vnc")
    if identity_id:
        require_identity(identity_id)
    token = secrets.token_urlsafe(24)
    request = {
        "token": token,
        "owner": owner,
        "url": url,
        "reason": reason,
        "mode": normalized_mode,
        "status": "pending",
        "created_at": now,
        "expires_at": now + AUTH_REQUEST_TTL_SECONDS,
        "portal_url": auth_portal_url(token),
        "local_portal_url": _local_auth_portal_url(token),
    }
    if identity_id:
        request["identity_id"] = identity_id
    if lease_id:
        request["lease_id"] = lease_id
    if slot:
        request["slot"] = slot
    if cdp:
        request["cdp"] = cdp
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


def get_pending_auth_request(token: str) -> dict[str, Any]:
    request = get_auth_request(token)
    if request.get("status") != "pending":
        raise AuthError(f"Auth request is {request.get('status', 'not pending')}")
    if int(request.get("expires_at", 0)) <= int(time.time()):
        raise AuthError("Auth request expired")
    return request


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
        if request.get("status") != "pending":
            raise AuthError(f"Auth request is {request.get('status', 'not pending')}")
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
            if f"--user-data-dir={AUTHENTICATED_PROFILE_DIR}" in item["args"]
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


def _find_free_tcp_port(start: int = 18900, end: int = 18999) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise AuthError("No free local proxy port found for identity auth")


def _terminate_tcp_listeners(port: int) -> list[int]:
    try:
        output = subprocess.check_output(["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"], text=True)
    except Exception:
        return []
    stopped: list[int] = []
    for line in output.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if _terminate_process_group(pid, timeout_seconds=3.0):
            stopped.append(pid)
    return stopped


def _stop_conflicting_auth_vncs(token: str, websocket_port: int, vnc_port: int) -> None:
    with locked_auth_state() as state:
        candidates = [
            item_token
            for item_token, item in state.get("requests", {}).items()
            if item_token != token
            and item.get("vnc")
            and not item.get("vnc", {}).get("stopped_at")
            and (
                int(item.get("vnc", {}).get("websocket_port", 0) or 0) == websocket_port
                or int(item.get("vnc", {}).get("vnc_port", 0) or 0) == vnc_port
            )
        ]
    for item_token in candidates:
        try:
            stop_auth_vnc(item_token, missing_ok=True)
        except AuthError:
            pass
    _terminate_tcp_listeners(websocket_port)
    _terminate_tcp_listeners(vnc_port)


def _identity_auth_slots(identity_id: str, profile_dir: Path, configured_slot: str) -> list[str]:
    slots: list[str] = []
    if configured_slot != "auto":
        slots.append(configured_slot)
    for slot in SLOTS:
        try:
            config = read_slot_config(slot.name)
        except Exception:
            continue
        if config.get("IDENTITY_ID") == identity_id or config.get("PROFILE_DIR") == str(profile_dir):
            slots.append(slot.name)
    return sorted(set(slots))


def _write_auth_maintenance(slot_names: list[str], request: dict[str, Any]) -> list[str]:
    ensure_dirs()
    written: list[str] = []
    now = int(time.time())
    for slot_name in slot_names:
        path = BROWSER_POOL_MAINTENANCE_DIR / f"{slot_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "slot": slot_name,
            "reason": "identity_auth",
            "identity_id": request.get("identity_id"),
            "owner": request.get("owner"),
            "created_at": now,
            "expires_at": int(request.get("expires_at", now + AUTH_REQUEST_TTL_SECONDS)),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        written.append(slot_name)
    return written


def _clear_auth_maintenance(slot_names: list[str]) -> list[str]:
    cleared: list[str] = []
    for slot_name in slot_names:
        path = BROWSER_POOL_MAINTENANCE_DIR / f"{slot_name}.json"
        try:
            path.unlink(missing_ok=True)
            cleared.append(slot_name)
        except OSError:
            pass
    return cleared


def _process_rows() -> list[tuple[int, str]]:
    try:
        output = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True)
    except subprocess.SubprocessError:
        return []
    rows: list[tuple[int, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, args = stripped.partition(" ")
        try:
            rows.append((int(pid_text), args))
        except ValueError:
            continue
    return rows


def _is_chrome_process(args: str) -> bool:
    executable = Path(args.split(maxsplit=1)[0]).name if args else ""
    return executable in {"chrome", "google-chrome", "google-chrome-stable"} or executable.startswith("chrome_crashpad")


def _terminate_pids(pids: list[int]) -> None:
    for pid in sorted(set(pids), reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue


def _kill_identity_pool_processes(_identity_id: str, profile_dir: Path, slot_names: list[str]) -> None:
    rows = _process_rows()
    profile_arg = f"--user-data-dir={profile_dir}"
    profile_pids = [pid for pid, args in rows if profile_arg in args and _is_chrome_process(args)]
    _terminate_pids(profile_pids)
    for slot_name in slot_names:
        slot = next((item for item in SLOTS if item.name == slot_name), None)
        if slot is None:
            continue
        port_arg = f"--remote-debugging-port={slot.port}"
        rows = _process_rows()
        port_pids = [pid for pid, args in rows if port_arg in args and _is_chrome_process(args)]
        _terminate_pids(port_pids)
        proxy_pid_file = BROWSER_POOL_DIR / "state" / f"{slot_name}.proxy.pid"
        if proxy_pid_file.exists():
            try:
                proxy_pid = int(proxy_pid_file.read_text(encoding="utf-8").strip())
                os.kill(proxy_pid, signal.SIGTERM)
            except (OSError, ValueError):
                pass
            try:
                proxy_pid_file.unlink(missing_ok=True)
            except OSError:
                pass


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
    auth_slots = _identity_auth_slots(identity.identity_id, identity.profile_dir, identity.slot)
    maintenance_slots = _write_auth_maintenance(auth_slots, request)
    identity.profile_dir.mkdir(parents=True, exist_ok=True)
    _kill_identity_pool_processes(identity.identity_id, identity.profile_dir, maintenance_slots)
    time.sleep(0.5)
    display = _find_free_display()
    env = os.environ.copy()
    env["DISPLAY"] = display
    started_pids: list[int] = []
    proxy_pid = None
    proxy_local_port = None
    proxy_args: list[str] = []
    cdp_port = _find_free_tcp_port(19400, 19499)
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
            if getattr(identity, "proxy_ref", None):
                proxy_forwarder = ROOT / "bin" / "ax-proxy-forwarder"
                if not proxy_forwarder.exists():
                    raise AuthError("ax-proxy-forwarder is missing")
                proxy_local_port = _find_free_tcp_port()
                proxy_proc = subprocess.Popen(
                    [
                        str(proxy_forwarder),
                        "--proxy-ref",
                        str(identity.proxy_ref),
                        "--listen-host",
                        "127.0.0.1",
                        "--listen-port",
                        str(proxy_local_port),
                    ],
                    stdout=log,
                    stderr=log,
                    env=env,
                    start_new_session=True,
                )
                proxy_pid = proxy_proc.pid
                started_pids.append(proxy_proc.pid)
                proxy_args = [f"--proxy-server=http://127.0.0.1:{proxy_local_port}"]
                time.sleep(0.4)
            chrome_proc = subprocess.Popen(
                [
                    chrome,
                    f"--user-data-dir={identity.profile_dir}",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-gpu-sandbox",
                    "--use-gl=swiftshader",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-component-extensions-with-background-pages",
                    "--no-first-run",
                    f"--remote-debugging-port={cdp_port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-allow-origins=*",
                    f"--lang={identity.lang}",
                    "--window-size=1280,800",
                    "--window-position=0,0",
                    *proxy_args,
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
            _clear_auth_maintenance(maintenance_slots)
            raise
    return {
        "mode": "identity",
        "identity_id": identity.identity_id,
        "maintenance_slots": maintenance_slots,
        "display": display,
        "xvfb_pid": xvfb_proc.pid,
        "chrome_pid": chrome_proc.pid,
        "x11vnc_pid": x11vnc_proc.pid,
        "websockify_pid": websockify_proc.pid,
        "proxy_pid": proxy_pid,
        "proxy_local_port": proxy_local_port,
        "cdp_port": cdp_port,
        "cdp": f"http://127.0.0.1:{cdp_port}",
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "password_file": str(password_file),
        "started_at": int(time.time()),
        "profile_dir": str(identity.profile_dir),
    }


def start_auth_vnc(token: str, websocket_port: int = 6081, vnc_port: int = 5901) -> dict[str, Any]:
    request = get_pending_auth_request(token)
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
    runtime_dir = ROOT / "state" / "vnc"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    password_file = runtime_dir / f"{token}.passwd"
    log_path = runtime_dir / f"{token}.log"

    stop_auth_vnc(token, missing_ok=True)
    _stop_conflicting_auth_vncs(token, websocket_port, vnc_port)

    password = secrets.token_urlsafe(12)
    password_file.write_text(password + "\n", encoding="utf-8")
    os.chmod(password_file, 0o600)

    try:
        if request.get("mode") == "same_lease":
            vnc_state = _start_lease_vnc(request, websocket_port, vnc_port, password_file, log_path)
            display = str(vnc_state["display"])
        elif request.get("identity_id"):
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
        "websocket_url": novnc_url(websocket_port),
        "local_websocket_url": _local_novnc_url(websocket_port),
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "cdp_port": vnc_state.get("cdp_port"),
        "cdp": vnc_state.get("cdp"),
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


def _close_chrome_via_cdp(cdp_port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        websocket_url = payload.get("webSocketDebuggerUrl")
        if not websocket_url:
            return False
        import websocket

        ws = websocket.create_connection(websocket_url, timeout=3)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            time.sleep(2.0)
            return True
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception:
        return False


def _port_accepts_connections(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_process_port(proc: subprocess.Popen[Any], port: int, log_path: Path, label: str, timeout_seconds: float = 5.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _port_accepts_connections(port):
            return
        time.sleep(0.1)
    error_tail = ""
    try:
        error_tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
    except OSError:
        pass
    raise AuthError(f"{label} failed to listen on 127.0.0.1:{port}: {error_tail}")


def _start_lease_vnc(
    request: dict[str, Any],
    websocket_port: int,
    vnc_port: int,
    password_file: Path,
    log_path: Path,
) -> dict[str, Any]:
    from .pool import require_lease

    lease_id = str(request.get("lease_id") or "")
    if not lease_id:
        raise AuthError("same_lease auth request is missing lease_id")
    lease = require_lease(lease_id)
    display_file = BROWSER_POOL_DIR / "state" / f"{lease.name}.display"
    try:
        display = display_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise AuthError(f"Headed display is not available for lease slot {lease.name}") from error
    if not display:
        raise AuthError(f"Headed display is empty for lease slot {lease.name}")
    x11vnc = shutil.which("x11vnc")
    websockify = shutil.which("websockify")
    if not x11vnc or not websockify:
        raise AuthError("x11vnc or websockify is missing")
    env = os.environ.copy()
    env["DISPLAY"] = display
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
    try:
        _wait_for_process_port(x11vnc_proc, vnc_port, log_path, "x11vnc")
        _wait_for_process_port(websockify_proc, websocket_port, log_path, "websockify")
    except AuthError:
        _terminate_process_group(x11vnc_proc.pid)
        _terminate_process_group(websockify_proc.pid)
        raise
    return {
        "mode": "same-lease",
        "lease_id": lease_id,
        "slot": lease.name,
        "identity_id": lease.identity_id,
        "display": display,
        "x11vnc_pid": x11vnc_proc.pid,
        "websockify_pid": websockify_proc.pid,
        "websocket_port": websocket_port,
        "vnc_port": vnc_port,
        "password_file": str(password_file),
        "started_at": int(time.time()),
        "profile_dir": lease.profile_dir,
        "cdp": lease.cdp,
    }


def stop_auth_vnc(token: str, missing_ok: bool = False) -> dict[str, Any]:
    try:
        request = get_auth_request(token)
    except AuthError:
        if missing_ok:
            return {"token": token, "stopped": []}
        raise
    vnc = request.get("vnc") or {}
    stopped = []
    cdp_port = vnc.get("cdp_port")
    if cdp_port:
        _close_chrome_via_cdp(int(cdp_port))
    for key in ("x11vnc_pid", "websockify_pid", "chrome_pid", "xvfb_pid", "proxy_pid"):
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
    maintenance_slots = [str(item) for item in vnc.get("maintenance_slots", [])]
    cleared_maintenance = _clear_auth_maintenance(maintenance_slots)
    with locked_auth_state() as state:
        state_request = state["requests"].get(token)
        if state_request is not None and "vnc" in state_request:
            state_request["vnc"]["stopped_at"] = int(time.time())
            state_request["vnc"]["stopped_pids"] = stopped
            state_request["vnc"]["cleared_maintenance_slots"] = cleared_maintenance
    return {"token": token, "stopped": stopped, "cleared_maintenance_slots": cleared_maintenance}
