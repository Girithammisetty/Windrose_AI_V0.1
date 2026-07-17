"""Decision write-back (INS-FR-061): governed, idempotent, four-eyes delivery
of a decision to an OUTGOING connection. The http_post executor delivers to a
REAL local HTTP sink server (no mock) so the full loop is exercised end to end.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.util import TENANT_A, bearer, make_token


class _Sink(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode() if n else "{}"
        _Sink.received.append({
            "path": self.path,
            "idempotency_key": self.headers.get("Idempotency-Key"),
            "json": json.loads(body),
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_a):  # silence
        pass


@pytest.fixture
def sink():
    _Sink.received = []
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Sink
    srv.shutdown()


async def _outgoing_http_conn(client, auth, url: str) -> dict:
    resp = await client.post(
        "/api/v1/connections",
        json={
            "name": "SoR webhook",
            "connector_type": "http_api",
            "config": {"url": url, "method": "POST"},
            "traffic_direction": "outgoing",
            "skip_test": True,
        },
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _wb_body(conn_id: str, **over) -> dict:
    return {
        "connection_id": conn_id,
        "decision_kind": "case.disposition",
        "decision_ref": "wr:t:case:case/42",
        "idempotency_key": "case-42-denied",
        "target": {"path": "decisions"},
        "payload": {"case_id": "42", "disposition": "denied", "note": "not covered"},
        **over,
    }


class TestWriteback:
    async def test_four_eyes_delivery_to_real_sink(self, client, auth_a, rsa_keys, sink):
        url, Sink = sink
        conn = await _outgoing_http_conn(client, auth_a, url)

        # enqueue as user-a -> pending_approval, nothing delivered yet
        r = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        assert r.status_code == 201, r.text
        wb = r.json()["data"]
        assert wb["status"] == "pending_approval"
        assert Sink.received == []

        # four-eyes: the requester cannot approve their own write-back
        same = await client.post(f"/api/v1/writebacks/{wb['id']}/approve", headers=auth_a)
        assert same.status_code == 422, same.text

        # a DISTINCT approver (same tenant) approves -> real delivery
        approver = bearer(make_token(rsa_keys[0], TENANT_A, sub="approver"))
        ok = await client.post(f"/api/v1/writebacks/{wb['id']}/approve", headers=approver)
        assert ok.status_code == 200, ok.text
        done = ok.json()["data"]
        assert done["status"] == "delivered"
        assert done["approved_by"] == "approver"

        # the real sink received the decision with the idempotency key
        assert len(Sink.received) == 1
        got = Sink.received[0]
        assert got["path"] == "/decisions"
        assert got["idempotency_key"] == "case-42-denied"
        assert got["json"]["disposition"] == "denied"

    async def test_idempotent_enqueue(self, client, auth_a, sink):
        url, _ = sink
        conn = await _outgoing_http_conn(client, auth_a, url)
        r1 = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        r2 = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    async def test_reject(self, client, auth_a, sink):
        url, Sink = sink
        conn = await _outgoing_http_conn(client, auth_a, url)
        r = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        wb = r.json()["data"]
        rej = await client.post(f"/api/v1/writebacks/{wb['id']}/reject", headers=auth_a)
        assert rej.status_code == 200
        assert rej.json()["data"]["status"] == "rejected"
        assert Sink.received == []

    async def test_rejects_incoming_connection(self, client, auth_a):
        # an INCOMING connection is not a valid write-back target
        from tests.util import create_connection

        conn = await create_connection(client, auth_a, name="reader")
        r = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        assert r.status_code == 422, r.text
        assert "outgoing" in r.text

    async def test_requester_cannot_self_select_auto_delivery(self, client, auth_a, sink):
        # H1 guard: a requester asking for approval_mode=auto must NOT get an
        # immediate delivery — the row stays pending_approval (four-eyes forced).
        url, Sink = sink
        conn = await _outgoing_http_conn(client, auth_a, url)
        r = await client.post(
            "/api/v1/writebacks",
            json=_wb_body(conn["id"], approval_mode="auto"),
            headers=auth_a,
        )
        assert r.status_code == 201, r.text
        wb = r.json()["data"]
        assert wb["status"] == "pending_approval"
        assert wb["approval_mode"] == "four_eyes"
        assert Sink.received == []  # nothing delivered without a distinct approver

    async def test_ssrf_blocked_to_link_local(self, client, auth_a, rsa_keys):
        # H2 guard: an http_api target resolving to a link-local address (the
        # cloud-metadata range) is refused at delivery; no body is exfiltrated.
        conn = await _outgoing_http_conn(client, auth_a, "http://169.254.169.254")
        r = await client.post("/api/v1/writebacks", json=_wb_body(conn["id"]), headers=auth_a)
        wb = r.json()["data"]
        approver = bearer(make_token(rsa_keys[0], TENANT_A, sub="approver"))
        ok = await client.post(f"/api/v1/writebacks/{wb['id']}/approve", headers=approver)
        assert ok.status_code == 200, ok.text
        done = ok.json()["data"]
        assert done["status"] == "failed"
        assert "SsrfBlocked" in (done["last_error"] or "")
