from __future__ import annotations

import hmac
import html
import ipaddress
import json
import os
import base64
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, model_validator

from .audit import run_audit
from .auth import (
    AuthError,
    complete_auth_request,
    create_auth_request,
    current_auth_vnc,
    get_auth_request,
    get_pending_auth_request,
    list_auth_requests,
    start_auth_vnc,
    stop_auth_vnc,
)
from .browser import controller
from .config import (
    AUTH_PORTAL_AUTOSTART,
    AUTH_TRUST_X_FORWARDED_FOR,
    AUTH_TRUSTED_CIDRS,
    BROKER_HOST,
    BROKER_PORT,
    OPENBROWSER_API_KEYS_FILE,
    PUBLIC_OPENBROWSER_BASE_URL,
    ensure_dirs,
)
from .docs import docs
from .feedback import FeedbackError, list_issues, report_issue, update_issue
from .identities import IdentityError, redacted_status
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
    profile: str | None = None

    @model_validator(mode="after")
    def normalize_legacy_profile(self) -> "AuthRequest":
        if not self.profile:
            return self
        if self.identity_id and self.identity_id != self.profile:
            raise ValueError("profile and identity_id must match when both are provided")
        self.identity_id = self.profile
        return self


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


def _active_identity_lease_id(identity_id: str | None) -> str | None:
    if not identity_id:
        return None
    for lease_id, lease_data in (status().get("leases") or {}).items():
        if lease_data.get("identity_id") == identity_id:
            return str(lease_id)
    return None


def _active_identity_control_redirect(auth_request_data: dict[str, Any], error: AuthError) -> RedirectResponse | None:
    identity_id = str(auth_request_data.get("identity_id") or "")
    message = str(error)
    if not identity_id or "actively leased" not in message:
        return None
    lease_id = _active_identity_lease_id(identity_id)
    if not lease_id:
        return None
    control = create_control_session(str(auth_request_data.get("owner") or "auth-handoff"), lease_id)
    _safe_record_event(
        source=str(auth_request_data.get("owner") or "auth-handoff"),
        event_type="session",
        message="Auth handoff redirected to active lease control",
        lease_id=lease_id,
        tags=["auth", "lease-control", "active-identity"],
        data={"identity_id": identity_id, "token": control.get("token")},
    )
    return RedirectResponse(str(control["portal_url"]), status_code=303)


def _active_identity_control_response(request: AuthRequest) -> dict[str, Any] | None:
    if not request.identity_id:
        return None
    lease_id = _active_identity_lease_id(request.identity_id)
    if not lease_id:
        return None
    control = create_control_session(request.owner, lease_id)
    _safe_record_event(
        source=request.owner,
        event_type="session",
        message="Auth request returned active lease control",
        lease_id=lease_id,
        url=request.url,
        tags=["auth", "lease-control", "active-identity"],
        data={"identity_id": request.identity_id, "token": control.get("token")},
    )
    return {
        "token": control["token"],
        "owner": request.owner,
        "url": request.url,
        "reason": request.reason,
        "status": "active_identity_leased",
        "identity_id": request.identity_id,
        "active_lease_id": lease_id,
        "portal_url": control["portal_url"],
        "local_portal_url": control["local_portal_url"],
        "lease_control": control,
        "warning": "Identity is already leased. Inspect tabs/snapshot/screenshot before sharing this control URL.",
    }


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


def _openbrowser_base_url() -> str:
    base = PUBLIC_OPENBROWSER_BASE_URL.rstrip("/")
    if not base:
        return "/openbrowser/v1"
    if base.endswith("/openbrowser/v1"):
        return base
    return base + "/openbrowser/v1"


def _script_json(value: Any) -> str:
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _openbrowser_endpoint_catalog() -> dict[str, str]:
    return {
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
        "upload": "POST /openbrowser/v1/browser/upload",
        "feedback_list_issues": "GET /openbrowser/v1/feedback/issues",
        "feedback_report_issue": "POST /openbrowser/v1/feedback/issues",
        "feedback_update_issue": "POST /openbrowser/v1/feedback/issues/{issue_id}",
        "telemetry_record_event": "POST /openbrowser/v1/telemetry/events",
        "telemetry_list_events": "GET /openbrowser/v1/telemetry/events",
        "telemetry_summary": "GET /openbrowser/v1/telemetry/summary",
    }


def _openbrowser_endpoint_description(key: str) -> str:
    descriptions = {
        "health": "Check API reachability and redacted pool status.",
        "identities": "List configured identities, proxy refs, and parallel-session limits.",
        "auth_request": "Create a human login handoff link.",
        "auth_batch": "Create login handoff links for multiple identities.",
        "auth_status": "List tracked auth handoff requests.",
        "audit": "Audit leases, issues, telemetry, and session-log usage.",
        "profile_status": "Inspect mirrored/golden profile health.",
        "lease": "Create an isolated browser lease.",
        "release": "Release a browser lease.",
        "heartbeat": "Extend a held lease.",
        "open": "Create a lease and open a URL in one call.",
        "navigate": "Navigate a leased browser.",
        "snapshot": "Read page text and interactive elements.",
        "screenshot": "Capture a leased browser screenshot.",
        "click": "Click by CSS selector.",
        "type": "Fill standard inputs.",
        "keyboard_type": "Type through real keyboard events.",
        "keyboard_press": "Press Enter, Tab, Escape, and other real keys.",
        "lease_control_request": "Create a temporary human-control link for an active lease.",
        "wait": "Wait for a selector or timeout in a leased browser.",
        "tabs": "List browser tabs for a lease.",
        "new_tab": "Open a new browser tab for a lease.",
        "switch_tab": "Switch the active browser tab.",
        "upload": "Upload a local file through a file input selector.",
        "feedback_list_issues": "List browser-agent issues.",
        "feedback_report_issue": "Report a browser-agent issue.",
        "feedback_update_issue": "Update or resolve a browser-agent issue.",
        "telemetry_record_event": "Record sanitized browser-agent telemetry.",
        "telemetry_list_events": "List sanitized browser-agent telemetry.",
        "telemetry_summary": "Summarize recent browser-agent telemetry.",
    }
    return descriptions.get(key, "OpenBrowser API endpoint.")


