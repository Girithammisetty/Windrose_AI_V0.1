"""query-service facade: dry-run validation for POST /compile?validate=true
(SEM-FR-024).

Runtime: ``HttpQueryServiceClient`` — a real httpx client that forwards the
caller's bearer JWT to query-service's JWT-authenticated
``POST /api/v1/sql/dry-run`` (query-service has no internal/SPIFFE route —
chart-service's and case-service's query-service clients forward the caller's
token the same way) and returns its cost/ceiling verdict. Unit tests:
``FakeQueryServiceClient`` — a deterministic double (never reachable from
``app.main`` when ``use_real_adapters`` is True).
"""

from __future__ import annotations

import httpx


class FakeQueryServiceClient:
    """Deterministic dry-run: sanity-checks the compiled artifact shape and
    returns a fixed cost estimate. Unit-test double only — NOT wired into the
    real runtime container. Tests may pre-program verdicts."""

    def __init__(self):
        self.calls: list[dict] = []
        self.verdict = "ok"
        self.estimated_bytes = 1024 * 1024

    async def dry_run(self, tenant_id: str, sql: str, params: list[dict],
                      dialect: str, token: str) -> dict:
        self.calls.append({"tenant_id": tenant_id, "sql": sql, "params": params,
                           "dialect": dialect, "token": token})
        valid = bool(sql) and sql.upper().startswith(("SELECT", "WITH"))
        return {
            "valid": valid,
            "estimated_bytes": self.estimated_bytes if valid else None,
            "verdict": self.verdict if valid else "invalid",
            "message": None if valid else "compiled SQL is not a SELECT",
        }


# query-service error codes that mean "the compiled SQL itself is not
# runnable" (as opposed to COST_CEILING_EXCEEDED, which means the SQL is fine
# but too expensive — see internal/domain/errors.go).
_INVALID_CODES = frozenset({
    "VALIDATION_FAILED", "VARIABLE_INVALID", "DATASET_NOT_FOUND",
    "STATEMENT_NOT_ALLOWED",
})


class HttpQueryServiceClient:
    """Real query-service dry-run client (SEM-FR-024, BRD 05). Forwards the
    caller's bearer JWT to query-service's ``POST /api/v1/sql/dry-run`` and
    maps its plan/ceiling response onto the ``{valid, estimated_bytes,
    verdict, message}`` cost/ceiling verdict this port returns."""

    def __init__(
        self,
        base_url: str = "http://localhost:8085",
        *,
        timeout_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport
        # Reuse one client (and its TCP+TLS connection pool) across calls rather
        # than paying a fresh handshake per request.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s, transport=self._transport)
        return self._client

    async def dry_run(self, tenant_id: str, sql: str, params: list[dict],
                      dialect: str, token: str) -> dict:
        # query-service's sql/dry-run contract takes positional binds, matching
        # how chart-service maps compiled semantic params (mirrors resolver.go).
        binds = [p.get("value") for p in params]
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        client = self._http()
        resp = await client.post(
            f"{self.base_url}/api/v1/sql/dry-run",
            json={"sql": sql, "binds": binds, "mode": "sync"},
            headers=headers,
        )
        if resp.status_code >= 400:
            error = resp.json().get("error", {}) if resp.content else {}
            code = error.get("code")
            if code == "COST_CEILING_EXCEEDED":
                return {"valid": True, "estimated_bytes": None,
                        "verdict": "over_ceiling", "message": error.get("message")}
            if code in _INVALID_CODES:
                return {"valid": False, "estimated_bytes": None,
                        "verdict": "invalid", "message": error.get("message")}
            resp.raise_for_status()
        body = resp.json()
        body = body.get("data", body)
        return {
            "valid": True,
            "estimated_bytes": body.get("estimated_scan_bytes"),
            "verdict": "ok",
            "message": None,
        }
