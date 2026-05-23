from __future__ import annotations

import html
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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
from .pool import LeaseError, heartbeat, lease, release, require_lease, status
from .profiles import profile_status, seed_slot, snapshot_golden


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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "broker": f"http://{BROKER_HOST}:{BROKER_PORT}", "pool": status()}


@app.get("/status")
async def get_status() -> dict[str, Any]:
    return status()


@app.post("/lease")
async def create_lease(request: LeaseRequest) -> dict[str, Any]:
    try:
        return lease(request.owner, request.ttl_seconds, request.identity_id).__dict__
    except Exception as error:
        raise _http_error(error) from error


@app.post("/release/{lease_id}")
async def release_lease(lease_id: str) -> dict[str, Any]:
    return release(lease_id)


@app.post("/heartbeat/{lease_id}")
async def heartbeat_lease(lease_id: str) -> dict[str, Any]:
    try:
        return heartbeat(lease_id).__dict__
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/navigate")
async def browser_navigate(request: NavigateRequest) -> dict[str, Any]:
    try:
        return await controller.navigate(require_lease(request.lease_id), request.url, request.wait_until)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/snapshot")
async def browser_snapshot(request: LeaseIdRequest) -> dict[str, Any]:
    try:
        return await controller.snapshot(require_lease(request.lease_id))
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/screenshot")
async def browser_screenshot(request: ScreenshotRequest) -> dict[str, Any]:
    try:
        return await controller.screenshot(require_lease(request.lease_id), request.full_page)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/click")
async def browser_click(request: ClickRequest) -> dict[str, Any]:
    try:
        return await controller.click(require_lease(request.lease_id), request.selector)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/type")
async def browser_type(request: TypeRequest) -> dict[str, Any]:
    try:
        return await controller.type_text(require_lease(request.lease_id), request.selector, request.text, request.submit)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/wait")
async def browser_wait(request: WaitRequest) -> dict[str, Any]:
    try:
        return await controller.wait(require_lease(request.lease_id), request.selector, request.timeout_ms)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/tabs")
async def browser_tabs(request: LeaseIdRequest) -> dict[str, Any]:
    try:
        return await controller.tabs(require_lease(request.lease_id))
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/new-tab")
async def browser_new_tab(request: NewTabRequest) -> dict[str, Any]:
    try:
        return await controller.new_tab(require_lease(request.lease_id), request.url)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/switch-tab")
async def browser_switch_tab(request: SwitchTabRequest) -> dict[str, Any]:
    try:
        return await controller.switch_tab(require_lease(request.lease_id), request.index)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/browser/upload")
async def browser_upload(request: UploadRequest) -> dict[str, Any]:
    try:
        return await controller.upload(require_lease(request.lease_id), request.selector, request.path)
    except Exception as error:
        raise _http_error(error) from error


@app.get("/auth/status")
async def auth_status() -> dict[str, Any]:
    return list_auth_requests()


@app.post("/auth/request")
async def auth_request(request: AuthRequest) -> dict[str, Any]:
    return create_auth_request(request.owner, request.url, request.reason)


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
