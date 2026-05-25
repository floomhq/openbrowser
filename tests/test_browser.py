from __future__ import annotations

import asyncio
import base64

from ax_browser_broker.browser import BrowserController
from ax_browser_broker.pool import Lease


def test_screenshot_falls_back_to_cdp_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("ax_browser_broker.browser.SCREENSHOT_DIR", tmp_path)

    class FakePage:
        url = "https://example.com"

        async def screenshot(self, **_kwargs):
            raise RuntimeError("screenshot timeout")

    controller = BrowserController()

    async def fake_page(_lease):
        return FakePage()

    controller.page = fake_page

    async def fake_capture(_lease, _page, path, _full_page):
        path.write_bytes(b"png-bytes")
        return b"png-bytes"

    controller._capture_screenshot_cdp = fake_capture
    lease = Lease(
        lease_id="lease-shot",
        name="pool-b",
        port=9224,
        owner="pytest",
        created_at=1,
        heartbeat_at=1,
        expires_at=2,
        cdp="http://127.0.0.1:9224",
        profile_dir="/tmp/profile",
    )

    result = asyncio.run(controller.screenshot(lease))

    assert result["fallback"] is True
    assert result["base64"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert (tmp_path / result["path"].split("/")[-1]).read_bytes() == b"png-bytes"
