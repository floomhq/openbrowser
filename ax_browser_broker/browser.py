from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, async_playwright
import websockets

from .config import MAX_SNAPSHOT_CHARS, SCREENSHOT_DIR, ensure_dirs
from .pool import Lease


class BrowserController:
    def __init__(self) -> None:
        self._playwright: Any = None
        self._browsers: dict[int, Browser] = {}
        self._active_pages: dict[str, Page] = {}

    async def start(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def stop(self) -> None:
        self._active_pages.clear()
        self._browsers.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def browser(self, lease: Lease) -> Browser:
        await self.start()
        existing = self._browsers.get(lease.port)
        if existing and existing.is_connected():
            return existing
        browser = await self._playwright.chromium.connect_over_cdp(lease.cdp)
        self._browsers[lease.port] = browser
        return browser

    async def page(self, lease: Lease) -> Page:
        active = self._active_pages.get(lease.lease_id)
        if active and not active.is_closed():
            return active
        browser = await self.browser(lease)
        if not browser.contexts:
            raise RuntimeError("No browser context available")
        context = browser.contexts[0]
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        self._active_pages[lease.lease_id] = page
        return page

    async def navigate(self, lease: Lease, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        page = await self.page(lease)
        response = await page.goto(url, wait_until=wait_until, timeout=30000)
        return {
            "lease_id": lease.lease_id,
            "slot": lease.name,
            "url": page.url,
            "title": await page.title(),
            "status": response.status if response else None,
        }

    async def snapshot(self, lease: Lease) -> dict[str, Any]:
        page = await self.page(lease)
        data = await page.evaluate(
            """(maxChars) => {
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const box = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
              };
              const label = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
              const elements = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"],[aria-label]'))
                .filter(visible)
                .slice(0, 250)
                .map((el, index) => ({
                  index,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || '',
                  type: el.getAttribute('type') || '',
                  name: label(el).slice(0, 200),
                  href: el.href || '',
                  selector: el.id ? `#${CSS.escape(el.id)}` : ''
                }));
              const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, maxChars);
              return { title: document.title, url: location.href, bodyText, elements };
            }""",
            MAX_SNAPSHOT_CHARS,
        )
        data["lease_id"] = lease.lease_id
        data["slot"] = lease.name
        return data

    async def screenshot(self, lease: Lease, full_page: bool = False) -> dict[str, Any]:
        ensure_dirs()
        page = await self.page(lease)
        stamp = int(time.time() * 1000)
        path = SCREENSHOT_DIR / f"{lease.name}-{stamp}.png"
        fallback = False
        try:
            data = await asyncio.wait_for(
                page.screenshot(path=str(path), full_page=full_page, type="png", timeout=8000),
                timeout=12,
            )
        except Exception:
            data = await asyncio.wait_for(self._capture_screenshot_cdp(lease, page, path, full_page), timeout=12)
            fallback = True
        return {
            "lease_id": lease.lease_id,
            "slot": lease.name,
            "path": str(path),
            "mime_type": "image/png",
            "base64": base64.b64encode(data).decode("ascii"),
            "fallback": fallback,
        }

    async def _capture_screenshot_cdp(self, lease: Lease, page: Page, path: Path, full_page: bool) -> bytes:
        page_url = page.url

        def load_tabs() -> list[dict[str, Any]]:
            with urllib.request.urlopen(f"http://127.0.0.1:{lease.port}/json/list", timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        tabs = await asyncio.to_thread(load_tabs)
        pages = [tab for tab in tabs if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")]
        if not pages:
            raise RuntimeError(f"No page CDP target available on {lease.name}")
        target = next((tab for tab in pages if tab.get("url") == page_url), pages[0])
        ws_url = str(target["webSocketDebuggerUrl"])
        async with websockets.connect(ws_url, open_timeout=5, close_timeout=1, max_size=16 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Page.captureScreenshot",
                        "params": {
                            "format": "png",
                            "fromSurface": True,
                            "captureBeyondViewport": bool(full_page),
                        },
                    }
                )
            )
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                message = json.loads(raw)
                if message.get("id") != 1:
                    continue
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                data = base64.b64decode(str(message["result"]["data"]))
                path.write_bytes(data)
                return data

    async def click(self, lease: Lease, selector: str) -> dict[str, Any]:
        page = await self.page(lease)
        await page.click(selector, timeout=10000)
        return {"lease_id": lease.lease_id, "slot": lease.name, "clicked": selector, "url": page.url}

    async def type_text(self, lease: Lease, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        page = await self.page(lease)
        await page.fill(selector, text, timeout=10000)
        if submit:
            await page.press(selector, "Enter")
        return {"lease_id": lease.lease_id, "slot": lease.name, "typed": selector, "submitted": submit}

    async def wait(self, lease: Lease, selector: str | None = None, timeout_ms: int = 1000) -> dict[str, Any]:
        page = await self.page(lease)
        bounded = max(1, min(int(timeout_ms), 30000))
        if selector:
            await page.wait_for_selector(selector, timeout=bounded)
            return {"lease_id": lease.lease_id, "slot": lease.name, "found": selector}
        await page.wait_for_timeout(bounded)
        return {"lease_id": lease.lease_id, "slot": lease.name, "waited_ms": bounded}

    async def tabs(self, lease: Lease) -> dict[str, Any]:
        browser = await self.browser(lease)
        if not browser.contexts:
            return {"lease_id": lease.lease_id, "slot": lease.name, "tabs": []}
        pages = browser.contexts[0].pages
        active = self._active_pages.get(lease.lease_id)
        tabs = []
        for index, page in enumerate(pages):
            tabs.append(
                {
                    "index": index,
                    "url": page.url,
                    "title": await page.title(),
                    "active": page == active,
                }
            )
        return {"lease_id": lease.lease_id, "slot": lease.name, "tabs": tabs}

    async def new_tab(self, lease: Lease, url: str | None = None) -> dict[str, Any]:
        browser = await self.browser(lease)
        if not browser.contexts:
            raise RuntimeError("No browser context available")
        page = await browser.contexts[0].new_page()
        self._active_pages[lease.lease_id] = page
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"lease_id": lease.lease_id, "slot": lease.name, "url": page.url, "title": await page.title()}

    async def switch_tab(self, lease: Lease, index: int) -> dict[str, Any]:
        browser = await self.browser(lease)
        if not browser.contexts:
            raise RuntimeError("No browser context available")
        pages = browser.contexts[0].pages
        if index < 0 or index >= len(pages):
            raise RuntimeError(f"Tab index {index} out of range")
        page = pages[index]
        await page.bring_to_front()
        self._active_pages[lease.lease_id] = page
        return {"lease_id": lease.lease_id, "slot": lease.name, "index": index, "url": page.url, "title": await page.title()}

    async def upload(self, lease: Lease, selector: str, path: str) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"Upload file not found: {path}")
        page = await self.page(lease)
        await page.set_input_files(selector, str(file_path), timeout=10000)
        return {"lease_id": lease.lease_id, "slot": lease.name, "uploaded": str(file_path), "selector": selector}


controller = BrowserController()
