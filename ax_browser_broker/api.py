from __future__ import annotations

import hmac
import html
import json
import os
import base64
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .audit import run_audit
from .auth import (
    AuthError,
    complete_auth_request,
    create_auth_request,
    get_auth_request,
    list_auth_requests,
    start_auth_vnc,
    stop_auth_vnc,
)
from .browser import controller
from .config import BROKER_HOST, BROKER_PORT, OPENBROWSER_API_KEYS_FILE, PUBLIC_OPENBROWSER_BASE_URL, ensure_dirs
from .docs import docs
from .feedback import FeedbackError, list_issues, report_issue, update_issue
from .identities import redacted_status
from .lease_control import LeaseControlError, complete_control_session, create_control_session, get_control_session
from .pool import LeaseError, heartbeat, lease, release, require_lease, status
from .profiles import profile_status, seed_slot, snapshot_golden
from .telemetry import TelemetryError, list_events, record_event, summary


class LeaseRequest(BaseModel):
    owner: str = "unknown"
    ttl_seconds: int = Field(default=14400, ge=60, le=14400)
    identity_id: str | None = None


class LeaseIdRequest(BaseModel):
    lease_id: str


class NavigateRequest(LeaseIdRequest):
    url: str
    wait_until: str = "domcontentloaded"


class ScreenshotRequest(LeaseIdRequest):
    full_page: bool = False


class ClickRequest(LeaseIdRequest):
    selector: str


class TypeRequest(LeaseIdRequest):
    selector: str
    text: str
    submit: bool = False


class KeyboardTypeRequest(LeaseIdRequest):
    text: str
    selector: str | None = None
    delay_ms: int = Field(default=0, ge=0, le=1000)


class KeyboardPressRequest(LeaseIdRequest):
    key: str
    selector: str | None = None


class LeaseControlRequest(LeaseIdRequest):
    owner: str = "agent"
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class MouseClickRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class LeaseControlTypeRequest(BaseModel):
    text: str = Field(max_length=4000)
    delay_ms: int = Field(default=0, ge=0, le=1000)


class LeaseControlPressRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)


class WaitRequest(LeaseIdRequest):
    selector: str | None = None
    timeout_ms: int = Field(default=1000, ge=1, le=30000)


class NewTabRequest(LeaseIdRequest):
    url: str | None = None


class SwitchTabRequest(LeaseIdRequest):
    index: int


class UploadRequest(LeaseIdRequest):
    selector: str
    path: str


class AuthRequest(BaseModel):
    owner: str = "unknown"
    url: str
    reason: str = "login_required"
    identity_id: str | None = None


class SeedSlotRequest(BaseModel):
    slot: str
    force: bool = False


class FeedbackIssueRequest(BaseModel):
    source: str = "agent"
    title: str
    details: str
    severity: str = "medium"
    lease_id: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)


class FeedbackUpdateRequest(BaseModel):
    status: str | None = None
    note: str | None = None


class TelemetryEventRequest(BaseModel):
    source: str = "agent"
    event_type: str
    message: str
    severity: str = "info"
    lease_id: str | None = None
    issue_id: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class OpenBrowserOpenRequest(BaseModel):
    owner: str = "openbrowser-api"
    url: str
    identity_id: str | None = None
    ttl_seconds: int = Field(default=300, ge=60, le=14400)
    wait_until: str = "domcontentloaded"


class OpenBrowserAuthBatchRequest(BaseModel):
    owner: str = "openbrowser-api"
    identity_ids: list[str] = Field(min_length=1, max_length=20)
    url: str = "https://accounts.google.com/"
    reason: str = "profile_login"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    await controller.start()
    try:
        yield
    finally:
        await controller.stop()


app = FastAPI(title="OpenBrowser Broker", version="0.1.0", lifespan=lifespan)


def _http_error(error: Exception) -> HTTPException:
    status_code = 409 if isinstance(error, LeaseError) else 400
    return HTTPException(status_code=status_code, detail=str(error))


def _safe_record_event(**kwargs: Any) -> None:
    try:
        record_event(**kwargs)
    except Exception:
        return


def _record_browser_failure(request: LeaseIdRequest, action: str, error: Exception, data: dict[str, Any] | None = None) -> None:
    _safe_record_event(
        source="broker-api",
        event_type="error",
        message=f"Browser {action} failed",
        severity="error",
        lease_id=request.lease_id,
        tags=["browser", action, "failure"],
        data={"error": str(error), **(data or {})},
    )


