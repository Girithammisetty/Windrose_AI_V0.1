"""SLM transcript sink (distillation milestone 1).

Captures a COMPLETED agent run into the governed ``agent_transcripts`` corpus —
PII-redacted and consent-gated — and, when the run's proposal is later decided,
joins in the human decision (approve / edit / reject) + the corrected output.
An approved or edited proposal is a gold (input -> corrected-output) training
pair (docs/design/slm-distillation.md).

Capture is strictly best-effort: a capture failure NEVER fails the run or the
decision (the learning loop is a side-channel, not on the critical path).
"""

from __future__ import annotations

import logging

from app.domain.entities import Run, Transcript, new_uuid
from app.domain.redact import redact

log = logging.getLogger("agent_runtime.transcripts")

# proposal action -> training-signal label
_DECISION_LABEL = {"approve": "approve", "edit_args": "edit", "reject": "reject",
                   "respond": "cancel"}


class TranscriptSink:
    def __init__(self, store, *, enabled: bool) -> None:
        self._store = store
        # `enabled` is the tenant/deploy consent gate: capture happens only when
        # opted in, and the stored `consent` flag records that for curation.
        self._enabled = enabled

    async def capture(self, run: Run, inputs: dict, outcome, proposal_id: str | None) -> None:
        if not self._enabled:
            return
        try:
            wi = getattr(outcome, "write_intent", None)
            proposed = None
            if wi is not None:
                proposed = {
                    "tool_id": getattr(wi, "tool_id", None),
                    "tool_version": getattr(wi, "tool_version", None),
                    "required_action": getattr(wi, "required_action", None),
                    "args": redact(getattr(wi, "args", {}) or {}),
                }
            usage = getattr(outcome, "usage", {}) or {}
            model = None
            if isinstance(usage, dict):
                model = usage.get("model") or usage.get("rung") or usage.get("deployment")
            evidence = getattr(outcome, "evidence", None) or []

            t = Transcript(
                transcript_id=new_uuid(), tenant_id=run.tenant_id, run_id=run.run_id,
                session_id=run.session_id, agent_key=run.agent_key,
                agent_version=run.agent_version, principal_type=run.principal_type,
                obo_sub=run.obo_sub,
                inputs=redact(inputs or {}),
                grounding={"evidence": redact(evidence)},
                final_text=redact(outcome.final_text) if outcome.final_text else None,
                proposed_action=proposed, proposal_id=proposal_id,
                model=model, usage=usage if isinstance(usage, dict) else {},
                consent=True,
            )
            await self._store.record_transcript(t)
        except Exception:  # noqa: BLE001 — never fail a run because capture failed
            log.warning("transcript capture failed for run %s", run.run_id, exc_info=True)

    async def attach_decision(
        self, *, tenant_id: str, proposal_id: str, action: str,
        edited_args: dict | None, decided_by: str, decided_at,
    ) -> None:
        if not self._enabled:
            return
        try:
            await self._store.attach_transcript_decision(
                tenant_id=tenant_id, proposal_id=proposal_id,
                decision=_DECISION_LABEL.get(action, action),
                corrected_output=redact(edited_args) if edited_args else None,
                decided_by=decided_by, decided_at=decided_at,
            )
        except Exception:  # noqa: BLE001 — never fail a decision because attach failed
            log.warning("transcript decision attach failed for proposal %s",
                        proposal_id, exc_info=True)
