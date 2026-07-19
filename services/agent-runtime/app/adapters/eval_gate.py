"""Eval-gate verifier — makes the agent-version publish gate REAL (P1).

The publish path (registry.publish_version) requires a passing eval gate before an
agent version goes live. Historically it only checked that ``eval_gate_result_id``
was non-null — so a hard-coded placeholder string satisfied it and prompt/graph
changes shipped with no actual evaluation. This client resolves that id against
eval-service (``GET /api/v1/gates/{id}``) and confirms the gate genuinely PASSED,
so a fake or failed gate can no longer authorize a publish.

Fail-closed: any transport error / non-200 / missing gate resolves to
``VerifyResult(found=False, passed=False)`` — the publish is blocked (the operator
must supply an explicit force+reason, which is audited).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("agent-runtime.eval_gate")


@dataclass(frozen=True, slots=True)
class VerifyResult:
    found: bool
    passed: bool


class EvalGateVerifier:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def verify(self, gate_run_id: str, *, auth_token: str) -> VerifyResult:
        if not gate_run_id:
            return VerifyResult(found=False, passed=False)
        url = f"{self._base}/api/v1/gates/{gate_run_id}"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("eval-gate verify failed (blocking publish): id=%s err=%r",
                           gate_run_id, exc)
            return VerifyResult(found=False, passed=False)
        if resp.status_code == 404:
            return VerifyResult(found=False, passed=False)
        if resp.status_code != 200:
            logger.warning("eval-gate verify non-200 (blocking publish): id=%s status=%s",
                           gate_run_id, resp.status_code)
            return VerifyResult(found=False, passed=False)
        gate = (resp.json() or {}).get("data") or {}
        return VerifyResult(found=True, passed=bool(gate.get("gate_passed")))
