"""Candidate-output providers for eval runs (EVL-FR-020).

A provider yields the candidate agent's output for a given case. Two real
implementations:

* :class:`InlineCandidateProvider` — the run carries a ``{case_id: output}`` map
  (CI executes the candidate build once and posts its outputs; the eval-service
  scores them). No stub: these are the real candidate build's outputs.
* :class:`AgentRuntimeReplayProvider` — calls agent-runtime's replay /
  no-side-effect endpoint (BRD 14 ART-FR-015: write tools stubbed, memory writes
  suppressed, corpus retrieval pinned to ``memory_snapshot_ver``) to produce the
  candidate output live for each case.
"""

from __future__ import annotations

import httpx


class CandidateUnavailable(Exception):
    """No REAL candidate output could be produced for a case.

    Raised instead of silently returning ``{}`` (which the scorers would treat
    as a genuine — empty — candidate and score as if real). The runner catches
    this, marks the case/run degraded, and logs loudly (EVL-FR-020). Carries the
    case id and a human reason for the eval-run diagnostics.
    """

    def __init__(self, case_id: str, reason: str):
        self.case_id = case_id
        self.reason = reason
        super().__init__(f"candidate unavailable for case {case_id!r}: {reason}")


class InlineCandidateProvider:
    def __init__(self, outputs: dict[str, dict]):
        self._outputs = outputs or {}

    async def candidate_output(self, *, agent_key, candidate, case, memory_snapshot_ver=None):
        case_id = case["id"]
        if case_id not in self._outputs:
            # CI did not supply an output for this case (or none was supplied at
            # all). Degrade honestly rather than scoring an empty candidate.
            raise CandidateUnavailable(
                case_id,
                "no candidate output supplied (inline provider miss) — CI must post "
                "the candidate build's output for every case, or configure "
                "EVAL_AGENT_RUNTIME_URL for live replay",
            )
        return self._outputs[case_id]


class AgentRuntimeReplayProvider:
    """Real HTTP client to agent-runtime replay mode (ART-FR-015).

    agent-runtime does not yet implement the replay/no-side-effect endpoint. When
    it is missing (404) or unreachable, this raises :class:`CandidateUnavailable`
    so the run degrades honestly — it never fabricates or empties the candidate.
    """

    def __init__(
        self,
        base_url: str,
        *,
        replay_path: str = "/api/v1/replay",
        jwt_provider=None,
        timeout_s: float = 120.0,
    ):
        self._base = base_url.rstrip("/")
        self._path = replay_path
        self._jwt_provider = jwt_provider
        self._timeout = timeout_s
        # Reuse one client (and its TCP+TLS connection pool) across calls rather
        # than paying a fresh handshake per replay request.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def candidate_output(self, *, agent_key, candidate, case, memory_snapshot_ver=None):
        body = {
            "agent_key": agent_key,
            "candidate": candidate,
            "input": case.get("input", {}),
            "memory_snapshot_ver": memory_snapshot_ver,
            "no_side_effect": True,
        }
        headers = {}
        if self._jwt_provider is not None:
            headers["Authorization"] = f"Bearer {self._jwt_provider(case.get('_tenant_id', ''))}"
        url = self._base + self._path
        try:
            client = self._http()
            resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise CandidateUnavailable(
                case["id"], f"agent-runtime replay unreachable at {url}: {exc}"
            ) from exc
        if resp.status_code == 404:
            # The replay endpoint is not implemented in agent-runtime yet
            # (ART-FR-015). Degrade loudly instead of scoring nothing as real.
            raise CandidateUnavailable(
                case["id"],
                f"agent-runtime replay endpoint {url} returned 404 — ART-FR-015 "
                "(replay/no-side-effect mode) is not implemented yet",
            )
        if resp.status_code >= 400:
            raise CandidateUnavailable(
                case["id"],
                f"agent-runtime replay {url} failed: {resp.status_code} {resp.text[:200]}",
            )
        data = resp.json()
        return data.get("output", data)