def _openbrowser_dashboard_html() -> str:
    base_url = _openbrowser_base_url()
    safe_base_url = html.escape(base_url)
    api_key_count = len(_configured_openbrowser_keys())
    api_key_state = "Configured" if api_key_count else "Missing"
    safe_api_key_state = html.escape(api_key_state)
    try:
        pool_state = status()
    except Exception:
        pool_state = {"slots": [], "leases": []}
    try:
        identity_state = redacted_status()
    except Exception:
        identity_state = {"identities": {}, "proxy_refs": []}
    public_slot_count = len(pool_state.get("slots") or [])
    public_lease_count = len(pool_state.get("leases") or [])
    public_identity_count = len(identity_state.get("identities") or {})
    public_proxy_count = len(identity_state.get("proxy_refs") or [])
    safe_public_slot_count = html.escape(str(public_slot_count))
    safe_public_lease_count = html.escape(str(public_lease_count))
    safe_public_identity_count = html.escape(str(public_identity_count))
    safe_public_proxy_count = html.escape(str(public_proxy_count))
    safe_api_dot = "ok" if api_key_count else "warn"
    remote_mcp_snippet = "\n".join(
        [
            "uv tool install git+https://github.com/floomhq/openbrowser.git",
            "",
            f'export OPENBROWSER_BASE_URL="{base_url}"',
            'export OPENBROWSER_API_KEY="<token>"',
            "openbrowser-remote-mcp",
        ]
    )
    mcp_config_snippet = json.dumps(
        {
            "mcpServers": {
                "openbrowser": {
                    "command": "openbrowser-remote-mcp",
                    "env": {
                        "OPENBROWSER_BASE_URL": base_url,
                        "OPENBROWSER_API_KEY": "<token>",
                    },
                }
            }
        },
        indent=2,
    )
    curl_snippet = "\n".join(
        [
            f'export OPENBROWSER_BASE_URL="{base_url}"',
            'export OPENBROWSER_API_KEY="<token>"',
            "",
            'curl -fsS "$OPENBROWSER_BASE_URL/health" \\',
            '  -H "Authorization: Bearer $OPENBROWSER_API_KEY" \\',
            '  -H "User-Agent: openbrowser-cli/1.0"',
        ]
    )
    safe_remote_mcp_snippet = html.escape(remote_mcp_snippet)
    safe_mcp_config_snippet = html.escape(mcp_config_snippet)
    safe_curl_snippet = html.escape(curl_snippet)
    return f"""
<!doctype html>
<html data-theme="light">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OpenBrowser</title>
    <script>
      (() => {{
        try {{
          const saved = localStorage.getItem('openbrowser-theme');
          const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
          document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
        }} catch (error) {{
          document.documentElement.dataset.theme = 'light';
        }}
      }})();
    </script>
    <style>
      :root {{
        color-scheme: light dark;
        --page: #e7dfd0;
        --paper: rgba(255,255,255,0.88);
        --panel: rgba(255,255,255,0.74);
        --panel-solid: #ffffff;
        --soft: #f5f2ec;
        --text: #1e1d1a;
        --muted: #807a70;
        --faint: #a8a196;
        --border: rgba(58,48,38,0.12);
        --border-strong: rgba(58,48,38,0.18);
        --primary: #24231f;
        --primary-text: #ffffff;
        --green: #47b274;
        --amber: #ee9c44;
        --red: #ec6a5f;
        --blue: #4f78d9;
        --radius-lg: 20px;
        --radius-md: 14px;
        --radius-sm: 10px;
        --radius-pill: 9999px;
        --shadow-window: 0 24px 80px rgba(33, 26, 17, 0.18), 0 1px 0 rgba(255,255,255,0.78) inset;
        --shadow-float: 0 24px 64px rgba(33, 26, 17, 0.20), 0 0 0 1px var(--border);
        --ease: cubic-bezier(0.22, 1, 0.36, 1);
      }}
      [data-theme="dark"] {{
        --page: #0f1211;
        --paper: rgba(25,25,24,0.92);
        --panel: rgba(33,33,31,0.76);
        --panel-solid: #21211f;
        --soft: #2b2a27;
        --text: #f4f1ea;
        --muted: #aaa49a;
        --faint: #746f68;
        --border: rgba(255,255,255,0.10);
        --border-strong: rgba(255,255,255,0.18);
        --primary: #f4f1ea;
        --primary-text: #191918;
        --green: #59c889;
        --amber: #f2b15d;
        --red: #f07b70;
        --blue: #7fa0ff;
        --shadow-window: 0 28px 90px rgba(0,0,0,0.48), 0 1px 0 rgba(255,255,255,0.08) inset;
        --shadow-float: 0 28px 70px rgba(0,0,0,0.42), 0 0 0 1px var(--border);
      }}
      * {{ box-sizing: border-box; }}
      html {{ min-height: 100%; background: var(--page); }}
      body {{
        margin: 0;
        min-height: 100dvh;
        overflow: auto;
        overflow-x: hidden;
        padding: 22px 28px;
        color: var(--text);
        background:
          linear-gradient(180deg, rgba(247,244,236,0.58) 0%, rgba(231,223,208,0.70) 44%, rgba(231,223,208,0.88) 100%),
          url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover no-repeat;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-feature-settings: "cv11" 1, "ss01" 1, "calt" 1;
      }}
      [data-theme="dark"] body {{
        background:
          linear-gradient(180deg, rgba(12,15,14,0.72) 0%, rgba(12,15,14,0.84) 56%, rgba(12,15,14,0.92) 100%),
          url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover no-repeat;
      }}
      .page-shell {{
        width: min(1500px, calc(100vw - 96px));
        max-width: 100%;
        margin: 0 auto;
        display: grid;
        gap: 28px;
      }}
      .product-hero {{
        display: grid;
        justify-items: center;
        text-align: center;
        gap: 10px;
        padding-top: 34px;
        text-shadow: 0 1px 28px rgba(255,255,255,0.62);
      }}
      [data-theme="dark"] .product-hero {{ text-shadow: 0 1px 28px rgba(0,0,0,0.62); }}
      .hero-logo {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 18px;
        font-size: clamp(38px, 3.7vw, 58px);
        line-height: 0.92;
        font-weight: 820;
        letter-spacing: 0;
      }}
      .hero-logo sup {{ margin-left: -10px; align-self: flex-start; font-size: 15px; font-weight: 850; }}
      .brand-mark {{
        position: relative;
        width: .96em;
        height: .96em;
        flex: 0 0 auto;
        border: .12em solid currentColor;
        border-radius: 24%;
        transform: rotate(30deg);
      }}
      .brand-mark::after {{
        content: "";
        position: absolute;
        inset: 23%;
        border: .11em solid currentColor;
        border-radius: 22%;
      }}
      .hero-line {{ color: color-mix(in srgb, var(--text) 72%, var(--muted)); font-size: clamp(20px, 1.6vw, 26px); font-weight: 560; }}
      .hero-subline {{ display: none; }}
      button, .button {{
        appearance: none;
        display: inline-flex;
        min-height: 44px;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid color-mix(in srgb, var(--primary) 82%, transparent);
        border-radius: var(--radius-sm);
        background: var(--primary);
        color: var(--primary-text);
        font-weight: 650;
        font-size: 13px;
        line-height: 1;
        padding: 0 14px;
        text-decoration: none;
        cursor: pointer;
        box-shadow: 0 1px 0 rgba(0,0,0,0.08);
        transition: transform 120ms var(--ease), background-color 150ms var(--ease), border-color 150ms var(--ease), box-shadow 150ms var(--ease);
        white-space: nowrap;
      }}
      button:hover, .button:hover {{ box-shadow: 0 10px 24px rgba(0,0,0,0.12); }}
      button:active, .button:active {{ transform: translateY(1px) scale(.985); }}
      button:focus-visible, .button:focus-visible, input:focus-visible {{
        outline: 3px solid color-mix(in srgb, var(--blue) 42%, transparent);
        outline-offset: 2px;
      }}
      .button-outline, .button-soft, button.secondary {{
        border-color: var(--border);
        background: color-mix(in srgb, var(--panel-solid) 86%, transparent);
        color: var(--text);
      }}
      .button-small {{ min-height: 44px; padding: 0 14px; font-size: 12px; }}
      .app-window {{
        position: relative;
        width: 100%;
        height: 836px;
        min-height: 0;
        overflow: hidden;
        max-width: 100%;
        display: grid;
        grid-template-rows: 74px auto;
        border: 1px solid var(--border);
        border-radius: 28px;
        background: var(--paper);
        box-shadow: var(--shadow-window);
        backdrop-filter: blur(26px) saturate(1.2);
      }}
      .window-bar {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 16px;
        padding: 18px 28px;
        border-bottom: 1px solid var(--border);
      }}
      .traffic-lights {{ display: none; }}
      .traffic-lights span {{ width: 13px; height: 13px; border-radius: var(--radius-pill); background: color-mix(in srgb, var(--faint) 45%, transparent); border: 1px solid var(--border); }}
      .traffic-lights span:nth-child(1) {{ background: color-mix(in srgb, var(--red) 62%, transparent); }}
      .traffic-lights span:nth-child(2) {{ background: color-mix(in srgb, var(--amber) 62%, transparent); }}
      .traffic-lights span:nth-child(3) {{ background: color-mix(in srgb, var(--green) 62%, transparent); }}
      .brand-block {{ min-width: 0; display: flex; align-items: center; gap: 12px; text-align: left; }}
      .brand-title {{ font-size: 17px; font-weight: 780; letter-spacing: 0; }}
      .brand-subtitle {{ display: none; }}
      .brand-mini-mark {{
        position: relative;
        width: 28px;
        height: 28px;
        border: 3px solid currentColor;
        border-radius: 8px;
        transform: rotate(30deg);
      }}
      .brand-mini-mark::after {{
        content: "";
        position: absolute;
        inset: 6px;
        border: 3px solid currentColor;
        border-radius: 5px;
      }}
      .top-actions {{ display: flex; justify-content: flex-end; gap: 10px; align-items: center; }}
      .api-link {{ min-width: 44px; min-height: 44px; display: inline-flex; align-items: center; justify-content: center; color: var(--muted); font-size: 13px; font-weight: 700; text-decoration: none; }}
      .app-grid {{
        min-height: 0;
        height: 100%;
        display: grid;
        grid-template-columns: 260px minmax(0, 1fr) 260px;
        align-items: stretch;
      }}
      .sidebar, .state-panel {{
        min-width: 0;
        min-height: 0;
        overflow: visible;
        padding: 28px 20px;
        background: color-mix(in srgb, var(--panel) 70%, transparent);
      }}
      .sidebar {{ border-right: 1px solid var(--border); }}
      .state-panel {{ border-left: 1px solid var(--border); }}
      .panel-title {{ margin-bottom: 18px; color: color-mix(in srgb, var(--muted) 86%, var(--text)); font-size: 12px; font-weight: 760; text-transform: uppercase; letter-spacing: .04em; }}
      .session-list, .stack {{ display: grid; gap: 14px; }}
      .session-card {{
        display: grid;
        grid-template-columns: 46px minmax(0, 1fr) auto;
        gap: 14px;
        align-items: center;
        padding: 18px 16px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: color-mix(in srgb, var(--panel-solid) 78%, transparent);
      }}
      .session-card.is-active {{ background: color-mix(in srgb, var(--panel-solid) 92%, transparent); box-shadow: 0 10px 28px rgba(0,0,0,0.045); }}
      .session-icon, .state-icon {{
        width: 40px;
        height: 40px;
        display: grid;
        place-items: center;
        border-radius: var(--radius-pill);
        background: var(--soft);
        border: 1px solid var(--border);
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
      }}
      .session-icon svg, .state-icon svg, .inline-icon {{
        width: 20px;
        height: 20px;
        stroke: currentColor;
        stroke-width: 2;
        fill: none;
        stroke-linecap: round;
        stroke-linejoin: round;
      }}
      .session-name {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 16px; font-weight: 760; }}
      .session-status {{ margin-top: 4px; color: var(--green); font-size: 13px; font-weight: 650; }}
      .session-status.muted {{ color: var(--muted); }}
      .app-logo {{
        width: 40px;
        height: 40px;
        display: grid;
        place-items: center;
        border-radius: 10px;
        color: white;
        font-size: 22px;
        font-weight: 850;
      }}
      .app-logo.google {{ background: white; color: #4285f4; border: 1px solid var(--border); font-size: 27px; }}
      .app-logo.hubspot {{ background: white; color: #ff5c35; border: 1px solid var(--border); }}
      .lease-summary {{
        width: auto;
        margin-top: 150px;
        display: grid;
        gap: 14px;
        padding: 18px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: color-mix(in srgb, var(--panel-solid) 66%, transparent);
      }}
      .lease-summary .button-small {{ display: none; }}
      .lease-summary .metric-line {{ display: grid; gap: 4px; }}
      .lease-summary b {{ font-size: 22px; }}
      .kebab {{ color: var(--faint); font-size: 24px; line-height: 1; }}
      .request-card, .surface {{
        margin-top: 18px;
        display: grid;
        gap: 13px;
        padding: 15px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 66%, transparent);
      }}
      .copy-source {{
        position: absolute;
        left: -10000px;
        top: auto;
        width: 1px;
        height: 1px;
        overflow: hidden;
      }}
      .request-row {{ min-width: 0; display: grid; gap: 4px; }}
      .label {{ color: var(--muted); font-size: 12px; font-weight: 760; }}
      .value {{ min-width: 0; overflow: hidden; overflow-wrap: anywhere; white-space: normal; font-size: 13px; font-weight: 600; }}
      .mono, code, pre {{ font-family: "SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace; }}
      code {{ overflow-wrap: anywhere; word-break: break-word; }}
      .browser-stage {{
        position: static;
        min-width: 0;
        min-height: 0;
        height: 100%;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
        gap: 16px;
        padding: 22px 16px 86px;
      }}
      .stage-title {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; font-weight: 760; text-transform: uppercase; letter-spacing: .04em; }}
      .stage-title-main {{ display: inline-flex; align-items: center; gap: 12px; min-width: 0; }}
      .status-chip {{ display: inline-flex; align-items: center; gap: 7px; color: var(--text); font-size: 13px; font-weight: 650; text-transform: none; letter-spacing: 0; }}
      .status-chip::before {{ content: ""; width: 8px; height: 8px; border-radius: var(--radius-pill); background: var(--green); box-shadow: 0 0 0 4px color-mix(in srgb, var(--green) 16%, transparent); }}
      .mobile-quick-state {{ display: none; }}
      .browser-shell {{
        min-width: 0;
        min-height: 0;
        height: 100%;
        overflow: visible;
        display: block;
        border: 0;
        border-radius: 0;
        background: transparent;
        box-shadow: none;
      }}
      .browser-toolbar {{
        min-width: 0;
        display: none;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 14px;
        padding: 12px 14px;
        border-bottom: 1px solid var(--border);
        background: color-mix(in srgb, var(--panel-solid) 88%, transparent);
      }}
      .toolbar-left {{ display: flex; gap: 8px; }}
      .toolbar-left span {{ width: 14px; height: 14px; border-radius: var(--radius-pill); background: var(--soft); border: 1px solid var(--border); }}
      .toolbar-url {{
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        height: 34px;
        display: flex;
        align-items: center;
        gap: 8px;
        border: 1px solid var(--border);
        border-radius: var(--radius-pill);
        padding: 0 14px;
        color: var(--muted);
        background: var(--soft);
        font-size: 13px;
        font-weight: 650;
      }}
      .lock {{ color: var(--green); font-size: 11px; text-transform: uppercase; }}
      .icon-button {{
        width: 44px;
        height: 44px;
        display: grid;
        place-items: center;
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        color: var(--muted);
        text-decoration: none;
        background: color-mix(in srgb, var(--panel-solid) 70%, transparent);
      }}
      .console-canvas {{
        min-height: 0;
        height: 100%;
        overflow-y: visible;
        overflow-x: visible;
        padding: 0;
        background: transparent;
      }}
      .operator-hero {{
        display: none;
        grid-template-columns: minmax(0, 1fr) 260px;
        gap: 18px;
        align-items: stretch;
        margin-bottom: 18px;
      }}
      .hero-copy, .hero-live {{
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 82%, transparent);
        padding: 18px;
      }}
      .session-demo {{
        position: static;
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 18px;
        min-height: 0;
        height: 100%;
        margin-bottom: 0;
      }}
      .mock-browser {{
        min-width: 0;
        height: 100%;
        overflow: hidden;
        display: grid;
        grid-template-rows: 62px minmax(0, 1fr);
        border: 1px solid var(--border);
        border-radius: 20px;
        background: color-mix(in srgb, var(--panel-solid) 92%, transparent);
        box-shadow: 0 26px 70px rgba(38,31,21,0.12);
      }}
      .mock-topbar {{
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 13px;
        padding: 14px 20px;
        border-bottom: 1px solid var(--border);
        background: color-mix(in srgb, var(--panel-solid) 78%, transparent);
      }}
      .mock-actions {{ display: flex; align-items: center; gap: 9px; color: var(--muted); }}
      .mock-actions span {{ width: 11px; height: 11px; border-radius: var(--radius-pill); background: var(--faint); opacity: .56; }}
      .mock-url {{
        min-width: 0;
        height: 44px;
        display: flex;
        align-items: center;
        gap: 8px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        border: 1px solid var(--border);
        border-radius: var(--radius-pill);
        padding: 0 13px;
        color: var(--muted);
        background: color-mix(in srgb, var(--soft) 88%, transparent);
        font-size: 14px;
        font-weight: 650;
      }}
      .mock-page {{
        min-height: 0;
        overflow: hidden;
        display: grid;
        grid-template-rows: 72px minmax(0, 1fr);
        background: color-mix(in srgb, var(--panel-solid) 94%, transparent);
      }}
      .mock-nav {{
        display: grid;
        grid-template-columns: 46px minmax(160px, 1fr) repeat(5, minmax(58px, 84px));
        gap: 14px;
        align-items: center;
        padding: 14px 22px;
        border-bottom: 1px solid var(--border);
      }}
      .li-badge {{
        width: 38px;
        height: 38px;
        display: grid;
        place-items: center;
        border-radius: 7px;
        background: #0a66c2;
        color: white;
        font-weight: 850;
        font-size: 20px;
      }}
      .search-pill {{ height: 34px; border-radius: 9px; background: var(--soft); border: 1px solid var(--border); }}
      .nav-dot {{ min-width: 0; height: auto; display: grid; place-items: center; color: var(--muted); font-size: 12px; font-weight: 650; border: 0; background: transparent; }}
      .mock-content {{
        min-height: 0;
        display: grid;
        grid-template-columns: 1fr;
        gap: 0;
        padding: 0 22px 22px;
      }}
      .filter-panel, .profile-panel, .agent-card, .highlights {{
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 80%, transparent);
      }}
      .filter-panel {{ display: none; gap: 10px; align-content: start; padding: 14px; }}
      .filter-line {{ height: 30px; border-radius: 8px; background: var(--soft); border: 1px solid var(--border); }}
      .profile-panel {{ min-width: 0; overflow: hidden; }}
      .profile-head {{ display: grid; grid-template-columns: 150px minmax(0,1fr) 210px; gap: 26px; align-items: center; padding: 34px 28px; border-bottom: 1px solid var(--border); }}
      .avatar {{
        width: 132px;
        height: 132px;
        border-radius: 50%;
        background:
          linear-gradient(0deg, rgba(0,0,0,0.04), rgba(0,0,0,0.04)),
          url('https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=320&q=80') center / cover;
        border: 5px solid color-mix(in srgb, var(--panel-solid) 88%, transparent);
        box-shadow: 0 10px 26px rgba(0,0,0,0.14);
      }}
      .profile-name {{ font-size: 28px; font-weight: 820; letter-spacing: 0; }}
      .profile-role {{ margin-top: 6px; color: var(--text); font-size: 16px; font-weight: 650; }}
      .profile-actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
      .mini-button {{ height: 42px; padding: 0 18px; display: inline-flex; align-items: center; border-radius: 10px; border: 1px solid var(--border); background: var(--soft); font-size: 14px; font-weight: 750; }}
      .mini-button.primary {{ background: #0a66c2; color: white; border-color: #0a66c2; }}
      .profile-body {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 24px; padding: 28px; }}
      .note-list {{ margin: 10px 0 0; padding-left: 18px; color: var(--text); font-size: 13px; line-height: 1.7; }}
      .highlights {{ padding: 14px; }}
      .agent-card {{
        position: static;
        width: 100%;
        padding: 20px;
        background: color-mix(in srgb, #eef9f1 82%, var(--panel-solid));
        box-shadow: var(--shadow-float);
      }}
      [data-theme="dark"] .agent-card {{ background: color-mix(in srgb, #203328 66%, var(--panel-solid)); }}
      .handoff-card {{
        position: absolute;
        z-index: 20;
        left: 28px;
        bottom: 20px;
        width: 286px;
        padding: 16px;
        border: 1px solid var(--border);
        border-radius: 18px;
        background: color-mix(in srgb, var(--panel-solid) 92%, transparent);
        box-shadow: var(--shadow-float);
        backdrop-filter: blur(24px) saturate(1.15);
      }}
      .handoff-head {{ display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; gap: 12px; align-items: center; }}
      .handoff-icon {{ width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px; color: white; background: #0a66c2; font-weight: 850; }}
      .handoff-actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }}
      .status-rail {{
        display: none;
        gap: 16px;
      }}
      .status-card {{
        min-width: 0;
        display: grid;
        grid-template-columns: 38px minmax(0, 1fr) auto;
        align-items: center;
        gap: 12px;
        padding: 16px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 72%, transparent);
      }}
      .feature-strip {{
        position: absolute;
        z-index: 14;
        left: 50%;
        bottom: 28px;
        transform: translateX(-50%);
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 24px;
        width: min(760px, 74%);
        margin: 0;
        padding: 14px 20px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: color-mix(in srgb, var(--panel-solid) 74%, transparent);
        box-shadow: 0 16px 44px rgba(33, 26, 17, 0.12), 0 0 0 1px var(--border);
        backdrop-filter: blur(18px) saturate(1.12);
      }}
      .feature-item {{ display: grid; grid-template-columns: 34px minmax(0,1fr); gap: 10px; align-items: center; min-width: 0; }}
      .feature-item b {{ display: block; font-size: 12px; }}
      .feature-item span {{ color: var(--muted); font-size: 11px; }}
      .eyebrow {{ margin: 0 0 8px; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }}
      h1, h2, h3, p {{ margin-top: 0; }}
      h1 {{ margin-bottom: 10px; font-size: clamp(28px, 2.4vw, 36px); line-height: 1.06; letter-spacing: 0; }}
      h2 {{ margin-bottom: 12px; font-size: 16px; letter-spacing: 0; }}
      h3 {{ margin-bottom: 10px; font-size: 14px; letter-spacing: 0; }}
      .muted {{ color: var(--muted); }}
      .operator-strip, .metric-grid {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin-top: 18px;
      }}
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 12px; }}
      .operator-tile, .metric {{
        min-width: 0;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: var(--soft);
        padding: 13px;
      }}
      .operator-tile span, .metric span {{ display: block; color: var(--muted); font-size: 11px; font-weight: 760; }}
      .operator-tile b, .metric b {{ display: block; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 17px; }}
      .dashboard-panels {{
        display: none;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 18px;
        margin-bottom: 18px;
      }}
      .section-panel {{
        min-width: 0;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 78%, transparent);
        padding: 18px;
      }}
      .status-row {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 12px;
        align-items: center;
        padding: 12px 0;
        border-top: 1px solid var(--border);
      }}
      .status-row:first-of-type {{ border-top: 0; padding-top: 0; }}
      .profile-head .status-row {{ grid-template-columns: 34px minmax(0, 1fr); padding: 8px 0; border-top: 0; }}
      .pill {{
        display: inline-flex;
        align-items: center;
        gap: 7px;
        border: 1px solid var(--border);
        background: var(--soft);
        border-radius: var(--radius-pill);
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 760;
        white-space: nowrap;
      }}
      .dot {{ width: 7px; height: 7px; border-radius: var(--radius-pill); background: var(--faint); }}
      .dot.ok {{ background: var(--green); }}
      .dot.warn {{ background: var(--amber); }}
      .copy-row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; }}
      input {{
        width: 100%;
        height: 44px;
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        padding: 0 12px;
        background: var(--soft);
        color: var(--text);
        font: inherit;
      }}
      .live-grid {{ display: grid; gap: 10px; }}
      .live-item {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 10px;
        padding: 11px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: var(--soft);
      }}
      .live-title {{ font-weight: 760; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      .live-sub {{ margin-top: 3px; color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      .steps {{ display: grid; gap: 12px; }}
      .step {{ display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 10px; align-items: start; }}
      .num {{ width: 30px; height: 30px; display: grid; place-items: center; border-radius: var(--radius-pill); background: var(--soft); border: 1px solid var(--border); color: var(--muted); font-weight: 800; }}
      .snippet-grid {{ display: none; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
      .snippet-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }}
      pre {{
        margin: 0;
        overflow: auto;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--soft);
        color: var(--text);
        padding: 13px;
        font-size: 12px;
        line-height: 1.5;
        white-space: pre-wrap;
        overflow-wrap: break-word;
        word-break: normal;
      }}
      .request-card pre {{ max-height: none; }}
      .state-list {{ display: grid; gap: 16px; }}
      .state-item {{
        display: grid;
        grid-template-columns: 46px minmax(0, 1fr) auto;
        gap: 14px;
        align-items: center;
        min-height: 86px;
        padding: 18px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: color-mix(in srgb, var(--panel-solid) 76%, transparent);
      }}
      .state-title {{ min-width: 0; overflow-wrap: anywhere; font-weight: 760; }}
      .state-subtitle {{ margin-top: 2px; color: var(--muted); font-size: 13px; font-weight: 560; }}
      .surface .muted {{ overflow-wrap: anywhere; }}
      .state-dot {{ width: 8px; height: 8px; border-radius: var(--radius-pill); background: var(--green); }}
      .mini-avatar {{
        width: 46px;
        height: 46px;
        border-radius: 50%;
        background: url('https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=120&q=80') center / cover;
        border: 2px solid color-mix(in srgb, var(--panel-solid) 84%, transparent);
        box-shadow: 0 10px 24px rgba(0,0,0,.12);
      }}
      .li-mini {{ display: grid; place-items: center; width: 22px; height: 22px; border-radius: 5px; background: #0a66c2; color: white; font-size: 14px; font-weight: 850; }}
      .danger {{ color: var(--red); }}
      @media (min-width: 1101px) and (max-width: 1400px) {{
        body {{ padding: 18px 20px; }}
        .page-shell {{ width: min(1320px, calc(100vw - 64px)); }}
        .hero-logo {{ font-size: 44px; }}
        .hero-line {{ font-size: 22px; }}
        .app-grid {{ grid-template-columns: 240px minmax(0, 1fr) 240px; }}
        .sidebar, .state-panel {{ padding: 26px 20px; }}
        .session-card {{ grid-template-columns: 40px minmax(0, 1fr) auto; padding: 16px 14px; }}
        .state-item {{ grid-template-columns: 40px minmax(0, 1fr) auto; min-height: 82px; padding: 16px; }}
        .profile-head {{ grid-template-columns: 124px minmax(0,1fr) 150px; gap: 18px; padding: 28px 24px; }}
        .avatar {{ width: 108px; height: 108px; }}
        .profile-name {{ font-size: 25px; }}
        .profile-role {{ font-size: 15px; }}
        .profile-body {{ grid-template-columns: minmax(0, 1fr) 300px; gap: 18px; padding: 24px; }}
        .mock-nav {{ grid-template-columns: 42px minmax(130px, 1fr) repeat(5, minmax(44px, 76px)); gap: 10px; padding: 14px 18px; }}
        .mock-content {{ padding: 0 18px 18px; }}
        .feature-strip {{ width: min(690px, 70%); }}
        .handoff-card {{ width: 286px; }}
      }}
      @media (max-width: 1100px) {{
        body {{ overflow: auto; }}
        .app-window {{ height: auto; min-height: 0; }}
        .app-grid {{ grid-template-columns: 280px minmax(0, 1fr); }}
        .state-panel {{ grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--border); }}
        .state-list {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
        .snippet-grid {{ grid-template-columns: 1fr; }}
        .session-demo {{ grid-template-columns: 1fr; }}
        .status-rail {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
        .agent-card {{ right: 26px; bottom: 104px; width: min(310px, 42%); }}
      }}
      @media (min-width: 901px) and (max-width: 1100px) {{
        .app-window {{ height: 836px; overflow: hidden; }}
        .app-grid {{ height: 100%; }}
      }}
      @media (max-width: 900px) {{
        body {{ padding: 12px; overflow: auto; }}
        .page-shell {{ width: 100%; gap: 16px; }}
        .product-hero {{ padding-top: 8px; }}
        .hero-logo {{ gap: 10px; font-size: 32px; }}
        .hero-line {{ max-width: 340px; font-size: 18px; line-height: 1.12; text-wrap: balance; }}
        .hero-subline {{ max-width: 320px; font-size: 15px; line-height: 1.3; text-wrap: balance; }}
        .app-window {{ width: 100%; grid-template-rows: auto auto; border-radius: 18px; }}
        .window-bar {{ grid-template-columns: 1fr; gap: 14px; padding: 18px; text-align: left; }}
        .brand-block {{ text-align: left; }}
        .top-actions {{ justify-content: flex-start; align-items: center; }}
        .app-grid, .operator-hero, .dashboard-panels {{ grid-template-columns: 1fr; }}
        .browser-stage {{ order: -1; }}
        .sidebar, .state-panel {{ border: 0; border-top: 1px solid var(--border); }}
        .lease-summary {{ width: auto; margin-top: 16px; }}
        .browser-stage {{ padding: 18px; }}
        .feature-strip {{ position: static; transform: none; width: 100%; margin: 0; }}
        .stage-title {{ align-items: start; }}
        .browser-toolbar {{ display: none; }}
        .operator-strip, .state-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        .mock-content, .profile-body, .feature-strip, .status-rail {{ grid-template-columns: 1fr; }}
        .filter-panel, .profile-body {{ display: none; }}
        .mock-nav {{ grid-template-columns: 38px minmax(0, 1fr) 36px 36px; }}
        .mock-nav .nav-dot:nth-of-type(n+4) {{ display: none; }}
        .mock-url {{ font-size: 0; }}
        .mock-url .lock {{ font-size: 11px; }}
        .mock-url::after {{ content: "linkedin.com"; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 12px; font-weight: 650; }}
        .profile-head {{ grid-template-columns: minmax(0, 1fr); gap: 14px; padding: 24px 18px; text-align: center; }}
        .avatar {{ width: 86px; height: 86px; margin: 0 auto; }}
        .profile-name {{ font-size: 26px; line-height: 1.02; }}
        .profile-role {{ font-size: 14px; line-height: 1.25; }}
        .profile-actions {{ justify-content: center; }}
        .profile-head > .stack {{ display: none; }}
        .profile-head .kebab {{ display: none; }}
        .mock-browser {{ order: 1; max-height: none; }}
        .mock-page {{ grid-template-rows: 62px minmax(0, 1fr); }}
        .handoff-card {{ order: 0; }}
        .agent-card {{ order: 2; }}
        .agent-card, .handoff-card {{ position: static; width: 100%; margin-top: 0; }}
        .session-demo {{ gap: 12px; }}
        .button-small, .api-link, .icon-button {{ min-width: 44px; min-height: 44px; }}
      }}
      @media (max-width: 560px) {{
        body {{ padding: 8px; }}
        .operator-strip, .metric-grid, .state-list {{ grid-template-columns: 1fr; }}
        .copy-row, .status-row {{ grid-template-columns: 1fr; }}
        .dashboard-panels, .snippet-grid {{ gap: 12px; }}
        .section-panel {{ padding: 16px; }}
        h1 {{ font-size: 32px; }}
        .product-hero {{ gap: 6px; }}
        .hero-logo {{ font-size: 26px; }}
        .hero-logo sup {{ margin-left: -6px; font-size: 10px; }}
        .hero-line {{ max-width: 300px; font-size: 15px; }}
        .app-window {{ border-radius: 16px; }}
        .window-bar {{ grid-template-columns: minmax(0, 1fr) auto; padding: 14px; }}
        .brand-mini-mark {{ width: 24px; height: 24px; border-radius: 7px; }}
        .brand-mini-mark::after {{ inset: 5px; border-width: 2px; }}
        .brand-title {{ font-size: 16px; }}
        .top-actions {{ display: flex; justify-content: flex-end; width: auto; }}
        .top-actions .api-link {{ display: none; }}
        .top-actions > * {{ width: auto; }}
        .button-soft {{ min-height: 40px; padding: 0 14px; }}
        .stage-title {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
        .stage-title .button-small {{ display: none; }}
        .browser-stage {{ padding: 16px; }}
        .mobile-quick-state {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
        }}
        .mobile-quick-state span {{
          min-width: 0;
          display: grid;
          gap: 2px;
          padding: 10px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: color-mix(in srgb, var(--panel-solid) 76%, transparent);
          color: var(--muted);
          font-size: 11px;
          font-weight: 700;
          text-align: center;
        }}
        .mobile-quick-state b {{ color: var(--text); font-size: 13px; }}
        .session-demo {{ position: relative; height: auto; min-height: 0; padding-bottom: 0; }}
        .mock-browser {{ height: auto; }}
        .handoff-card {{
          position: static;
          order: 0;
          width: 100%;
          padding: 14px;
          border-radius: 16px;
        }}
        .handoff-head {{ grid-template-columns: 36px minmax(0, 1fr) auto; }}
        .handoff-icon {{ width: 36px; height: 36px; border-radius: 10px; }}
        .handoff-actions {{ margin-top: 12px; }}
        .mock-topbar {{ grid-template-columns: auto minmax(0, 1fr) 44px; padding: 12px; }}
        .mock-actions {{ gap: 7px; }}
        .mock-url {{ height: 38px; padding: 0 10px; }}
        .mock-nav {{ padding: 12px; gap: 10px; }}
        .profile-panel {{ border-radius: 16px; }}
        .mini-button {{ min-height: 40px; padding: 0 14px; }}
        .feature-strip {{ margin-bottom: 34px; }}
      }}
    </style>
  </head>
  <body>
    <div class="page-shell">
      <section class="product-hero" aria-label="OpenBrowser overview">
        <div class="hero-logo"><span class="brand-mark" aria-hidden="true"></span><span>OpenBrowser</span><sup>TM</sup></div>
        <div class="hero-line">The browser infrastructure for AI agents.</div>
        <div class="hero-subline">Real browsers. Real logins. Human in the loop.</div>
      </section>
    <div class="app-window">
      <header class="window-bar">
        <div class="brand-block">
          <span class="brand-mini-mark" aria-hidden="true"></span>
          <div class="brand-title">OpenBrowser</div>
          <div class="brand-subtitle">The browser API for AI agents</div>
        </div>
        <div class="top-actions">
          <a class="api-link" href="/openbrowser/reference">Docs</a>
          <button class="button-soft" type="button" id="themeToggle">Night mode</button>
          <a class="api-link" href="/openbrowser/reference">API</a>
        </div>
      </header>
      <main class="app-grid">
        <aside class="sidebar">
          <div class="panel-title">Browser Sessions</div>
          <div class="session-list">
            <div class="session-card is-active">
              <div class="app-logo">in</div>
              <div>
                <div class="session-name">work-main</div>
                <div class="session-status">Active lease</div>
              </div>
              <div class="kebab">...</div>
            </div>
            <div class="session-card">
              <div class="app-logo google">G</div>
              <div>
                <div class="session-name">research-01</div>
                <div class="session-status muted">Idle</div>
              </div>
              <div class="kebab">...</div>
            </div>
            <div class="session-card">
              <div class="app-logo hubspot">h</div>
              <div>
                <div class="session-name">sales-bot</div>
                <div class="session-status muted">Idle</div>
              </div>
              <div class="kebab">...</div>
            </div>
          </div>
          <section class="lease-summary" aria-label="Browser capacity">
            <div class="metric-line"><span class="label">Active leases</span><b>{safe_public_lease_count}</b><span class="state-dot"></span></div>
            <div class="metric-line"><span class="label">Browsers ready</span><b>{safe_public_slot_count}</b><span class="state-dot"></span></div>
            <pre class="copy-source" id="remoteMcpSnippet"><code>{safe_remote_mcp_snippet}</code></pre>
            <button class="secondary button-small" type="button" data-copy-target="remoteMcpSnippet">Copy Remote MCP</button>
          </section>
        </aside>
        <section class="browser-stage">
          <div class="stage-title">
            <div class="stage-title-main"><span>Live Browser Session</span><span class="status-chip">work-main</span></div>
            <a class="button button-outline button-small" href="/openbrowser/reference">Open in new tab</a>
          </div>
          <div class="mobile-quick-state" aria-label="Mobile session summary">
            <span><b>work-main</b>Active</span>
            <span><b>research</b>Idle</span>
            <span><b>Proxy</b>US-West</span>
          </div>
          <div class="browser-shell">
            <div class="browser-toolbar">
              <div class="toolbar-left" aria-hidden="true"><span></span><span></span><span></span></div>
              <div class="toolbar-url"><span class="lock">API</span>{safe_base_url}</div>
              <a class="icon-button" href="/openbrowser/reference" aria-label="Open API reference">API</a>
            </div>
            <div class="console-canvas">
              <section class="session-demo" aria-label="OpenBrowser product preview">
                <div class="mock-browser">
                  <div class="mock-topbar">
                    <div class="mock-actions" aria-hidden="true"><span></span><span></span><span></span></div>
                    <div class="mock-url"><span class="lock">lock</span>linkedin.com/in/alexchen</div>
                    <a class="icon-button" href="/openbrowser/reference" aria-label="Open API reference">...</a>
                  </div>
                  <div class="mock-page">
                    <div class="mock-nav">
                      <div class="li-badge">in</div>
                      <div class="search-pill"></div>
                      <div class="nav-dot">Home</div>
                      <div class="nav-dot">My Network</div>
                      <div class="nav-dot">Jobs</div>
                      <div class="nav-dot">Messaging</div>
                      <div class="nav-dot">Me</div>
                    </div>
                    <div class="mock-content">
                      <aside class="filter-panel" aria-label="Search filters">
                        <div class="label">Keywords</div>
                        <div class="filter-line"></div>
                        <div class="label">Location</div>
                        <div class="filter-line"></div>
                        <div class="label">Company</div>
                        <div class="filter-line"></div>
                        <div class="label">Connections</div>
                        <div class="filter-line"></div>
                      </aside>
                      <section class="profile-panel">
                        <div class="profile-head">
                          <div class="avatar" aria-hidden="true"></div>
                          <div>
                            <div class="profile-name">Alex Chen <span class="pill">2nd</span></div>
                            <div class="profile-role">Head of Infrastructure · Scale AI</div>
                            <div class="muted">San Francisco, California, United States</div>
                            <div class="muted">500+ connections</div>
                            <div class="profile-actions">
                              <span class="mini-button primary">Message</span>
                              <span class="mini-button">+ Follow</span>
                              <span class="mini-button">More</span>
                            </div>
                          </div>
                          <div class="stack">
                            <div class="status-row"><span class="pill">S</span><div><b>Scale AI</b></div></div>
                            <div class="status-row"><span class="pill">S</span><div><b>Stanford University</b></div></div>
                          </div>
                        </div>
                        <div class="profile-body">
                          <div>
                            <h3>Agent notes <span class="pill">Private</span></h3>
                            <ul class="note-list">
                              <li>Strong background in infra at scale</li>
                              <li>Led Kubernetes migration that improved reliability by 35%</li>
                              <li>Good fit for Director, Infrastructure role</li>
                            </ul>
                          </div>
                          <aside class="highlights">
                            <div class="agent-card">
                              <div class="label">Agent action</div>
                              <div class="state-title">Reviewing profile...</div>
                              <p class="muted">Save to “AI Infra · Prospects”</p>
                            </div>
                          </aside>
                        </div>
                      </section>
                    </div>
                  </div>
                </div>
                <aside class="status-rail">
                  <div class="status-card"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2v4"></path><path d="M12 18v4"></path><path d="M4.93 4.93l2.83 2.83"></path><path d="M16.24 16.24l2.83 2.83"></path><path d="M2 12h4"></path><path d="M18 12h4"></path><path d="M4.93 19.07l2.83-2.83"></path><path d="M16.24 7.76l2.83-2.83"></path></svg></div><div><div class="state-title">Active lease</div><div class="state-subtitle">Expires in 28m 47s</div></div><span class="state-dot"></span></div>
                  <div class="status-card"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"></circle><path d="M4 21c1.7-4 14.3-4 16 0"></path></svg></div><div><div class="state-title">Profile</div><div class="state-subtitle">work-main</div></div><span class="state-dot"></span></div>
                  <div class="status-card"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="10" width="16" height="10" rx="2"></rect><path d="M8 10V7a4 4 0 0 1 8 0v3"></path></svg></div><div><div class="state-title">Human handoff</div><div class="state-subtitle">Waiting for approval</div></div><span class="state-dot"></span></div>
                  <div class="status-card"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2-2a5 5 0 0 0-7.07-7.07l-1.2 1.2"></path><path d="M14 11a5 5 0 0 0-7.07 0l-2 2A5 5 0 0 0 12 20.07l1.2-1.2"></path></svg></div><div><div class="state-title">Connection</div><div class="state-subtitle">CDP connected</div></div><span class="state-dot"></span></div>
                </aside>
                <div class="handoff-card">
                  <div class="handoff-head">
                    <div class="handoff-icon">in</div>
                    <div><div class="state-title">LinkedIn sign-in needs approval</div><div class="state-subtitle">for work-main browser profile.</div></div>
                    <svg class="inline-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"></path><path d="m9 12 2 2 4-4"></path></svg>
                  </div>
                  <div class="handoff-actions"><button class="secondary" type="button">Decline</button><button type="button">Approve</button></div>
                </div>
              </section>
              <div class="feature-strip" aria-label="Core OpenBrowser guarantees">
                <div class="feature-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M3 9h18"></path></svg></div><div><b>Real browsers</b><span>No emulation</span></div></div>
                <div class="feature-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"></path></svg></div><div><b>Human in the loop</b><span>Approval for sensitive actions</span></div></div>
                <div class="feature-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v16H4z"></path><path d="M8 8h8"></path><path d="M8 12h8"></path><path d="M8 16h5"></path></svg></div><div><b>Audit ready</b><span>Full logs & traceability</span></div></div>
              </div>
              <section class="operator-hero">
                <div class="hero-copy">
                  <p class="eyebrow">Operator console</p>
                  <h1>Browser API control surface for remote agents.</h1>
                  <p class="muted">Lease isolated Chrome sessions, switch persisted profiles, route through configured proxies, hand login walls to a human, and audit agent behavior from one broker.</p>
                  <div class="operator-strip" aria-label="Public broker summary">
                    <div class="operator-tile"><span>Remote API</span><b>{safe_api_key_state}</b></div>
                    <div class="operator-tile"><span>Pool slots</span><b>{safe_public_slot_count}</b></div>
                    <div class="operator-tile"><span>Profiles</span><b>{safe_public_identity_count}</b></div>
                    <div class="operator-tile"><span>Proxy routes</span><b>{safe_public_proxy_count}</b></div>
                  </div>
                  <div class="status-row">
                    <div><div class="label">Remote API base</div><div class="muted"><code>{safe_base_url}</code></div></div>
                    <span class="pill"><span class="dot {safe_api_dot}"></span>{safe_api_key_state}</span>
                  </div>
                  <div class="status-row">
                    <div><div class="label">Bearer token</div><div class="muted">Remote agents use <code>Authorization: Bearer &lt;OPENBROWSER_API_KEY&gt;</code>.</div></div>
                  </div>
                </div>
                <div class="hero-live">
                  <h2>Live Status</h2>
                  <p class="muted">Paste an OpenBrowser API key locally in this tab. It is stored in sessionStorage and used only for bearer-authenticated fetches.</p>
                  <div class="copy-row">
                    <input id="apiKey" type="password" autocomplete="off" placeholder="OPENBROWSER_API_KEY">
                    <button type="button" id="saveKey">Load</button>
                  </div>
                  <p id="liveStatus" class="muted">Live data locked. Public broker counts are visible; detailed leases, auth, issues, and telemetry require a key.</p>
                  <div class="metric-grid" aria-label="Live operator summary">
                    <div class="metric"><span>Audit</span><b id="metricAudit">unlock</b></div>
                    <div class="metric"><span>Leases</span><b id="metricLeases">{safe_public_lease_count}</b></div>
                    <div class="metric"><span>Auth</span><b id="metricAuth">unlock</b></div>
                    <div class="metric"><span>Profiles</span><b id="metricProfiles">{safe_public_identity_count}</b></div>
                    <div class="metric"><span>Proxies</span><b id="metricProxies">{safe_public_proxy_count}</b></div>
                    <div class="metric"><span>Issues</span><b id="metricIssues">unlock</b></div>
                  </div>
                </div>
              </section>
              <div class="dashboard-panels">
                <section class="section-panel">
                  <h2>Identities And Proxies</h2>
                  <div id="identities" class="live-grid"><div class="muted">Unlock live status to see profiles, proxy refs, and parallel-session limits.</div></div>
                </section>
                <section class="section-panel">
                  <h2>Sessions And Audit</h2>
                  <div id="sessions" class="live-grid"><div class="muted">Unlock live status to see active leases, auth requests, open issues, telemetry, and audit score.</div></div>
                </section>
              </div>
              <div class="snippet-grid">
                <section class="section-panel">
                  <div class="snippet-head"><h2>MCP Config</h2><button class="secondary button-small" type="button" data-copy-target="mcpConfigSnippet">Copy</button></div>
                  <pre id="mcpConfigSnippet"><code>{safe_mcp_config_snippet}</code></pre>
                </section>
                <section class="section-panel">
                  <div class="snippet-head"><h2>API Smoke Test</h2><button class="secondary button-small" type="button" data-copy-target="curlSnippet">Copy</button></div>
                  <pre id="curlSnippet"><code>{safe_curl_snippet}</code></pre>
                </section>
                <section class="section-panel">
                  <h2>Agent Workflow</h2>
                  <div class="steps">
                    <div class="step"><div class="num">1</div><div><b>Lease</b><div class="muted">Create an isolated browser session. Pass <code>identity_id</code> for a persisted profile.</div></div></div>
                    <div class="step"><div class="num">2</div><div><b>Act</b><div class="muted">Navigate, snapshot, click, type, upload, use keyboard events, and request human auth when a login wall appears.</div></div></div>
                    <div class="step"><div class="num">3</div><div><b>Release</b><div class="muted">Release the lease, then record feedback/telemetry and run audit.</div></div></div>
                  </div>
                </section>
              </div>
            </div>
          </div>
        </section>
        <aside class="state-panel">
          <div class="panel-title">Session State</div>
          <div class="state-list">
            <div class="state-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2v4"></path><path d="M12 18v4"></path><path d="M4.93 4.93l2.83 2.83"></path><path d="M16.24 16.24l2.83 2.83"></path><path d="M2 12h4"></path><path d="M18 12h4"></path></svg></div><div><div class="state-title">Active lease</div><div class="state-subtitle">Expires in 28m 47s</div></div><span class="state-dot"></span></div>
            <div class="state-item profile-state"><div class="mini-avatar" aria-hidden="true"></div><div><div class="state-title">Profile: work-main</div><div class="state-subtitle">Signed in as Maria Santos</div></div><span class="li-mini">in</span></div>
            <div class="state-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><path d="M2 12h20"></path><path d="M12 2a15.3 15.3 0 0 1 0 20"></path><path d="M12 2a15.3 15.3 0 0 0 0 20"></path></svg></div><div><div class="state-title">Residential proxy</div><div class="state-subtitle">US-West (CA)</div></div></div>
            <div class="state-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="10" width="16" height="10" rx="2"></rect><path d="M8 10V7a4 4 0 0 1 8 0v3"></path></svg></div><div><div class="state-title">Human handoff</div><div class="state-subtitle">Waiting for approval</div></div></div>
            <div class="state-item"><div class="state-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2-2a5 5 0 0 0-7.07-7.07l-1.2 1.2"></path><path d="M14 11a5 5 0 0 0-7.07 0l-2 2A5 5 0 0 0 12 20.07l1.2-1.2"></path></svg></div><div><div class="state-title">Connection</div><div class="state-subtitle">CDP connected</div></div></div>
          </div>
        </aside>
      </main>
    </div>
    </div>
    <script>
      const displayBaseUrl = {_script_json(base_url)};
      const apiBaseUrl = new URL('/openbrowser/v1', window.location.origin).toString();
      const themeButton = document.getElementById('themeToggle');
      const setThemeLabel = () => {{
        themeButton.textContent = document.documentElement.dataset.theme === 'dark' ? 'Day mode' : 'Night mode';
      }};
      setThemeLabel();
      themeButton.addEventListener('click', () => {{
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        localStorage.setItem('openbrowser-theme', next);
        setThemeLabel();
      }});
      document.querySelectorAll('[data-copy]').forEach((button) => {{
        button.addEventListener('click', async () => {{
          const old = button.textContent;
          try {{
            await navigator.clipboard.writeText(button.dataset.copy);
            button.textContent = 'Copied';
          }} catch (error) {{
            button.textContent = 'Copy failed';
          }}
          setTimeout(() => button.textContent = old, 1200);
        }});
      }});
      const apiKeyInput = document.getElementById('apiKey');
      const liveStatus = document.getElementById('liveStatus');
      const identitiesBox = document.getElementById('identities');
      const sessionsBox = document.getElementById('sessions');
      const metricAudit = document.getElementById('metricAudit');
      const metricLeases = document.getElementById('metricLeases');
      const metricAuth = document.getElementById('metricAuth');
      const metricProfiles = document.getElementById('metricProfiles');
      const metricProxies = document.getElementById('metricProxies');
      const metricIssues = document.getElementById('metricIssues');
      apiKeyInput.value = sessionStorage.getItem('openbrowser-api-key') || '';
      const headers = () => ({{'authorization': `Bearer ${{apiKeyInput.value.trim()}}`, 'content-type': 'application/json'}});
      const getJson = async (path) => {{
        const response = await fetch(apiBaseUrl + path, {{headers: headers()}});
        if (!response.ok) throw new Error(`${{response.status}} ${{await response.text()}}`);
        return response.json();
      }};
      const liveItem = (title, sub, badge = '') => {{
        const row = document.createElement('div');
        row.className = 'live-item';
        const copy = document.createElement('div');
        const titleEl = document.createElement('div');
        titleEl.className = 'live-title';
        titleEl.textContent = String(title ?? '');
        const subEl = document.createElement('div');
        subEl.className = 'live-sub';
        subEl.textContent = String(sub ?? '');
        const badgeEl = document.createElement('span');
        badgeEl.className = 'pill';
        badgeEl.textContent = String(badge ?? '');
        copy.append(titleEl, subEl);
        row.append(copy, badgeEl);
        return row;
      }};
      const mutedText = (text) => {{
        const el = document.createElement('div');
        el.className = 'muted';
        el.textContent = text;
        return el;
      }};
      const renderItems = (container, rows, emptyText) => {{
        if (!rows.length) {{
          container.replaceChildren(mutedText(emptyText));
          return;
        }}
        container.replaceChildren(...rows);
      }};
      const loadLive = async () => {{
        const key = apiKeyInput.value.trim();
        if (!key) {{
          liveStatus.textContent = 'Live data locked.';
          return;
        }}
        sessionStorage.setItem('openbrowser-api-key', key);
        liveStatus.textContent = 'Loading live broker state...';
        try {{
          const [health, identities, authStatus, audit, issues, telemetry] = await Promise.all([
            getJson('/health'),
            getJson('/identities'),
            getJson('/auth/status'),
            getJson('/audit'),
            getJson('/feedback/issues?status=open&limit=5'),
            getJson('/telemetry/summary?window_seconds=86400'),
          ]);
          const identityEntries = Object.entries(identities.identities || {{}});
          const pool = health.pool || {{}};
          const leases = Object.values(pool.leases || {{}});
          const authCount = Object.keys((authStatus.requests || {{}})).length;
          const proxyCount = (identities.proxy_refs || []).length;
          metricAudit.textContent = String(audit.score ?? 'n/a');
          metricLeases.textContent = String(leases.length);
          metricAuth.textContent = String(authCount);
          metricProfiles.textContent = String(identityEntries.length);
          metricProxies.textContent = String(proxyCount);
          metricIssues.textContent = String(issues.count || 0);
          renderItems(
            identitiesBox,
            identityEntries.map(([id, data]) =>
              liveItem(id, `proxy=${{data.proxy_ref || 'none'}} · max_parallel=${{data.max_parallel_sessions}} · timezone=${{data.timezone || 'default'}}`, data.active_on_slot ? 'active' : 'ready')
            ),
            'No identities configured.'
          );
          renderItems(
            sessionsBox,
            [
              liveItem('Audit score', `open issues=${{issues.count || 0}} · telemetry events=${{telemetry.count || 0}}`, String(audit.score ?? 'n/a')),
              liveItem('Active leases', `${{leases.length}} currently held`, leases.length ? 'busy' : 'clear'),
              liveItem('Auth requests', `${{authCount}} tracked`, 'handoff'),
            ],
            'No session data returned.'
          );
          liveStatus.textContent = 'Live state loaded.';
        }} catch (error) {{
          liveStatus.textContent = `Live load failed: ${{String(error.message || error).slice(0, 220)}}`;
        }}
      }};
      document.querySelectorAll('[data-copy-target]').forEach((button) => {{
        button.addEventListener('click', async () => {{
          const target = document.getElementById(button.dataset.copyTarget);
          const old = button.textContent;
          try {{
            await navigator.clipboard.writeText(target ? target.innerText : '');
            button.textContent = 'Copied';
          }} catch (error) {{
            button.textContent = 'Copy failed';
          }}
          setTimeout(() => button.textContent = old, 1200);
        }});
      }});
      document.getElementById('saveKey').addEventListener('click', loadLive);
      if (apiKeyInput.value) loadLive();
    </script>
  </body>
</html>
"""