def _configured_openbrowser_keys() -> list[str]:
    keys: list[str] = []
    for raw in (os.environ.get("OPENBROWSER_API_KEYS", ""), os.environ.get("AX_OPENBROWSER_API_KEYS", "")):
        keys.extend(item.strip() for item in raw.split(",") if item.strip())
    if OPENBROWSER_API_KEYS_FILE.exists():
        try:
            data = json.loads(OPENBROWSER_API_KEYS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data.get("keys"), list):
            keys.extend(str(item).strip() for item in data["keys"] if str(item).strip())
        if isinstance(data.get("tokens"), dict):
            keys.extend(str(item).strip() for item in data["tokens"].values() if str(item).strip())
    return keys


def require_openbrowser_api_key(
    authorization: str | None = Header(default=None),
    x_openbrowser_key: str | None = Header(default=None),
) -> str:
    token = ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = value.strip()
    if not token and x_openbrowser_key:
        token = x_openbrowser_key.strip()
    configured = _configured_openbrowser_keys()
    if not configured:
        raise HTTPException(status_code=503, detail="OpenBrowser API keys are not configured")
    if token and any(hmac.compare_digest(token, key) for key in configured):
        return "openbrowser-api"
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "broker": f"http://{BROKER_HOST}:{BROKER_PORT}", "pool": status()}


@app.get("/status")
async def get_status() -> dict[str, Any]:
    return status()


@app.get("/agent-docs")
async def agent_docs(topic: str = "quickstart") -> dict[str, Any]:
    return docs(topic)


@app.get("/audit")
async def broker_audit(hours: int = 24) -> dict[str, Any]:
    return run_audit(hours)


@app.get("/openbrowser/v1/health")
async def openbrowser_health(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "openbrowser",
        "base_url": PUBLIC_OPENBROWSER_BASE_URL + "/openbrowser/v1" if PUBLIC_OPENBROWSER_BASE_URL else "/openbrowser/v1",
        "pool": status(),
    }


