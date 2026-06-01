from __future__ import annotations

import argparse
import base64
import select
import socket
import socketserver
from typing import Any

from .identities import ProxyCredential, require_proxy


BUFFER_SIZE = 65536


class ProxyForwarderError(RuntimeError):
    pass


def _proxy_authorization(proxy: ProxyCredential) -> bytes | None:
    if not proxy.username:
        return None
    token = proxy.username
    if proxy.password:
        token += ":" + proxy.password
    encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {encoded}\r\n".encode("ascii")


def _read_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            raise ProxyForwarderError("HTTP headers exceeded 1 MiB")
    return data


def _inject_proxy_auth(headers: bytes, auth: bytes | None) -> bytes:
    if not auth or b"\r\nProxy-Authorization:" in headers or b"\r\nproxy-authorization:" in headers:
        return headers
    marker = b"\r\n"
    first_line, rest = headers.split(marker, 1)
    return first_line + marker + auth + rest


def _relay(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    while True:
        readable, _, exceptional = select.select(sockets, [], sockets, 60)
        if exceptional:
            return
        if not readable:
            return
        for source in readable:
            target = right if source is left else left
            try:
                data = source.recv(BUFFER_SIZE)
                if not data:
                    return
                target.sendall(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler: type[socketserver.BaseRequestHandler], proxy: ProxyCredential):
        self.proxy = proxy
        super().__init__(server_address, handler)


class ForwardingHandler(socketserver.BaseRequestHandler):
    server: ThreadedTCPServer

    def handle(self) -> None:
        client = self.request
        client.settimeout(90)
        headers = _read_headers(client)
        if not headers:
            return
        proxy = self.server.proxy
        auth = _proxy_authorization(proxy)
        with socket.create_connection((proxy.host, proxy.port), timeout=30) as upstream:
            upstream.settimeout(90)
            upstream.sendall(_inject_proxy_auth(headers, auth))
            _relay(client, upstream)


def serve(proxy_ref: str, listen_host: str, listen_port: int) -> None:
    proxy = require_proxy(proxy_ref)
    if proxy.scheme != "http":
        raise ProxyForwarderError(f"Only HTTP upstream proxies are supported, got {proxy.scheme!r}")
    with ThreadedTCPServer((listen_host, listen_port), ForwardingHandler, proxy) as server:
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local authenticated HTTP proxy forwarder")
    parser.add_argument("--proxy-ref", required=True)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    args = parser.parse_args(argv)
    serve(args.proxy_ref, args.listen_host, args.listen_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