def _openbrowser_reference_html() -> str:
    base_url = _openbrowser_base_url()
    safe_base_url = html.escape(base_url)
    endpoint_catalog = _openbrowser_endpoint_catalog()
    endpoints = []
    for key, value in endpoint_catalog.items():
        method, path = value.split(" ", 1)
        endpoints.append((method, path.removeprefix("/openbrowser/v1"), _openbrowser_endpoint_description(key)))
    endpoint_rows = "\n".join(
        f"<tr><td><code>{html.escape(method)}</code></td><td><code>{html.escape(path)}</code></td><td>{html.escape(description)}</td></tr>"
        for method, path, description in endpoints
    )
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OpenBrowser API Reference</title>
    <style>
      :root {{ color-scheme: light dark; --page:#f4f1ea; --panel:rgba(255,255,255,.88); --text:#20201d; --muted:#756f66; --border:rgba(42,35,27,.14); --soft:#f6f4ef; --strong:#24231f; --strong-text:#fff; --shadow:0 24px 80px rgba(33,26,17,.16); }}
      @media (prefers-color-scheme: dark) {{ :root {{ --page:#101211; --panel:rgba(32,32,30,.9); --text:#f4f1ea; --muted:#aaa49a; --border:rgba(255,255,255,.12); --soft:#2a2926; --strong:#f4f1ea; --strong-text:#191918; --shadow:0 28px 90px rgba(0,0,0,.44); }} }}
      * {{ box-sizing: border-box; }}
      body {{ margin:0; min-height:100dvh; background:linear-gradient(180deg, rgba(247,244,236,.62), rgba(231,223,208,.88)), url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover fixed no-repeat; color:var(--text); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
      @media (prefers-color-scheme: dark) {{ body {{ background:linear-gradient(180deg, rgba(12,15,14,.78), rgba(12,15,14,.94)), url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover fixed no-repeat; }} }}
      main {{ width:min(1160px, calc(100vw - 32px)); margin:32px auto 52px; }}
      header, section {{ border:1px solid var(--border); background:var(--panel); border-radius:18px; padding:24px; margin-bottom:16px; box-shadow:var(--shadow); backdrop-filter:blur(22px) saturate(1.15); }}
      h1 {{ margin:0; display:flex; align-items:center; gap:12px; font-size:34px; letter-spacing:0; }}
      h2 {{ margin:0 0 12px; font-size:18px; }}
      p {{ color:var(--muted); line-height:1.5; }}
      a, button {{ color:inherit; }}
      .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }}
      .button {{ display:inline-flex; align-items:center; min-height:44px; padding:0 16px; border-radius:10px; border:1px solid var(--border); background:var(--strong); color:var(--strong-text); text-decoration:none; font-weight:760; }}
      .brand-mark {{ width:34px; height:34px; border:.12em solid currentColor; border-radius:24%; transform:rotate(30deg); position:relative; flex:0 0 auto; }}
      .brand-mark::after {{ content:""; position:absolute; inset:23%; border:.11em solid currentColor; border-radius:22%; }}
      code, pre {{ font-family:"SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace; }}
      pre {{ white-space:pre-wrap; overflow-wrap:break-word; background:var(--soft); border:1px solid var(--border); border-radius:12px; padding:14px; font-size:13px; }}
      table {{ width:100%; border-collapse:collapse; }}
      td, th {{ text-align:left; vertical-align:top; border-top:1px solid var(--border); padding:11px 8px; }}
      th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
      @media (max-width:720px) {{ main {{ width:min(100vw - 20px, 640px); }} td, th {{ display:block; width:100%; }} tr {{ display:block; border-top:1px solid var(--border); padding:8px 0; }} td {{ border:0; padding:4px 0; }} }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1><span class="brand-mark" aria-hidden="true"></span><span>OpenBrowser API Reference</span></h1>
        <p>Remote browser sessions use <code>Authorization: Bearer &lt;OPENBROWSER_API_KEY&gt;</code>. Base URL: <code>{safe_base_url}</code>.</p>
        <div class="actions">
          <a class="button" href="/openbrowser">Dashboard</a>
          <a class="button" href="/docs">FastAPI schema</a>
        </div>
      </header>
      <section>
        <h2>Install</h2>
        <pre>uv tool install git+https://github.com/floomhq/openbrowser.git
export OPENBROWSER_BASE_URL="{safe_base_url}"
export OPENBROWSER_API_KEY="&lt;token&gt;"
openbrowser-remote-mcp</pre>
      </section>
      <section>
        <h2>Endpoints</h2>
        <table>
          <thead><tr><th>Method</th><th>Path</th><th>Use</th></tr></thead>
          <tbody>{endpoint_rows}</tbody>
        </table>
      </section>
    </main>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def openbrowser_home_redirect() -> str:
    return _openbrowser_dashboard_html()


@app.get("/openbrowser", response_class=HTMLResponse)
async def openbrowser_dashboard() -> str:
    return _openbrowser_dashboard_html()


@app.get("/openbrowser/reference", response_class=HTMLResponse)
async def openbrowser_reference() -> str:
    return _openbrowser_reference_html()


@app.get("/favicon.ico")
async def openbrowser_favicon() -> Response:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#111827"/><path d="M32 9 52 20.5v23L32 55 12 43.5v-23L32 9Z" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/><path d="M32 22 41 27.2v9.6L32 42 23 36.8v-9.6L32 22Z" fill="none" stroke="#fff" stroke-width="4" stroke-linejoin="round"/></svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/openbrowser/v1/health")
async def openbrowser_health(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    pool = status()
    safe_slots = []
    for slot in pool.get("slots", []):
        safe_slots.append({key: value for key, value in slot.items() if key != "profile_dir"})
    safe_leases = {}
    for lease_id, lease_data in (pool.get("leases") or {}).items():
        safe_leases[lease_id] = {key: value for key, value in lease_data.items() if key != "profile_dir"}
    return {
        "ok": True,
        "service": "openbrowser",
        "base_url": _openbrowser_base_url(),
        "pool": {
            "slots": safe_slots,
            "leases": safe_leases,
            "expired": pool.get("expired", []),
        },
    }


@app.get("/openbrowser/v1/docs")
async def openbrowser_docs(_auth: str = Depends(require_openbrowser_api_key)) -> dict[str, Any]:
    identities = redacted_status()
    return {
        "service": "openbrowser",
        "version": "v1",
        "auth": "Authorization: Bearer <OPENBROWSER_API_KEY>",
        "dashboard": "/openbrowser",
        "base_url": _openbrowser_base_url(),
        "endpoints": _openbrowser_endpoint_catalog(),
        "identities": {
            "generic": "omit identity_id for a neutral non-account browser",
            "configured": {
                identity_id: {
                    "label": item.get("label"),
                    "proxy_ref": item.get("proxy_ref"),
                    "max_parallel_sessions": item.get("max_parallel_sessions"),
                    "active_on_slot": item.get("active_on_slot"),
                }
                for identity_id, item in identities.get("identities", {}).items()
            },
            "proxy_refs": identities.get("proxy_refs", []),
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
    try:
        active_response = _active_identity_control_response(request)
        if active_response:
            return active_response
        return await auth_request(request)
    except IdentityError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
<html data-theme="light">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OpenBrowser Manual Control</title>
    <script>
      (() => {{
        try {{
          const saved = localStorage.getItem('openbrowser-theme');
          const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
          document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
        }} catch (error) {{
          document.documentElement.dataset.theme = 'light';
        }}
      }})();
    </script>
    <style>
      :root {{
        color-scheme: light dark;
        --page: #f4f1ea;
        --panel: #ffffff;
        --soft: #f6f4ef;
        --text: #20201d;
        --muted: #777269;
        --border: rgba(42,35,27,0.14);
        --strong: #24231f;
        --strong-text: #ffffff;
      }}
      [data-theme="dark"] {{
        --page: #101211;
        --panel: #20201e;
        --soft: #2a2926;
        --text: #f4f1ea;
        --muted: #aaa49a;
        --border: rgba(255,255,255,0.12);
        --strong: #f4f1ea;
        --strong-text: #191918;
      }}
      * {{ box-sizing: border-box; }}
      body {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: var(--page); color: var(--text); }}
      header {{ padding: 18px 22px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; align-items: center; }}
      main {{ padding: 16px; max-width: 1280px; margin: 0 auto; }}
      button {{ background: var(--strong); color: var(--strong-text); border: 1px solid var(--border); padding: 9px 12px; border-radius: 10px; cursor: pointer; font-weight: 700; }}
      button.secondary {{ background: var(--panel); color: var(--text); }}
      input {{ padding: 9px 10px; border: 1px solid var(--border); border-radius: 10px; min-width: min(520px, 70vw); background: var(--soft); color: var(--text); }}
      .muted {{ color: var(--muted); }}
      .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 14px; margin: 14px 0; }}
      .toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
      #screen {{ display: block; max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 14px; background: white; cursor: crosshair; }}
      #status {{ font-size: 14px; color: var(--muted); min-height: 20px; }}
    </style>
  </head>
  <body>
    <header>
      <div><b>OpenBrowser Manual Control</b><div class="muted">Manual browser control · Owner: {safe_owner}</div></div>
      <div class="toolbar"><span>Lease: <code>{safe_lease_id}</code></span><button class="secondary" type="button" id="themeToggle">Night mode</button></div>
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
        <p class="muted">Click the screenshot to control the held browser tab. Use this for login, passkey, or challenge prompts. This view does not expose session cookies, saved passwords, or proxy credentials.</p>
        <div id="status" data-expires-at="{safe_expires_at}">Control link active.</div>
      </div>
      <img id="screen" alt="Current browser screenshot" src="/auth/lease-control/{safe_token}/screenshot?ts=0">
    </main>
    <script>
      const token = {json.dumps(token)};
      const screen = document.getElementById('screen');
      const statusBox = document.getElementById('status');
      const setStatus = (text) => {{ statusBox.textContent = text; }};
      const themeButton = document.getElementById('themeToggle');
      const setThemeLabel = () => {{ themeButton.textContent = document.documentElement.dataset.theme === 'dark' ? 'Day mode' : 'Night mode'; }};
      setThemeLabel();
      themeButton.addEventListener('click', () => {{
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        localStorage.setItem('openbrowser-theme', next);
        setThemeLabel();
      }});
      const expiresAt = Number(statusBox.dataset.expiresAt || 0);
      if (expiresAt) setStatus(`Control link expires at ${{new Date(expiresAt * 1000).toLocaleString()}}`);
      const refresh = () => {{ screen.src = `/auth/lease-control/${{token}}/screenshot?ts=${{Date.now()}}`; }};
      const postJson = async (path, body) => {{
        const response = await fetch(path, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: body === undefined ? undefined : JSON.stringify(body)
        }});
        if (!response.ok) throw new Error(await response.text());
        return response;
      }};
      document.getElementById('refresh').addEventListener('click', refresh);
      screen.addEventListener('click', async (event) => {{
        const rect = screen.getBoundingClientRect();
        const x = Math.round((event.clientX - rect.left) * screen.naturalWidth / rect.width);
        const y = Math.round((event.clientY - rect.top) * screen.naturalHeight / rect.height);
        setStatus(`Clicking ${{x}}, ${{y}}...`);
        try {{
          await postJson(`/auth/lease-control/${{token}}/click`, {{x, y}});
          setStatus(`Clicked ${{x}}, ${{y}}`);
          setTimeout(refresh, 700);
        }} catch (error) {{
          setStatus(`Click failed: ${{String(error.message || error).slice(0, 180)}}`);
        }}
      }});
      document.getElementById('typeForm').addEventListener('submit', async (event) => {{
        event.preventDefault();
        const text = document.getElementById('text').value;
        setStatus('Typing...');
        try {{
          await postJson(`/auth/lease-control/${{token}}/keyboard-type`, {{text}});
          setStatus('Typed text into focused field');
          document.getElementById('text').value = '';
          setTimeout(refresh, 700);
        }} catch (error) {{
          setStatus(`Type failed: ${{String(error.message || error).slice(0, 180)}}`);
        }}
      }});
      document.getElementById('pressForm').addEventListener('submit', async (event) => {{
        event.preventDefault();
        const key = document.getElementById('key').value || 'Enter';
        setStatus(`Pressing ${{key}}...`);
        try {{
          await postJson(`/auth/lease-control/${{token}}/keyboard-press`, {{key}});
          setStatus(`Pressed ${{key}}`);
          setTimeout(refresh, 700);
        }} catch (error) {{
          setStatus(`Key failed: ${{String(error.message || error).slice(0, 180)}}`);
        }}
      }});
      document.getElementById('done').addEventListener('click', async () => {{
        try {{
          const response = await fetch(`/auth/lease-control/${{token}}/complete`, {{method: 'POST'}});
          if (!response.ok) throw new Error(await response.text());
          setStatus('Control link ended');
          document.querySelectorAll('button, input').forEach((item) => item.disabled = true);
        }} catch (error) {{
          setStatus(`End failed: ${{String(error.message || error).slice(0, 180)}}`);
        }}
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


def _auth_client_ip(request: Request) -> str:
    if AUTH_TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _auth_client_is_trusted(request: Request) -> bool:
    if not AUTH_TRUSTED_CIDRS:
        return False
    try:
        client_ip = ipaddress.ip_address(_auth_client_ip(request))
    except ValueError:
        return False
    for cidr in AUTH_TRUSTED_CIDRS:
        try:
            if client_ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _novnc_embed_url(vnc: dict[str, Any], passwordless: bool) -> str:
    url = str(vnc["websocket_url"])
    password = str(vnc.get("password", ""))
    parts = urllib.parse.urlsplit(url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if key != "resize"]
    query.append(("resize", "scale"))
    fragment = ""
    if passwordless:
        fragment = urllib.parse.urlencode({"password": password})
    else:
        fragment = parts.fragment
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), fragment))


def _auth_portal_html(
    token: str,
    auth_request_data: dict[str, Any],
    vnc: dict[str, Any] | None,
    *,
    trusted_client: bool,
    client_ip: str,
    start_error: str | None = None,
) -> str:
    safe_token = html.escape(token, quote=True)
    safe_url = html.escape(str(auth_request_data["url"]))
    safe_owner = html.escape(str(auth_request_data["owner"]))
    safe_status = html.escape(str(auth_request_data["status"]))
    safe_identity = html.escape(str(auth_request_data.get("identity_id") or "authenticated-chrome"))
    safe_reason = html.escape(str(auth_request_data.get("reason") or "login_required"))
    safe_client_ip = html.escape(client_ip or "unknown")
    safe_start_error = html.escape(start_error or "")
    mark_svg = """<svg class="brand-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.6 20.4 7.3v9.4L12 21.4l-8.4-4.7V7.3L12 2.6Z"></path><path d="M12 7.2 16.2 9.6v4.8L12 16.8l-4.2-2.4V9.6L12 7.2Z"></path></svg>"""
    browser_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><path d="M3 9h18"></path><path d="M8 15h3"></path><path d="M14 15h2"></path></svg>"""
    status_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2v4"></path><path d="M12 18v4"></path><path d="M4.93 4.93l2.83 2.83"></path><path d="M16.24 16.24l2.83 2.83"></path><path d="M2 12h4"></path><path d="M18 12h4"></path><path d="M4.93 19.07l2.83-2.83"></path><path d="M16.24 7.76l2.83-2.83"></path></svg>"""
    identity_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"></circle><path d="M4 21c1.7-4 14.3-4 16 0"></path></svg>"""
    handoff_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="10" width="16" height="10" rx="2"></rect><path d="M8 10V7a4 4 0 0 1 8 0v3"></path></svg>"""
    network_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><path d="M2 12h20"></path><path d="M12 2a15 15 0 0 1 0 20"></path><path d="M12 2a15 15 0 0 0 0 20"></path></svg>"""
    cdp_svg = """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2-2a5 5 0 0 0-7.07-7.07l-1.2 1.2"></path><path d="M14 11a5 5 0 0 0-7.07 0l-2 2A5 5 0 0 0 12 20.07l1.2-1.2"></path></svg>"""
    frame = ""
    floating_auth = ""
    if vnc:
        embed_url = _novnc_embed_url(vnc, trusted_client)
        safe_embed_url = html.escape(embed_url, quote=True)
        safe_open_url = html.escape(embed_url, quote=True)
        if trusted_client:
            floating_auth = f"""
          <aside class="auth-card is-success" aria-label="Human auth request">
            <div class="auth-logo">{mark_svg}</div>
            <div class="auth-copy">
              <div class="auth-title">Trusted connection</div>
              <div class="auth-subtitle">The browser opens without a temporary VNC password prompt.</div>
            </div>
            <form method="post" action="/auth/{safe_token}/complete" data-async-action="Auth handoff marked complete"><button type="submit">Done</button></form>
          </aside>
"""
        else:
            safe_password = html.escape(str(vnc.get("password", "")))
            floating_auth = f"""
          <aside class="auth-card is-warning" id="authPasswordCard" aria-label="Human auth request">
            <div class="auth-logo">{mark_svg}</div>
            <div class="auth-copy">
              <div class="auth-title">Human auth request</div>
              <div class="auth-subtitle">Temporary VNC password: enter it in the browser prompt, finish login, then mark complete.</div>
              <div class="password-row"><code id="vncPassword">{safe_password}</code><button class="button button-soft button-small" type="button" id="copyPassword">Copy</button></div>
            </div>
          </aside>
          <button class="auth-reopen" type="button" id="showPasswordCard">Show VNC password</button>
"""
        frame = f"""
        <section class="browser-stage">
          <div class="stage-title">
            <span>Live Browser Session</span>
            <a class="button button-outline button-small" href="{safe_open_url}" target="_blank" rel="noopener noreferrer">Open full screen</a>
          </div>
          <div class="browser-shell">
            <div class="browser-toolbar">
              <div class="toolbar-left" aria-hidden="true">
                <span></span><span></span><span></span>
              </div>
              <div class="toolbar-url"><span class="lock">lock</span>{safe_url}</div>
              <a class="icon-button" href="{safe_open_url}" target="_blank" rel="noopener noreferrer" aria-label="Open full screen">Open</a>
            </div>
            <div class="browser-frame">
              <iframe src="{safe_embed_url}" title="OpenBrowser login view" allow="clipboard-read; clipboard-write"></iframe>
            </div>
          </div>
          {floating_auth}
        </section>
"""
    else:
        frame = f"""
        <section class="browser-stage">
          <div class="stage-title"><span>Live Browser Session</span></div>
          <div class="empty-state">
            <div class="empty-icon">{mark_svg}</div>
            <div>
              <div class="title-small">Browser login view is not running</div>
              <p>{safe_start_error or "Start it below, then sign in inside the browser view."}</p>
            </div>
            <form method="post" action="/auth/{safe_token}/start-vnc"><button type="submit">Start browser login view</button></form>
          </div>
        </section>
"""
    return f"""
<!doctype html>
<html data-theme="light">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OpenBrowser Login Handoff</title>
    <script>
      (() => {{
        try {{
          const saved = localStorage.getItem('openbrowser-theme');
          const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
          document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
        }} catch (error) {{
          document.documentElement.dataset.theme = 'light';
        }}
      }})();
    </script>
    <style>
      :root {{
        color-scheme: light dark;
        --page: #e7dfd0;
        --paper: rgba(255,255,255,0.88);
        --panel: rgba(255,255,255,0.74);
        --panel-solid: #ffffff;
        --soft: #f5f2ec;
        --text: #1e1d1a;
        --muted: #807a70;
        --faint: #a8a196;
        --border: rgba(58,48,38,0.12);
        --border-strong: rgba(58,48,38,0.18);
        --primary: #24231f;
        --primary-text: #ffffff;
        --green: #47b274;
        --amber: #ee9c44;
        --red: #ec6a5f;
        --blue: #4f78d9;
        --radius-lg: 20px;
        --radius-md: 14px;
        --radius-sm: 10px;
        --radius-pill: 9999px;
        --shadow-window: 0 24px 80px rgba(33, 26, 17, 0.18), 0 1px 0 rgba(255,255,255,0.78) inset;
        --shadow-float: 0 24px 64px rgba(33, 26, 17, 0.20), 0 0 0 1px var(--border);
        --ease: cubic-bezier(0.22, 1, 0.36, 1);
      }}
      [data-theme="dark"] {{
        --page: #0f1211;
        --paper: rgba(25,25,24,0.92);
        --panel: rgba(33,33,31,0.76);
        --panel-solid: #21211f;
        --soft: #2b2a27;
        --text: #f4f1ea;
        --muted: #aaa49a;
        --faint: #746f68;
        --border: rgba(255,255,255,0.10);
        --border-strong: rgba(255,255,255,0.18);
        --primary: #f4f1ea;
        --primary-text: #191918;
        --green: #59c889;
        --amber: #f2b15d;
        --red: #f07b70;
        --blue: #7fa0ff;
        --shadow-window: 0 28px 90px rgba(0,0,0,0.48), 0 1px 0 rgba(255,255,255,0.08) inset;
        --shadow-float: 0 28px 70px rgba(0,0,0,0.42), 0 0 0 1px var(--border);
      }}
      * {{ box-sizing: border-box; }}
      html {{ min-height: 100%; background: var(--page); }}
      body {{
        margin: 0;
        min-height: 100dvh;
        overflow: hidden;
        display: grid;
        place-items: center;
        padding: 28px;
        color: var(--text);
        background:
          linear-gradient(180deg, rgba(247,244,236,0.58) 0%, rgba(231,223,208,0.70) 44%, rgba(231,223,208,0.88) 100%),
          url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover no-repeat;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-feature-settings: "cv11" 1, "ss01" 1, "calt" 1;
      }}
      [data-theme="dark"] body {{
        background:
          linear-gradient(180deg, rgba(12,15,14,0.72) 0%, rgba(12,15,14,0.84) 56%, rgba(12,15,14,0.92) 100%),
          url('https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=2400&q=80') center / cover no-repeat;
      }}
      button, .button {{
        appearance: none;
        display: inline-flex;
        min-height: 44px;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid color-mix(in srgb, var(--primary) 82%, transparent);
        border-radius: var(--radius-sm);
        background: var(--primary);
        color: var(--primary-text);
        font-weight: 650;
        font-size: 13px;
        line-height: 1;
        padding: 0 14px;
        text-decoration: none;
        cursor: pointer;
        box-shadow: 0 1px 0 rgba(0,0,0,0.08);
        transition: transform 120ms var(--ease), background-color 150ms var(--ease), border-color 150ms var(--ease), box-shadow 150ms var(--ease);
        white-space: nowrap;
      }}
      button:hover, .button:hover {{ box-shadow: 0 10px 24px rgba(0,0,0,0.12); }}
      button:active, .button:active {{ transform: translateY(1px) scale(.985); }}
      .button-outline, .button-soft {{
        border-color: var(--border);
        background: color-mix(in srgb, var(--panel-solid) 86%, transparent);
        color: var(--text);
      }}
      .button-outline:hover, .button-soft:hover {{ border-color: var(--border-strong); background: var(--soft); }}
      .button-small {{ min-height: 36px; padding: 0 12px; font-size: 12px; }}
      .app-window {{
        width: min(1680px, calc(100vw - 56px));
        height: min(940px, calc(100dvh - 56px));
        min-height: 680px;
        overflow: hidden;
        display: grid;
        grid-template-rows: 86px minmax(0, 1fr);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        background: var(--paper);
        box-shadow: var(--shadow-window);
        backdrop-filter: blur(22px) saturate(1.15);
      }}
      .window-bar {{
        display: grid;
        grid-template-columns: 220px minmax(0, 1fr) 220px;
        align-items: center;
        gap: 16px;
        padding: 18px 22px;
        border-bottom: 1px solid var(--border);
      }}
      .traffic-lights {{ display: flex; gap: 8px; align-items: center; }}
      .traffic-lights span {{ width: 13px; height: 13px; border-radius: var(--radius-pill); background: color-mix(in srgb, var(--faint) 45%, transparent); border: 1px solid var(--border); }}
      .traffic-lights span:nth-child(1) {{ background: color-mix(in srgb, var(--red) 62%, transparent); }}
      .traffic-lights span:nth-child(2) {{ background: color-mix(in srgb, var(--amber) 62%, transparent); }}
      .traffic-lights span:nth-child(3) {{ background: color-mix(in srgb, var(--green) 62%, transparent); }}
      .brand-block {{ min-width: 0; text-align: center; }}
      .brand-title {{ display: inline-flex; align-items: center; justify-content: center; gap: 10px; font-size: 21px; font-weight: 760; letter-spacing: 0; }}
      .brand-title .brand-icon {{ width: 26px; height: 26px; color: currentColor; }}
      .brand-subtitle {{ margin-top: 5px; color: var(--muted); font-size: 14px; font-weight: 560; }}
      .top-actions {{ display: flex; justify-content: flex-end; gap: 10px; align-items: center; }}
      .api-link {{ color: var(--muted); font-size: 13px; font-weight: 700; text-decoration: none; }}
      .app-grid {{
        min-height: 0;
        display: grid;
        grid-template-columns: 300px minmax(0, 1fr) 300px;
      }}
      .sidebar, .state-panel {{
        min-width: 0;
        padding: 26px 22px;
        background: color-mix(in srgb, var(--panel) 92%, transparent);
      }}
      .sidebar {{ border-right: 1px solid var(--border); }}
      .state-panel {{ border-left: 1px solid var(--border); }}
      .panel-title {{ margin-bottom: 16px; color: var(--muted); font-size: 14px; font-weight: 760; }}
      .session-list {{ display: grid; gap: 14px; }}
      .session-card {{
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr) auto;
        gap: 12px;
        align-items: center;
        padding: 16px 14px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 72%, transparent);
      }}
      .session-card.is-active {{ background: color-mix(in srgb, var(--panel-solid) 92%, transparent); box-shadow: 0 10px 28px rgba(0,0,0,0.045); }}
      .session-icon, .state-icon, .empty-icon, .auth-logo {{
        display: grid;
        place-items: center;
        border-radius: var(--radius-pill);
        background: var(--soft);
        border: 1px solid var(--border);
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
      }}
      .session-icon svg, .state-icon svg, .empty-icon svg, .auth-logo svg {{
        width: 19px;
        height: 19px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.9;
        stroke-linecap: round;
        stroke-linejoin: round;
      }}
      .empty-icon svg, .auth-logo svg {{ width: 25px; height: 25px; }}
      .session-icon {{ width: 38px; height: 38px; }}
      .session-name {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 16px; font-weight: 760; }}
      .session-status {{ margin-top: 4px; color: var(--green); font-size: 13px; font-weight: 650; }}
      .kebab {{ color: var(--faint); font-size: 24px; line-height: 1; }}
      .request-card {{
        margin-top: 18px;
        display: grid;
        gap: 13px;
        padding: 15px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 66%, transparent);
      }}
      .request-row {{ min-width: 0; display: grid; gap: 4px; }}
      .label {{ color: var(--muted); font-size: 12px; font-weight: 760; }}
      .value {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; }}
      .value.mono {{ font-family: "SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; }}
      .actions {{ display: grid; gap: 9px; }}
      .actions form, .actions button {{ width: 100%; }}
      .actions button {{ width: 100%; }}
      .browser-stage {{
        position: relative;
        min-width: 0;
        min-height: 0;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
        gap: 18px;
        padding: 26px 26px 22px;
      }}
      .stage-title {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 14px; font-weight: 760; }}
      .browser-shell {{
        min-width: 0;
        min-height: 0;
        overflow: hidden;
        display: grid;
        grid-template-rows: 58px minmax(0, 1fr);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: var(--panel-solid);
        box-shadow: 0 14px 42px rgba(0,0,0,0.055);
      }}
      .browser-toolbar {{
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 14px;
        padding: 12px 14px;
        border-bottom: 1px solid var(--border);
        background: color-mix(in srgb, var(--panel-solid) 88%, transparent);
      }}
      .toolbar-left {{ display: flex; gap: 8px; }}
      .toolbar-left span {{ width: 14px; height: 14px; border-radius: var(--radius-pill); background: var(--soft); border: 1px solid var(--border); }}
      .toolbar-url {{
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        height: 34px;
        display: flex;
        align-items: center;
        gap: 8px;
        border: 1px solid var(--border);
        border-radius: var(--radius-pill);
        padding: 0 14px;
        color: var(--muted);
        background: var(--soft);
        font-size: 13px;
        font-weight: 650;
      }}
      .lock {{ color: var(--green); font-size: 11px; text-transform: uppercase; }}
      .icon-button {{
        width: 44px;
        height: 44px;
        display: grid;
        place-items: center;
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        color: var(--muted);
        text-decoration: none;
        background: color-mix(in srgb, var(--panel-solid) 70%, transparent);
      }}
      .browser-frame {{ min-height: 0; background: #111; }}
      iframe {{ width: 100%; height: 100%; min-height: 0; display: block; border: 0; background: white; }}
      .auth-card {{
        position: absolute;
        right: 28px;
        bottom: 32px;
        width: min(420px, calc(100% - 56px));
        display: grid;
        grid-template-columns: 50px minmax(0, 1fr);
        gap: 16px;
        align-items: center;
        padding: 24px;
        border: 1px solid var(--border);
        border-radius: 18px;
        background: color-mix(in srgb, var(--panel-solid) 94%, transparent);
        box-shadow: var(--shadow-float);
        backdrop-filter: blur(18px) saturate(1.12);
      }}
      .auth-card.is-hidden {{ display: none; }}
      .auth-card form {{ grid-column: 2; }}
      .auth-card button[type="submit"] {{ width: 100%; }}
      .auth-reopen {{
        position: absolute;
        right: 28px;
        bottom: 28px;
        display: none;
        width: auto;
        padding: 9px 12px;
        border: 1px solid var(--border);
        border-radius: var(--radius-pill);
        background: color-mix(in srgb, var(--panel-solid) 92%, transparent);
        color: var(--text);
        box-shadow: var(--shadow-soft);
        backdrop-filter: blur(14px);
      }}
      .auth-reopen.is-visible {{ display: inline-flex; }}
      .auth-logo {{
        width: 48px;
        height: 48px;
        color: var(--blue);
        background: color-mix(in srgb, var(--blue) 10%, var(--panel-solid));
      }}
      .auth-title {{ font-size: 18px; line-height: 1.2; font-weight: 760; }}
      .auth-subtitle {{ margin-top: 5px; color: var(--muted); font-size: 14px; font-weight: 560; }}
      .password-row {{ margin-top: 12px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
      code {{
        max-width: 180px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        border: 1px solid var(--border);
        border-radius: 9px;
        padding: 6px 8px;
        background: var(--soft);
        color: var(--text);
        font-family: "SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace;
        font-size: 12px;
      }}
      .state-list {{ display: grid; gap: 22px; }}
      .state-item {{ display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 13px; align-items: center; }}
      .state-icon {{ width: 36px; height: 36px; }}
      .state-title {{ font-size: 15px; font-weight: 760; }}
      .state-subtitle {{ margin-top: 3px; color: var(--muted); font-size: 13px; font-weight: 560; }}
      .state-dot {{ width: 7px; height: 7px; border-radius: var(--radius-pill); background: var(--green); }}
      .empty-state {{
        min-height: 0;
        display: grid;
        place-items: center;
        align-content: center;
        gap: 16px;
        padding: 32px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        background: color-mix(in srgb, var(--panel-solid) 82%, transparent);
        text-align: center;
      }}
      .empty-icon {{ width: 56px; height: 56px; color: var(--blue); }}
      .title-small {{ font-size: 17px; font-weight: 760; }}
      .empty-state p {{ max-width: 420px; margin: 8px auto 0; color: var(--muted); line-height: 1.45; }}
      @media (max-width: 900px) {{
        body {{ overflow: auto; padding: 12px; place-items: start center; }}
        .app-window {{
          width: 100%;
          height: auto;
          min-height: calc(100dvh - 24px);
          grid-template-rows: auto auto;
          border-radius: 16px;
        }}
        .window-bar {{ grid-template-columns: 1fr; justify-items: start; padding: 16px; }}
        .brand-block {{ text-align: left; }}
        .top-actions {{ width: 100%; justify-content: space-between; }}
        .app-grid {{ grid-template-columns: 1fr; }}
        .sidebar, .state-panel {{ border: 0; padding: 18px 16px; }}
        .sidebar {{ border-bottom: 1px solid var(--border); }}
        .state-panel {{ border-top: 1px solid var(--border); }}
        .browser-stage {{ min-height: 72dvh; padding: 18px 16px; }}
        .stage-title {{ align-items: flex-start; }}
        .browser-shell {{ min-height: 58dvh; grid-template-rows: auto minmax(0, 1fr); }}
        .browser-toolbar {{ grid-template-columns: minmax(0, 1fr) auto; }}
        .toolbar-left {{ display: none; }}
        .auth-card {{ position: static; width: 100%; margin-top: 14px; grid-template-columns: 42px minmax(0, 1fr); padding: 18px; }}
        .auth-reopen {{ position: static; justify-self: end; margin-top: 12px; }}
        .auth-logo {{ width: 44px; height: 44px; }}
        .auth-card form {{ grid-column: 1 / -1; }}
      }}
    </style>
  </head>
  <body>
    <div class="app-window">
      <header class="window-bar">
        <div class="traffic-lights" aria-hidden="true"><span></span><span></span><span></span></div>
        <div class="brand-block">
          <div class="brand-title">{mark_svg}<span>OpenBrowser</span></div>
          <div class="brand-subtitle">The browser API for AI agents</div>
        </div>
        <div class="top-actions">
          <button class="button-soft button-small" type="button" id="themeToggle" aria-label="Toggle day and night mode">Theme</button>
          <a class="api-link" href="/docs" target="_blank" rel="noopener noreferrer">API</a>
        </div>
      </header>
      <main class="app-grid">
        <aside class="sidebar">
          <div class="panel-title">Browser Sessions</div>
          <div class="session-list">
            <div class="session-card is-active">
              <div class="session-icon">{browser_svg}</div>
              <div>
                <div class="session-name">{safe_identity}</div>
                <div class="session-status">{safe_status}</div>
              </div>
              <div class="kebab" aria-hidden="true">...</div>
            </div>
          </div>
          <section class="request-card" aria-label="Handoff details">
            <div class="request-row"><span class="label">Target</span><span class="value mono" title="{safe_url}">{safe_url}</span></div>
            <div class="request-row"><span class="label">Agent</span><span class="value">{safe_owner}</span></div>
            <div class="request-row"><span class="label">Reason</span><span class="value">{safe_reason}</span></div>
            <div class="actions">
              <form method="post" action="/auth/{safe_token}/complete" data-async-action="Auth handoff marked complete"><button type="submit">Mark complete</button></form>
              <form method="post" action="/auth/{safe_token}/stop-vnc" data-async-action="Browser login view stopped"><button class="button-outline" type="submit">Stop view</button></form>
            </div>
            <div id="portalStatus" class="auth-subtitle" aria-live="polite">Complete the login in the browser view, then mark this request complete.</div>
          </section>
        </aside>
        {frame}
        <aside class="state-panel">
          <div class="panel-title">Session State</div>
          <div class="state-list">
            <div class="state-item"><div class="state-icon">{status_svg}</div><div><div class="state-title">Status: {safe_status}</div><div class="state-subtitle">Agent handoff active</div></div><span class="state-dot"></span></div>
            <div class="state-item"><div class="state-icon">{identity_svg}</div><div><div class="state-title">Profile: {safe_identity}</div><div class="state-subtitle">Persistent broker identity</div></div><span class="state-dot"></span></div>
            <div class="state-item"><div class="state-icon">{handoff_svg}</div><div><div class="state-title">Human handoff ready</div><div class="state-subtitle">Enabled</div></div><span class="state-dot"></span></div>
            <div class="state-item"><div class="state-icon">{network_svg}</div><div><div class="state-title">Client: {safe_client_ip}</div><div class="state-subtitle">Trusted IPs skip password prompts</div></div><span class="state-dot"></span></div>
            <div class="state-item"><div class="state-icon">{cdp_svg}</div><div><div class="state-title">noVNC scaling</div><div class="state-subtitle">resize=scale active</div></div><span class="state-dot"></span></div>
          </div>
        </aside>
      </main>
    </div>
    <script>
      const themeButton = document.getElementById('themeToggle');
      const setThemeButton = () => {{
        const dark = document.documentElement.dataset.theme === 'dark';
        themeButton.textContent = dark ? 'Day mode' : 'Night mode';
      }};
      if (themeButton) {{
        setThemeButton();
        themeButton.addEventListener('click', () => {{
          const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
          document.documentElement.dataset.theme = next;
          localStorage.setItem('openbrowser-theme', next);
          setThemeButton();
        }});
      }}
      const copyButton = document.getElementById('copyPassword');
      const authPasswordCard = document.getElementById('authPasswordCard');
      const showPasswordCard = document.getElementById('showPasswordCard');
      if (copyButton) {{
        copyButton.addEventListener('click', async () => {{
          const value = document.getElementById('vncPassword').textContent;
          await navigator.clipboard.writeText(value);
          copyButton.textContent = 'Copied';
          setTimeout(() => {{
            authPasswordCard?.classList.add('is-hidden');
            showPasswordCard?.classList.add('is-visible');
            copyButton.textContent = 'Copy';
          }}, 250);
        }});
      }}
      showPasswordCard?.addEventListener('click', () => {{
        authPasswordCard?.classList.remove('is-hidden');
        showPasswordCard.classList.remove('is-visible');
      }});
      const portalStatus = document.getElementById('portalStatus');
      document.querySelectorAll('form[data-async-action]').forEach((form) => {{
        form.addEventListener('submit', async (event) => {{
          event.preventDefault();
          const button = form.querySelector('button[type="submit"]');
          const oldText = button ? button.textContent : '';
          if (button) {{
            button.disabled = true;
            button.textContent = 'Working...';
          }}
          if (portalStatus) portalStatus.textContent = 'Updating handoff state...';
          try {{
            const response = await fetch(form.action, {{ method: 'POST' }});
            if (!response.ok) throw new Error(await response.text());
            if (portalStatus) portalStatus.textContent = form.dataset.asyncAction || 'Done';
            if (form.action.endsWith('/complete')) {{
              document.querySelectorAll('form[data-async-action] button').forEach((item) => item.disabled = true);
            }} else if (button) {{
              button.disabled = false;
              button.textContent = oldText;
            }}
          }} catch (error) {{
            if (portalStatus) portalStatus.textContent = `Action failed: ${{String(error.message || error).slice(0, 180)}}`;
            if (button) {{
              button.disabled = false;
              button.textContent = oldText;
            }}
          }}
        }});
      }});
    </script>
  </body>
</html>
"""


@app.get("/auth/{token}", response_class=HTMLResponse, response_model=None)
async def auth_portal(token: str, request: Request) -> Any:
    try:
        auth_request_data = get_pending_auth_request(token)
    except AuthError as error:
        raise HTTPException(status_code=410 if "expired" in str(error) or "is expired" in str(error) else 404, detail=str(error)) from error
    vnc = current_auth_vnc(token)
    start_error = None
    if vnc is None and AUTH_PORTAL_AUTOSTART:
        try:
            vnc = start_auth_vnc(token)
        except AuthError as error:
            redirect = _active_identity_control_redirect(auth_request_data, error)
            if redirect:
                return redirect
            start_error = str(error)
    return _auth_portal_html(
        token,
        auth_request_data,
        vnc,
        trusted_client=_auth_client_is_trusted(request),
        client_ip=_auth_client_ip(request),
        start_error=start_error,
    )


@app.post("/auth/{token}/start-vnc", response_class=HTMLResponse, response_model=None)
async def auth_start_vnc(token: str, request: Request) -> Any:
    try:
        vnc = start_auth_vnc(token)
        auth_request_data = get_pending_auth_request(token)
    except AuthError as error:
        try:
            auth_request_data = get_pending_auth_request(token)
        except AuthError:
            auth_request_data = {}
        redirect = _active_identity_control_redirect(auth_request_data, error)
        if redirect:
            return redirect
        raise HTTPException(status_code=410 if "expired" in str(error) or "is expired" in str(error) else 400, detail=str(error)) from error
    return _auth_portal_html(
        token,
        auth_request_data,
        vnc,
        trusted_client=_auth_client_is_trusted(request),
        client_ip=_auth_client_ip(request),
    )


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
        raise HTTPException(status_code=410 if "expired" in str(error) or "is expired" in str(error) else 404, detail=str(error)) from error


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
