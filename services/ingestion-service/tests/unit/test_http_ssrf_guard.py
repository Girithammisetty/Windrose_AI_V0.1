"""Unit: the HTTP driver's SSRF guard is enforced on EVERY redirect hop.

A user-configured connection target could 302-redirect to a link-local
(cloud-metadata) or private address. httpx's own follow_redirects validates
only the first URL, so the driver follows redirects MANUALLY and re-guards each
Location. These tests bind a real loopback server (loopback is an allowed target
for the local dev/test HTTP source) that redirects to a blocked address and
assert the hop is refused — the blocked target's IP is a numeric link-local /
RFC1918 literal, so the guard rejects it before any packet leaves the host.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.domain.connectors import HttpApiConfig
from app.domain.drivers.http import HttpProber


def _server(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _redirect_server(location: str) -> ThreadingHTTPServer:
    class _H(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        do_GET = do_HEAD  # noqa: N815

        def log_message(self, *args):  # silence
            return

    return _server(_H)


def _ok_server() -> ThreadingHTTPServer:
    class _H(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_HEAD  # noqa: N815

        def log_message(self, *args):  # silence
            return

    return _server(_H)


def _url(srv: ThreadingHTTPServer, path: str = "/start") -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


async def test_probe_refuses_redirect_to_link_local_metadata() -> None:
    # 302 -> cloud-metadata endpoint (link-local); refused on the redirect hop.
    srv = _redirect_server("http://169.254.169.254/latest/meta-data/")
    try:
        probe = await HttpProber(connect_timeout_s=5).probe(
            HttpApiConfig(method="GET", url=_url(srv)), {}
        )
        assert probe.status == "failed"
        assert probe.error_category == "SOURCE_UNREACHABLE"
        assert "not allowed" in (probe.error_detail or "")
    finally:
        srv.shutdown()


async def test_probe_refuses_redirect_to_private_rfc1918() -> None:
    # 302 -> an internal RFC1918 host; refused on the redirect hop.
    srv = _redirect_server("http://10.0.0.1/internal")
    try:
        probe = await HttpProber(connect_timeout_s=5).probe(
            HttpApiConfig(method="GET", url=_url(srv)), {}
        )
        assert probe.status == "failed"
        assert probe.error_category == "SOURCE_UNREACHABLE"
        assert "not allowed" in (probe.error_detail or "")
    finally:
        srv.shutdown()


async def test_probe_follows_allowed_redirect() -> None:
    # a 302 to another ALLOWED (loopback) target is still followed to completion.
    target = _ok_server()
    src = _redirect_server(_url(target, "/data"))
    try:
        probe = await HttpProber(connect_timeout_s=5).probe(
            HttpApiConfig(method="GET", url=_url(src)), {}
        )
        assert probe.status == "ok", probe.error_detail
    finally:
        src.shutdown()
        target.shutdown()


async def test_probe_bounds_redirect_chain() -> None:
    # a self-referential redirect loop is capped, not followed forever.
    srv = _redirect_server("")  # Location patched to point at itself below
    loc = _url(srv, "/start")

    class _Loop(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", loc)
            self.end_headers()

        do_GET = do_HEAD  # noqa: N815

        def log_message(self, *args):
            return

    srv.RequestHandlerClass = _Loop
    try:
        probe = await HttpProber(connect_timeout_s=5).probe(
            HttpApiConfig(method="GET", url=loc), {}
        )
        assert probe.status == "failed"
        assert probe.error_category == "SOURCE_UNREACHABLE"
    finally:
        srv.shutdown()
