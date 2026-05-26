from __future__ import annotations

import asyncio
import base64

from ax_browser_broker.browser import BrowserController
from ax_browser_broker.pool import Lease


def make_lease() -> Lease:
    return Lease(
        lease_id="lease-test",
        name="pool-b",
        port=9224,
        owner="pytest",
        created_at=1,
        heartbeat_at=1,
        expires_at=2,
        cdp="http://127.0.0.1:9224",
        profile_dir="/tmp/profile",
    )


class FakeKeyboard:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events

    async def press(self, key: str) -> None:
        self.events.append(("keyboard.press", key))

    async def type(self, text: str, delay: int = 0) -> None:
        self.events.append(("keyboard.type", text, delay))


class FakeMouse:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events

    async def click(self, x: int, y: int) -> None:
        self.events.append(("mouse.click", x, y))


class FakeLocator:
    def __init__(self, events: list[tuple], rich_text: bool) -> None:
        self.events = events
        self.rich_text = rich_text

    async def evaluate(self, _script: str) -> bool:
        self.events.append(("locator.evaluate",))
        return self.rich_text

    async def click(self, timeout: int = 0) -> None:
        self.events.append(("locator.click", timeout))


class FakePage:
    url = "https://example.com/editor"

    def __init__(self, rich_text: bool = False) -> None:
        self.events: list[tuple] = []
        self.keyboard = FakeKeyboard(self.events)
        self.mouse = FakeMouse(self.events)
        self.rich_text = rich_text

    def locator(self, selector: str) -> FakeLocator:
        self.events.append(("locator", selector))
        return FakeLocator(self.events, self.rich_text)

    async def fill(self, selector: str, text: str, timeout: int = 0) -> None:
        self.events.append(("fill", selector, text, timeout))

    async def press(self, selector: str, key: str) -> None:
        self.events.append(("press", selector, key))


def test_type_text_uses_keyboard_events_for_rich_text_submit() -> None:
    controller = BrowserController()
    page = FakePage(rich_text=True)

    async def fake_page(_lease):
        return page

    controller.page = fake_page

    result = asyncio.run(controller.type_text(make_lease(), "#editor", "hello", submit=True))

    assert result["keyboard"] is True
    assert result["submitted"] is True
    assert ("fill", "#editor", "hello", 10000) not in page.events
    assert page.events == [
        ("locator", "#editor"),
        ("locator.evaluate",),
        ("locator", "#editor"),
        ("locator.click", 10000),
        ("keyboard.press", "Control+A"),
        ("keyboard.press", "Backspace"),
        ("keyboard.type", "hello", 0),
        ("keyboard.press", "Enter"),
    ]


def test_type_text_keeps_fill_path_for_plain_inputs() -> None:
    controller = BrowserController()
    page = FakePage(rich_text=False)

    async def fake_page(_lease):
        return page

    controller.page = fake_page

    result = asyncio.run(controller.type_text(make_lease(), "input[name=q]", "hello", submit=True))

    assert result["keyboard"] is False
    assert page.events == [
        ("locator", "input[name=q]"),
        ("locator.evaluate",),
        ("fill", "input[name=q]", "hello", 10000),
        ("press", "input[name=q]", "Enter"),
    ]


def test_keyboard_type_and_press_use_page_keyboard() -> None:
    controller = BrowserController()
    page = FakePage(rich_text=True)

    async def fake_page(_lease):
        return page

    controller.page = fake_page
    lease = make_lease()

    typed = asyncio.run(controller.keyboard_type(lease, "hello", "#editor", delay_ms=25))
    pressed = asyncio.run(controller.keyboard_press(lease, "Enter", "#editor"))

    assert typed["text_length"] == 5
    assert typed["delay_ms"] == 25
    assert pressed["pressed"] == "Enter"
    assert page.events == [
        ("locator", "#editor"),
        ("locator.click", 10000),
        ("keyboard.type", "hello", 25),
        ("locator", "#editor"),
        ("locator.click", 10000),
        ("keyboard.press", "Enter"),
    ]


def test_mouse_click_uses_page_mouse_coordinates() -> None:
    controller = BrowserController()
    page = FakePage(rich_text=False)

    async def fake_page(_lease):
        return page

    controller.page = fake_page
    lease = make_lease()

    result = asyncio.run(controller.mouse_click(lease, 123, 45))

    assert result["clicked"] == {"x": 123, "y": 45}
    assert page.events == [("mouse.click", 123, 45)]


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
    lease = make_lease()

    result = asyncio.run(controller.screenshot(lease))

    assert result["fallback"] is True
    assert result["base64"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert (tmp_path / result["path"].split("/")[-1]).read_bytes() == b"png-bytes"
