from __future__ import annotations

import html
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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
from .config import BROKER_HOST, BROKER_PORT, ensure_dirs
from .docs import docs
from .feedback import FeedbackError, list_issues, report_issue, update_issue
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    await controller.start()
    try:
        yield
    finally:
        await controller.stop()


app = FastAPI(title="AX41 Browser Broker", version="0.1.0", lifespan=lifespan)


def _http_error(error: Exception) -> HTTPException:
    status_code = 409 if isinstance(error, LeaseError) else 400
    return HTTPException(status_code=status_code, detail=str(error))


def _safe_record_event(**kwargs: Any) -> None:
    try:
        record_event(**kwargs)
    except Exception:
        return


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
            data={"slot": lease_obj.name, "selector": request.selector, "submitted": request.submit, "text_length": len(request.text)},
        )
        return result
    except Exception as error:
        raise _http_error(error) from error


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
        raise _http_error(error) from error


@app.get("/auth/status")
async def auth_status() -> dict[str, Any]:
    return list_auth_requests()


@app.post("/auth/request")
async def auth_request(request: AuthRequest) -> dict[str, Any]:
    result = create_auth_request(request.owner, request.url, request.reason)
    _safe_record_event(
        source=request.owner,
        event_type="auth",
        message="Auth request created",
        url=request.url,
        tags=["auth", request.reason],
        data={"token": result.get("token"), "status": result.get("status")},
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
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>AX41 Browser Auth</title>
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
    return f"""
<!doctype html>
<html>
  <head><meta charset="utf-8"><title>AX41 noVNC</title></head>
  <body style="font-family: system-ui, sans-serif; margin: 40px">
    <h1>Login view ready</h1>
    <p>Open <a href="{vnc["websocket_url"]}">{vnc["websocket_url"]}</a>.</p>
    <p>VNC password: <code>{vnc["password"]}</code></p>
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