@app.get("/openbrowser/v1/docs")
async def openbrowser_docs(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return {
        "service": "openbrowser",
        "version": "v1",
        "auth": "Authorization: Bearer <OPENBROWSER_API_KEY>",
        "endpoints": {
            "health": "GET /openbrowser/v1/health",
            "identities": "GET /openbrowser/v1/identities",
            "auth_request": "POST /openbrowser/v1/auth/request",
            "auth_batch": "POST /openbrowser/v1/auth/batch",
            "auth_status": "GET /openbrowser/v1/auth/status",
            "audit": "GET /openbrowser/v1/audit",
            "profile_status": "GET /openbrowser/v1/profiles/status",
            "lease": "POST /openbrowser/v1/leases",
            "release": "POST /openbrowser/v1/leases/{lease_id}/release",
            "heartbeat": "POST /openbrowser/v1/leases/{lease_id}/heartbeat",
            "open": "POST /openbrowser/v1/open",
            "navigate": "POST /openbrowser/v1/browser/navigate",
            "snapshot": "POST /openbrowser/v1/browser/snapshot",
            "screenshot": "POST /openbrowser/v1/browser/screenshot",
            "click": "POST /openbrowser/v1/browser/click",
            "type": "POST /openbrowser/v1/browser/type",
            "keyboard_type": "POST /openbrowser/v1/browser/keyboard-type",
            "keyboard_press": "POST /openbrowser/v1/browser/keyboard-press",
            "lease_control_request": "POST /openbrowser/v1/lease-control/request",
            "wait": "POST /openbrowser/v1/browser/wait",
            "tabs": "POST /openbrowser/v1/browser/tabs",
            "new_tab": "POST /openbrowser/v1/browser/new-tab",
            "switch_tab": "POST /openbrowser/v1/browser/switch-tab",
            "feedback_list_issues": "GET /openbrowser/v1/feedback/issues",
            "feedback_report_issue": "POST /openbrowser/v1/feedback/issues",
            "feedback_update_issue": "POST /openbrowser/v1/feedback/issues/{issue_id}",
            "telemetry_record_event": "POST /openbrowser/v1/telemetry/events",
            "telemetry_list_events": "GET /openbrowser/v1/telemetry/events",
            "telemetry_summary": "GET /openbrowser/v1/telemetry/summary",
        },
        "identities": {
            "generic": "omit identity_id for a neutral non-account browser",
            "work-main": "Example persisted Chrome profile with account state",
            "qa-generic": "Example generic QA identity",
        },
    }


@app.get("/openbrowser/v1/identities")
async def openbrowser_identities(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return redacted_status()


@app.get("/openbrowser/v1/auth/status")
async def openbrowser_auth_status(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await auth_status()


@app.get("/openbrowser/v1/audit")
async def openbrowser_audit(hours: int = 24, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return run_audit(hours)


@app.get("/openbrowser/v1/profiles/status")
async def openbrowser_profiles_status(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return profile_status()


@app.post("/openbrowser/v1/auth/request")
async def openbrowser_auth_request(request: AuthRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await auth_request(request)


@app.post("/openbrowser/v1/auth/batch")
async def openbrowser_auth_batch(request: OpenBrowserAuthBatchRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    requests = []
    for identity_id in request.identity_ids:
        requests.append(
            await auth_request(
                AuthRequest(
                    owner=request.owner,
                    identity_id=identity_id,
                    url=request.url,
                    reason=request.reason,
                )
            )
        )
    return {"count": len(requests), "requests": requests}


@app.post("/openbrowser/v1/leases")
async def openbrowser_create_lease(request: LeaseRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await create_lease(request)


@app.post("/openbrowser/v1/leases/{lease_id}/release")
async def openbrowser_release_lease(lease_id: str, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await release_lease(lease_id)


@app.post("/openbrowser/v1/leases/{lease_id}/heartbeat")
async def openbrowser_heartbeat_lease(lease_id: str, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await heartbeat_lease(lease_id)


@app.post("/openbrowser/v1/open")
async def openbrowser_open(request: OpenBrowserOpenRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    lease_obj = await create_lease(LeaseRequest(owner=request.owner, ttl_seconds=request.ttl_seconds, identity_id=request.identity_id))
    try:
        navigation = await browser_navigate(
            NavigateRequest(lease_id=lease_obj["lease_id"], url=request.url, wait_until=request.wait_until)
        )
    except Exception as error:
        await release_lease(str(lease_obj["lease_id"]))
        if isinstance(error, HTTPException):
            raise
        raise _http_error(error) from error
    return {"lease": lease_obj, "navigation": navigation}


@app.post("/openbrowser/v1/browser/navigate")
async def openbrowser_navigate(request: NavigateRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_navigate(request)


@app.post("/openbrowser/v1/browser/snapshot")
async def openbrowser_snapshot(request: LeaseIdRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_snapshot(request)


@app.post("/openbrowser/v1/browser/screenshot")
async def openbrowser_screenshot(request: ScreenshotRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_screenshot(request)


@app.post("/openbrowser/v1/browser/click")
async def openbrowser_click(request: ClickRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_click(request)


@app.post("/openbrowser/v1/browser/type")
async def openbrowser_type(request: TypeRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_type(request)


@app.post("/openbrowser/v1/browser/keyboard-type")
async def openbrowser_keyboard_type(request: KeyboardTypeRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_keyboard_type(request)


@app.post("/openbrowser/v1/browser/keyboard-press")
async def openbrowser_keyboard_press(request: KeyboardPressRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_keyboard_press(request)


@app.post("/openbrowser/v1/lease-control/request")
async def openbrowser_lease_control_request(
    request: LeaseControlRequest, _auth: str = Depends(require_openbrowser_api_key)
) -> dict[str, Any]:
    return await lease_control_request(request)


@app.post("/openbrowser/v1/browser/wait")
async def openbrowser_wait(request: WaitRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_wait(request)


@app.post("/openbrowser/v1/browser/tabs")
async def openbrowser_tabs(request: LeaseIdRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_tabs(request)


@app.post("/openbrowser/v1/browser/new-tab")
async def openbrowser_new_tab(request: NewTabRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_new_tab(request)


@app.post("/openbrowser/v1/browser/switch-tab")
async def openbrowser_switch_tab(request: SwitchTabRequest, _auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    return await browser_switch_tab(request)


@app.get("/openbrowser/v1/feedback/issues")
async def openbrowser_feedback_issues(
    status: str = "open",
    limit: int = 50,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await feedback_issues(status, limit)


@app.post("/openbrowser/v1/feedback/issues")
async def openbrowser_feedback_create_issue(
    request: FeedbackIssueRequest,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await feedback_create_issue(request)


@app.post("/openbrowser/v1/feedback/issues/{issue_id}")
async def openbrowser_feedback_update(
    issue_id: str,
    request: FeedbackUpdateRequest,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await feedback_update(issue_id, request)


@app.post("/openbrowser/v1/telemetry/events")
async def openbrowser_telemetry_create_event(
    request: TelemetryEventRequest,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await telemetry_create_event(request)


@app.get("/openbrowser/v1/telemetry/events")
async def openbrowser_telemetry_events(
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    lease_id: str | None = None,
    issue_id: str | None = None,
    limit: int = 100,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await telemetry_events(source, event_type, severity, lease_id, issue_id, limit)


@app.get("/openbrowser/v1/telemetry/summary")
async def openbrowser_telemetry_summary(
    window_seconds: int = 86400,
    _auth: str = Depends(require_openbrowser_api_key),
) -> dict[str, Any]:
    return await telemetry_summary(window_seconds)


@app.post("/lease")
async def create_lease(request: LeaseRequest) -> dict[str, Any]:
    try:
        lease_obj = lease(request.owner, request.ttl_seconds, request.identity_id)
        result = lease_obj.__dict__
        _safe_record_event(
            source=lease_obj.owner,
            event_type="lease",
            message="Lease created",
            lease_id=lease_obj.lease_id,
            tags=["lease", lease_obj.name],
            data={"slot": lease_obj.name, "identity_id": lease_obj.identity_id, "ttl_seconds": request.ttl_seconds},
        )
        return result
    except Exception as error:
        _safe_record_event(
            source=request.owner,
            event_type="error",
            message="Lease failed",
            severity="error",
            tags=["lease", "failure"],
            data={"identity_id": request.identity_id, "error": str(error), "ttl_seconds": request.ttl_seconds},
        )
        raise _http_error(error) from error


@app.post("/release/{lease_id}")
async def release_lease(lease_id: str) -> dict[str, Any]:
    result = release(lease_id)
    _safe_record_event(
        source="broker-api",
        event_type="lease",
        message="Lease released",
        lease_id=lease_id,
        tags=["lease", "release"],
        data=result,
    )
    return result


@app.post("/heartbeat/{lease_id}")
async def heartbeat_lease(lease_id: str) -> dict[str, Any]:
    try:
        lease_obj = heartbeat(lease_id)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="lease",
            message="Lease heartbeat",
            lease_id=lease_obj.lease_id,
            tags=["lease", "heartbeat"],
            data={"slot": lease_obj.name, "identity_id": lease_obj.identity_id},
        )
        return lease_obj.__dict__
    except Exception as error:
        _safe_record_event(
            source="broker-api",
            event_type="error",
            message="Lease heartbeat failed",
            severity="error",
            lease_id=lease_id,
            tags=["lease", "heartbeat", "failure"],
            data={"error": str(error)},
        )
        raise _http_error(error) from error


@app.post("/browser/navigate")
async def browser_navigate(request: NavigateRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.navigate(lease_obj, request.url, request.wait_until)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser navigate",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["browser", "navigate"],
            data={"slot": lease_obj.name, "status": result.get("status"), "title": result.get("title")},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "navigate", error, {"url": request.url, "wait_until": request.wait_until})
        raise _http_error(error) from error


@app.post("/browser/snapshot")
async def browser_snapshot(request: LeaseIdRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.snapshot(lease_obj)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser snapshot",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["browser", "snapshot"],
            data={"slot": lease_obj.name, "title": result.get("title"), "element_count": len(result.get("elements", []))},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "snapshot", error)
        raise _http_error(error) from error


@app.post("/browser/screenshot")
async def browser_screenshot(request: ScreenshotRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.screenshot(lease_obj, request.full_page)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser screenshot",
            lease_id=lease_obj.lease_id,
            tags=["browser", "screenshot"],
            data={"slot": lease_obj.name, "path": result.get("path"), "full_page": request.full_page},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "screenshot", error, {"full_page": request.full_page})
        raise _http_error(error) from error


@app.post("/browser/click")
async def browser_click(request: ClickRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.click(lease_obj, request.selector)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser click",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["browser", "click"],
            data={"slot": lease_obj.name, "selector": request.selector},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "click", error, {"selector": request.selector})
        raise _http_error(error) from error


@app.post("/browser/type")
async def browser_type(request: TypeRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.type_text(lease_obj, request.selector, request.text, request.submit)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser type",
            lease_id=lease_obj.lease_id,
            tags=["browser", "type"],
            data={
                "slot": lease_obj.name,
                "selector": request.selector,
                "submitted": request.submit,
                "text_length": len(request.text),
                "keyboard": result.get("keyboard"),
            },
        )
        return result
    except Exception as error:
        _record_browser_failure(
            request,
            "type",
            error,
            {"selector": request.selector, "submitted": request.submit, "text_length": len(request.text)},
        )
        raise _http_error(error) from error


@app.post("/browser/keyboard-type")
async def browser_keyboard_type(request: KeyboardTypeRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.keyboard_type(lease_obj, request.text, request.selector, request.delay_ms)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser keyboard type",
            lease_id=lease_obj.lease_id,
            tags=["browser", "keyboard", "type"],
            data={
                "slot": lease_obj.name,
                "selector": request.selector,
                "text_length": len(request.text),
                "delay_ms": request.delay_ms,
            },
        )
        return result
    except Exception as error:
        _record_browser_failure(
            request,
            "keyboard-type",
            error,
            {"selector": request.selector, "text_length": len(request.text), "delay_ms": request.delay_ms},
        )
        raise _http_error(error) from error


@app.post("/browser/keyboard-press")
async def browser_keyboard_press(request: KeyboardPressRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.keyboard_press(lease_obj, request.key, request.selector)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser keyboard press",
            lease_id=lease_obj.lease_id,
            tags=["browser", "keyboard", "press"],
            data={"slot": lease_obj.name, "selector": request.selector, "key": request.key},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "keyboard-press", error, {"selector": request.selector, "key": request.key})
        raise _http_error(error) from error


@app.post("/lease-control/request")
async def lease_control_request(request: LeaseControlRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = create_control_session(request.owner, lease_obj.lease_id, request.ttl_seconds)
        _safe_record_event(
            source=request.owner,
            event_type="session",
            message="Lease control session created",
            lease_id=lease_obj.lease_id,
            tags=["lease-control", "human-handoff"],
            data={"slot": lease_obj.name, "identity_id": lease_obj.identity_id, "ttl_seconds": request.ttl_seconds},
        )
        return result
    except Exception as error:
        _safe_record_event(
            source=request.owner,
            event_type="error",
            message="Lease control session failed",
            severity="error",
            lease_id=request.lease_id,
            tags=["lease-control", "failure"],
            data={"error": str(error)},
        )
        raise _http_error(error) from error


def _control_html(token: str, session: dict[str, Any]) -> str:
    safe_owner = html.escape(str(session.get("owner", "unknown")))
    safe_lease_id = html.escape(str(session.get("lease_id", "")))
    safe_token = html.escape(token, quote=True)
    safe_expires_at = html.escape(str(session.get("expires_at", "")))
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OpenBrowser Broker Control</title>
    <style>
      :root {{ color-scheme: light; }}
      body {{ font-family: system-ui, sans-serif; margin: 0; background: #f8fafc; color: #111827; }}
      header {{ padding: 16px 20px; background: #111827; color: white; display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
      main {{ padding: 16px; max-width: 1280px; margin: 0 auto; }}
      button {{ background: #111827; color: white; border: 0; padding: 9px 12px; border-radius: 6px; cursor: pointer; }}
      input {{ padding: 9px 10px; border: 1px solid #cbd5e1; border-radius: 6px; min-width: min(520px, 70vw); }}
      .muted {{ color: #64748b; }}
      .panel {{ background: white; border: 1px solid #dbe3ef; border-radius: 8px; padding: 12px; margin: 14px 0; }}
      .toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
      #screen {{ display: block; max-width: 100%; height: auto; border: 1px solid #cbd5e1; background: white; cursor: crosshair; }}
      #status {{ font-size: 14px; color: #334155; min-height: 20px; }}
    </style>
  </head>
  <body>
    <header>
      <div><b>Manual browser control</b><div>Owner: {safe_owner}</div></div>
      <div>Lease: <code>{safe_lease_id}</code></div>
    </header>
    <main>
      <div class="panel">
        <div class="toolbar">
          <button id="refresh" type="button">Refresh screenshot</button>
          <form id="typeForm" class="toolbar">
            <input id="text" autocomplete="off" placeholder="Text to type into focused field">
            <button type="submit">Type</button>
          </form>
          <form id="pressForm" class="toolbar">
            <input id="key" autocomplete="off" value="Enter" aria-label="Key">
            <button type="submit">Press key</button>
          </form>
          <button id="done" type="button">End control link</button>
        </div>
        <p class="muted">Click the screenshot to send a coordinate click to the held browser tab. This is a human handoff view for login or challenge prompts; it does not expose session cookies or passwords.</p>
        <div id="status">Expires at Unix time {safe_expires_at}</div>
      </div>
      <img id="screen" alt="Current browser screenshot" src="/auth/lease-control/{safe_token}/screenshot?ts=0">
    </main>
    <script>
      const token = {json.dumps(token)};
      const screen = document.getElementById('screen');
      const statusBox = document.getElementById('status');
      const setStatus = (text) => {{ statusBox.textContent = text; }};
      const refresh = () => {{ screen.src = `/auth/lease-control/${{token}}/screenshot?ts=${{Date.now()}}`; }};
      document.getElementById('refresh').addEventListener('click', refresh);
      screen.addEventListener('click', async (event) => {{
        const rect = screen.getBoundingClientRect();
        const x = Math.round((event.clientX - rect.left) * screen.naturalWidth / rect.width);
        const y = Math.round((event.clientY - rect.top) * screen.naturalHeight / rect.height);
        setStatus(`Clicking ${{x}}, ${{y}}...`);
        const response = await fetch(`/auth/lease-control/${{token}}/click`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify({{x, y}})
        }});
        setStatus(response.ok ? `Clicked ${{x}}, ${{y}}` : `Click failed: ${{await response.text()}}`);
        setTimeout(refresh, 700);
      }});
      document.getElementById('typeForm').addEventListener('submit', async (event) => {{
        event.preventDefault();
        const text = document.getElementById('text').value;
        setStatus('Typing...');
        const response = await fetch(`/auth/lease-control/${{token}}/keyboard-type`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify({{text}})
        }});
        setStatus(response.ok ? 'Typed text into focused field' : `Type failed: ${{await response.text()}}`);
        document.getElementById('text').value = '';
        setTimeout(refresh, 700);
      }});
      document.getElementById('pressForm').addEventListener('submit', async (event) => {{
        event.preventDefault();
        const key = document.getElementById('key').value || 'Enter';
        setStatus(`Pressing ${{key}}...`);
        const response = await fetch(`/auth/lease-control/${{token}}/keyboard-press`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify({{key}})
        }});
        setStatus(response.ok ? `Pressed ${{key}}` : `Key failed: ${{await response.text()}}`);
        setTimeout(refresh, 700);
      }});
      document.getElementById('done').addEventListener('click', async () => {{
        const response = await fetch(`/auth/lease-control/${{token}}/complete`, {{method: 'POST'}});
        setStatus(response.ok ? 'Control link ended' : `End failed: ${{await response.text()}}`);
      }});
    </script>
  </body>
</html>
"""


@app.get("/auth/lease-control/{token}", response_class=HTMLResponse)
async def lease_control_portal(token: str) -> str:
    try:
        session = get_control_session(token)
        require_lease(str(session["lease_id"]))
        return _control_html(token, session)
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise _http_error(error) from error


@app.get("/auth/lease-control/{token}/screenshot")
async def lease_control_screenshot(token: str) -> Response:
    try:
        session = get_control_session(token)
        lease_obj = require_lease(str(session["lease_id"]))
        result = await controller.screenshot(lease_obj, False)
        return Response(content=base64.b64decode(str(result["base64"])), media_type="image/png")
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise _http_error(error) from error


@app.post("/auth/lease-control/{token}/click")
async def lease_control_click(token: str, request: MouseClickRequest) -> dict[str, Any]:
    try:
        session = get_control_session(token)
        lease_obj = require_lease(str(session["lease_id"]))
        result = await controller.mouse_click(lease_obj, request.x, request.y)
        _safe_record_event(
            source=str(session.get("owner", "lease-control")),
            event_type="browser_action",
            message="Lease control click",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["lease-control", "click"],
            data={"slot": lease_obj.name, "x": request.x, "y": request.y},
        )
        return result
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise _http_error(error) from error


@app.post("/auth/lease-control/{token}/keyboard-type")
async def lease_control_keyboard_type(token: str, request: LeaseControlTypeRequest) -> dict[str, Any]:
    try:
        session = get_control_session(token)
        lease_obj = require_lease(str(session["lease_id"]))
        result = await controller.keyboard_type(lease_obj, request.text, None, request.delay_ms)
        _safe_record_event(
            source=str(session.get("owner", "lease-control")),
            event_type="browser_action",
            message="Lease control keyboard type",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["lease-control", "keyboard", "type"],
            data={"slot": lease_obj.name, "text_length": len(request.text), "delay_ms": request.delay_ms},
        )
        return result
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise _http_error(error) from error


@app.post("/auth/lease-control/{token}/keyboard-press")
async def lease_control_keyboard_press(token: str, request: LeaseControlPressRequest) -> dict[str, Any]:
    try:
        session = get_control_session(token)
        lease_obj = require_lease(str(session["lease_id"]))
        result = await controller.keyboard_press(lease_obj, request.key, None)
        _safe_record_event(
            source=str(session.get("owner", "lease-control")),
            event_type="browser_action",
            message="Lease control keyboard press",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["lease-control", "keyboard", "press"],
            data={"slot": lease_obj.name, "key": request.key},
        )
        return result
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise _http_error(error) from error


@app.post("/auth/lease-control/{token}/complete")
async def lease_control_complete(token: str) -> dict[str, Any]:
    try:
        session = complete_control_session(token)
        _safe_record_event(
            source=str(session.get("owner", "lease-control")),
            event_type="session",
            message="Lease control session completed",
            lease_id=str(session.get("lease_id")),
            tags=["lease-control", "complete"],
            data={"ttl_seconds": session.get("ttl_seconds")},
        )
        return session
    except LeaseControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/browser/wait")
async def browser_wait(request: WaitRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.wait(lease_obj, request.selector, request.timeout_ms)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser wait",
            lease_id=lease_obj.lease_id,
            tags=["browser", "wait"],
            data={"slot": lease_obj.name, "selector": request.selector, "timeout_ms": request.timeout_ms},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "wait", error, {"selector": request.selector, "timeout_ms": request.timeout_ms})
        raise _http_error(error) from error


@app.post("/browser/tabs")
async def browser_tabs(request: LeaseIdRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.tabs(lease_obj)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser tabs listed",
            lease_id=lease_obj.lease_id,
            tags=["browser", "tabs"],
            data={"slot": lease_obj.name, "tab_count": len(result.get("tabs", []))},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "tabs", error)
        raise _http_error(error) from error


@app.post("/browser/new-tab")
async def browser_new_tab(request: NewTabRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.new_tab(lease_obj, request.url)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser new tab",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["browser", "new-tab"],
            data={"slot": lease_obj.name, "title": result.get("title")},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "new-tab", error, {"url": request.url})
        raise _http_error(error) from error


@app.post("/browser/switch-tab")
async def browser_switch_tab(request: SwitchTabRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.switch_tab(lease_obj, request.index)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser switch tab",
            lease_id=lease_obj.lease_id,
            url=result.get("url"),
            tags=["browser", "switch-tab"],
            data={"slot": lease_obj.name, "index": request.index, "title": result.get("title")},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "switch-tab", error, {"index": request.index})
        raise _http_error(error) from error


@app.post("/browser/upload")
async def browser_upload(request: UploadRequest) -> dict[str, Any]:
    try:
        lease_obj = require_lease(request.lease_id)
        result = await controller.upload(lease_obj, request.selector, request.path)
        _safe_record_event(
            source=lease_obj.owner,
            event_type="browser_action",
            message="Browser upload",
            lease_id=lease_obj.lease_id,
            tags=["browser", "upload"],
            data={"slot": lease_obj.name, "selector": request.selector, "path": request.path},
        )
        return result
    except Exception as error:
        _record_browser_failure(request, "upload", error, {"selector": request.selector, "path": request.path})
        raise _http_error(error) from error


@app.get("/auth/status")
async def auth_status() -> dict[str, Any]:
    return list_auth_requests()


@app.post("/auth/request")
async def auth_request(request: AuthRequest) -> dict[str, Any]:
    result = create_auth_request(request.owner, request.url, request.reason, request.identity_id)
    _safe_record_event(
        source=request.owner,
        event_type="auth",
        message="Auth request created",
        url=request.url,
        tags=["auth", request.reason],
        data={"token": result.get("token"), "status": result.get("status"), "identity_id": request.identity_id},
    )
    return result


@app.get("/auth/{token}", response_class=HTMLResponse)
async def auth_portal(token: str) -> str:
    try:
        request = get_auth_request(token)
    except AuthError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    safe_url = html.escape(str(request["url"]))
    safe_owner = html.escape(str(request["owner"]))
    safe_status = html.escape(str(request["status"]))
    safe_identity = html.escape(str(request.get("identity_id") or "authenticated-chrome"))
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>OpenBrowser Broker Auth</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 40px; max-width: 760px; line-height: 1.45; }}
      code, pre {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
      button, a.button {{ background: #111827; color: white; border: 0; padding: 10px 14px; border-radius: 6px; text-decoration: none; cursor: pointer; }}
      .row {{ margin: 18px 0; }}
      .muted {{ color: #4b5563; }}
    </style>
  </head>
  <body>
    <h1>Auth refresh</h1>
    <p class="muted">Requesting agent: <b>{safe_owner}</b></p>
    <p class="muted">Status: <b>{safe_status}</b></p>
    <p class="muted">Chrome identity: <b>{safe_identity}</b></p>
    <div class="row">Target URL: <code>{safe_url}</code></div>
    <div class="row">
      <form method="post" action="/auth/{token}/start-vnc"><button type="submit">Start browser login view</button></form>
    </div>
    <div class="row">
      <form method="post" action="/auth/{token}/complete"><button type="submit">Mark login complete</button></form>
    </div>
  </body>
</html>
"""


@app.post("/auth/{token}/start-vnc", response_class=HTMLResponse)
async def auth_start_vnc(token: str) -> str:
    try:
        vnc = start_auth_vnc(token)
    except AuthError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    safe_websocket_url = html.escape(str(vnc["websocket_url"]), quote=True)
    safe_password = html.escape(str(vnc["password"]))
    return f"""
<!doctype html>
<html>
  <head><meta charset="utf-8"><title>OpenBrowser Broker noVNC</title></head>
  <body style="font-family: system-ui, sans-serif; margin: 40px">
    <h1>Login view ready</h1>
    <p>Open <a href="{safe_websocket_url}">{safe_websocket_url}</a>.</p>
    <p>VNC password: <code>{safe_password}</code></p>
    <p>After login, return to the auth page and mark the request complete.</p>
  </body>
</html>
"""


@app.post("/auth/{token}/complete")
async def auth_complete(token: str) -> dict[str, Any]:
    try:
        request = complete_auth_request(token)
        request["vnc_stop"] = stop_auth_vnc(token, missing_ok=True)
        _safe_record_event(
            source=str(request.get("owner", "unknown")),
            event_type="auth",
            message="Auth request completed",
            url=str(request.get("url") or ""),
            tags=["auth", "complete"],
            data={"token": token, "status": request.get("status")},
        )
        return request
    except AuthError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/auth/{token}/stop-vnc")
async def auth_stop_vnc(token: str) -> dict[str, Any]:
    try:
        return stop_auth_vnc(token)
    except AuthError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/profiles/status")
async def profiles_status() -> dict[str, Any]:
    return profile_status()


@app.post("/profiles/snapshot-golden")
async def profiles_snapshot_golden() -> dict[str, Any]:
    try:
        return snapshot_golden()
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/profiles/seed-slot")
async def profiles_seed_slot(request: SeedSlotRequest) -> dict[str, Any]:
    try:
        return seed_slot(request.slot, request.force)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/feedback/issues")
async def feedback_issues(status: str = "open", limit: int = 50) -> dict[str, Any]:
    try:
        return list_issues(status, limit)
    except FeedbackError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/feedback/issues")
async def feedback_create_issue(request: FeedbackIssueRequest) -> dict[str, Any]:
    try:
        issue = report_issue(
            source=request.source,
            title=request.title,
            details=request.details,
            severity=request.severity,
            lease_id=request.lease_id,
            url=request.url,
            tags=request.tags,
        )
        _safe_record_event(
            source=request.source,
            event_type="issue",
            message="Issue reported",
            severity="error" if issue["severity"] in {"high", "blocker"} else "warning",
            lease_id=request.lease_id,
            issue_id=issue["id"],
            url=request.url,
            tags=["issue", *request.tags],
            data={"title": issue["title"], "issue_severity": issue["severity"]},
        )
        return issue
    except FeedbackError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/feedback/issues/{issue_id}")
async def feedback_update(issue_id: str, request: FeedbackUpdateRequest) -> dict[str, Any]:
    try:
        issue = update_issue(issue_id, request.status, request.note)
        _safe_record_event(
            source="broker-api",
            event_type="issue",
            message="Issue updated",
            severity="info",
            lease_id=issue.get("lease_id"),
            issue_id=issue_id,
            url=issue.get("url"),
            tags=["issue", "update"],
            data={"status": issue.get("status"), "note_added": bool(request.note)},
        )
        return issue
    except FeedbackError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/telemetry/events")
async def telemetry_create_event(request: TelemetryEventRequest) -> dict[str, Any]:
    try:
        return record_event(
            source=request.source,
            event_type=request.event_type,
            message=request.message,
            severity=request.severity,
            lease_id=request.lease_id,
            issue_id=request.issue_id,
            url=request.url,
            tags=request.tags,
            data=request.data,
        )
    except TelemetryError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/telemetry/events")
async def telemetry_events(
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    lease_id: str | None = None,
    issue_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return list_events(source, event_type, severity, lease_id, issue_id, limit)


@app.get("/telemetry/summary")
async def telemetry_summary(window_seconds: int = 86400) -> dict[str, Any]:
    return summary(window_seconds)


def main() -> None:
    uvicorn.run(app, host=BROKER_HOST, port=BROKER_PORT)


if __name__ == "__main__":
    main()
