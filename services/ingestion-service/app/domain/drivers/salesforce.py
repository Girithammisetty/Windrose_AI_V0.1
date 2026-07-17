"""Real Salesforce driver (httpx over the REST Query API + OAuth2).

Credential-gated: the adapter speaks the real Salesforce wire protocol but a live
pull needs org credentials (username + password + security token + connected-app
client id/secret). A contract test injects an ``httpx`` mock transport to
exercise the OAuth + SOQL + pagination request/response shaping offline.

**Watermark / no-splicing note (the one honest ceiling among the new drivers).**
SOQL over the REST API has *no* bind-parameter facility — the query travels in
the request URL. The watermark still enters as a **typed** ``datetime`` bound in
``params`` (never free text); this driver renders it through
``soql_datetime_literal`` which accepts *only* a ``datetime`` and emits the
canonical ISO-8601 SystemModstamp literal Salesforce requires (unquoted). A
string or any non-datetime is rejected — so the value can never carry injection
payloads. Every other new driver uses true driver-level bind parameters; this is
documented as Salesforce's protocol limitation.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

# Recognises the incremental wrap produced by domain.watermark.build_incremental_query:
#   SELECT * FROM (<inner soql>) src WHERE <col> <op> :watermark
_WRAP_RE = re.compile(
    r"^\s*SELECT \* FROM \((?P<inner>.*)\) src WHERE (?P<col>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?P<op>>=|<=|>|<|=)\s*:watermark\s*$",
    re.DOTALL,
)


def soql_datetime_literal(value: Any) -> str:
    """Render a bound watermark value as a canonical SOQL datetime literal.

    Accepts ONLY a ``datetime`` — SystemModstamp is always a datetime — so no
    untyped/free-text value can ever reach the SOQL string (injection-safe).
    """
    if not isinstance(value, datetime):
        raise ValueError(
            f"Salesforce watermark must be a typed datetime, got {type(value).__name__}"
        )
    text = value.isoformat()
    return text.replace("+00:00", "Z") if text.endswith("+00:00") else text


def build_soql(sql: str, params: dict[str, Any]) -> str:
    """Turn the runner's wrapped statement back into valid incremental SOQL.

    The watermark value comes from ``params`` (a typed datetime) and is rendered
    via :func:`soql_datetime_literal`; it is never taken from the SQL text.
    """
    match = _WRAP_RE.match(sql)
    if not match:
        if ":watermark" in sql:
            raise ValueError("unrecognised watermark wrap for Salesforce SOQL")
        return sql.strip().rstrip(";")
    inner = match.group("inner").strip().rstrip(";")
    col, op = match.group("col"), match.group("op")
    literal = soql_datetime_literal(params["watermark"])
    joiner = "AND" if re.search(r"\bWHERE\b", inner, re.IGNORECASE) else "WHERE"
    return f"{inner} {joiner} {col} {op} {literal}"


def _login_host(config: BaseModel) -> str:
    domain = getattr(config, "domain", "login")
    return f"https://{'test' if domain == 'test' else 'login'}.salesforce.com"


class _SalesforceSession:
    """OAuth2 password-flow session + SOQL pagination over one httpx client."""

    def __init__(
        self, config: BaseModel, secrets: dict[str, str], *, timeout: float, transport
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.timeout = timeout
        self._transport = transport
        self.api_version = getattr(config, "api_version", "59.0")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def authenticate(self, client: httpx.AsyncClient) -> tuple[str, str]:
        password = (self.secrets.get("password") or "") + (self.secrets.get("security_token") or "")
        form = {
            "grant_type": "password",
            "client_id": self.secrets.get("client_id", ""),
            "client_secret": self.secrets.get("client_secret", ""),
            "username": self.config.username,
            "password": password,
        }
        resp = await client.post(f"{_login_host(self.config)}/services/oauth2/token", data=form)
        if resp.status_code in (400, 401):
            raise TransientSourceError(
                ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
            )
        resp.raise_for_status()
        body = resp.json()
        instance_url = getattr(self.config, "instance_url", None) or body["instance_url"]
        return body["access_token"], instance_url

    async def query_pages(self, client, token: str, instance_url: str, soql: str):
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{instance_url}/services/data/v{self.api_version}/query"
        resp = await client.get(url, params={"q": soql}, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        while True:
            yield [_strip_attributes(r) for r in body.get("records", [])]
            next_url = body.get("nextRecordsUrl")
            if body.get("done", True) or not next_url:
                break
            resp = await client.get(f"{instance_url}{next_url}", headers=headers)
            resp.raise_for_status()
            body = resp.json()


def _strip_attributes(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "attributes"}


class SalesforceProber:
    def __init__(self, *, connect_timeout_s: float = 15.0, transport=None) -> None:
        self.connect_timeout_s = connect_timeout_s
        self._transport = transport

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        session = _SalesforceSession(
            config, secrets, timeout=self.connect_timeout_s, transport=self._transport
        )
        try:
            async with session._client() as client:
                await session.authenticate(client)  # OAuth round-trip == trivial probe
        except TransientSourceError as exc:
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=exc.category,
                error_detail=exc.message,
            )
        except httpx.HTTPError:
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=ErrorCategory.SOURCE_UNREACHABLE,
                error_detail="request failed (scrubbed)",
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class SalesforcePreviewer:
    def __init__(self, *, connect_timeout_s: float = 30.0, transport=None) -> None:
        self.connect_timeout_s = connect_timeout_s
        self._transport = transport

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        soql = request.get("query")
        if not soql and request.get("table"):
            soql = f"SELECT FIELDS(ALL) FROM {request['table']} LIMIT {int(limit)}"
        if not soql:
            raise ValueError("preview requires query or table (Salesforce object)")
        session = _SalesforceSession(
            config, secrets, timeout=self.connect_timeout_s, transport=self._transport
        )
        rows: list[dict[str, Any]] = []
        async with session._client() as client:
            token, instance_url = await session.authenticate(client)
            async for page in session.query_pages(client, token, instance_url, soql):
                rows.extend(page)
                if len(rows) >= limit:
                    break
        rows = rows[:limit]
        columns = list(rows[0].keys()) if rows else []
        return PreviewResult(columns=columns, rows=rows)


class SalesforceQuerySource:
    def __init__(
        self, *, connect_timeout_s: float = 15.0, query_timeout_s: float = 1600.0, transport=None
    ) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s
        self._transport = transport

    def _session(self, config: BaseModel, secrets: dict[str, str], timeout: float):
        return _SalesforceSession(config, secrets, timeout=timeout, transport=self._transport)

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        # SystemModstamp watermark check: probe the object's field shape with a
        # single-row SOQL read; fall back to the SELECT field list if empty.
        session = self._session(config, secrets, self.connect_timeout_s)
        probe_soql = re.sub(r"\blimit\s+\d+\s*$", "", statement.strip().rstrip(";"), flags=re.I)
        probe_soql = f"{probe_soql} LIMIT 1"
        try:
            async with session._client() as client:
                token, instance_url = await session.authenticate(client)
                async for page in session.query_pages(client, token, instance_url, probe_soql):
                    if page:
                        return list(page[0].keys())
                    break
        except TransientSourceError:
            raise
        except httpx.HTTPError as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, "column introspection failed"
            ) from exc
        return _select_fields(statement)

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        soql = build_soql(sql, params)  # typed watermark rendered as a SOQL literal
        session = self._session(config, secrets, self.query_timeout_s)
        try:
            async with session._client() as client:
                token, instance_url = await session.authenticate(client)
                async for page in session.query_pages(client, token, instance_url, soql):
                    if page:
                        yield page
        except TransientSourceError:
            raise
        except httpx.HTTPError as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, "salesforce query failed (scrubbed)"
            ) from exc


def _select_fields(soql: str) -> list[str]:
    match = re.search(r"select\s+(.*?)\s+from\s", soql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    return [f.strip() for f in match.group(1).split(",") if f.strip()]
