from __future__ import annotations

from ax_browser_broker.identities import ProxyCredential
from ax_browser_broker import proxy_forwarder
from ax_browser_broker.proxy_forwarder import _inject_proxy_auth, _proxy_authorization


def test_proxy_authorization_header_is_basic() -> None:
    proxy = ProxyCredential(
        ref="iproyal:test",
        scheme="http",
        host="proxy.example",
        port=1234,
        username="user",
        password="pass",
    )

    assert _proxy_authorization(proxy) == b"Proxy-Authorization: Basic dXNlcjpwYXNz\r\n"


def test_inject_proxy_auth_after_request_line() -> None:
    headers = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"

    out = _inject_proxy_auth(headers, b"Proxy-Authorization: Basic token\r\n")

    assert out.startswith(b"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic token\r\n")
    assert out.endswith(b"Host: example.com:443\r\n\r\n")


def test_relay_treats_connection_reset_as_closed(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self, reset: bool = False) -> None:
            self.reset = reset
            self.sent = []

        def recv(self, _size: int) -> bytes:
            if self.reset:
                raise ConnectionResetError("client closed")
            return b""

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    left = FakeSocket(reset=True)
    right = FakeSocket()
    monkeypatch.setattr(proxy_forwarder.select, "select", lambda *_args: ([left], [], []))

    proxy_forwarder._relay(left, right)

    assert right.sent == []
