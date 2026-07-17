"""AgentRunWorkflow (ART-FR-010/011): one Temporal workflow per agent run.

Durable HITL: after a write-tier proposal is created the workflow blocks in
``awaiting_approval`` on a ``proposal_decision`` signal (days-long OK) or the
proposal-expiry timer. Worker crashes replay from history; the run never dies
(AC-2, NFR run durability). All IO is in activities; the workflow is deterministic.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="AgentRunWorkflow")
class AgentRunWorkflow:
    def __init__(self) -> None:
        self._decision: dict | None = None
        self._cancelled = False
        self._killed = False

    @workflow.signal(name="proposal_decision")
    def proposal_decision(self, decision: dict) -> None:
        self._decision = decision

    @workflow.signal(name="cancel")
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.signal(name="kill")
    def kill(self) -> None:
        self._killed = True

    @workflow.query(name="status")
    def status(self) -> str:
        if self._killed:
            return "killed"
        if self._cancelled:
            return "cancelled"
        if self._decision is not None:
            return "decided"
        return "running"

    @workflow.run
    async def run(self, req: dict) -> dict:
        retry = RetryPolicy(maximum_attempts=3,
                            non_retryable_error_types=["GuardrailError", "BudgetError"])
        short = timedelta(seconds=180)

        outcome = await workflow.execute_activity(
            "run_graph", args=[req], start_to_close_timeout=short, retry_policy=retry)
        # The computed answer MUST travel to finalize_run — it persists it on
        # the Run and streams it to the hub; dropping it here loses the answer.
        final_text = outcome.get("final_text")

        if not outcome.get("write_intent"):
            # SLM distillation (task #72): capture the read-only run transcript
            # (no proposal). Mirrors the inline engine's capture at run completion.
            await workflow.execute_activity(
                "capture_transcript", args=[req, outcome, None],
                start_to_close_timeout=short, retry_policy=retry)
            await workflow.execute_activity(
                "finalize_run",
                args=[req["tenant_id"], req["run_id"], "completed",
                      outcome.get("usage", {}), final_text],
                start_to_close_timeout=short, retry_policy=retry)
            return {"status": "completed", "usage": outcome.get("usage", {}),
                    "final_text": final_text}

        created = await workflow.execute_activity(
            "create_proposal", args=[req, outcome["write_intent"]],
            start_to_close_timeout=short, retry_policy=retry)

        # SLM distillation (task #72): capture the write-tier run transcript ONCE,
        # right after the proposal exists (so the transcript carries proposal_id)
        # and BEFORE the durable HITL wait — so the training pair is recorded for
        # every write run regardless of the eventual decision. The human decision
        # is joined onto this row later by ProposalService.decide's attach_decision
        # (which no-op'd for Temporal runs until this capture existed).
        await workflow.execute_activity(
            "capture_transcript", args=[req, outcome, created["proposal_id"]],
            start_to_close_timeout=short, retry_policy=retry)

        if created["executed"]:  # auto-execute policy path
            await workflow.execute_activity(
                "finalize_run",
                args=[req["tenant_id"], req["run_id"], "completed",
                      outcome.get("usage", {}), final_text],
                start_to_close_timeout=short, retry_policy=retry)
            return {"status": "completed", "proposal_id": created["proposal_id"],
                    "outcome": "auto_executed", "final_text": final_text}

        # ---- durable HITL wait (ART-FR-042) --------------------------------
        await workflow.execute_activity(
            "mark_awaiting", args=[req["tenant_id"], req["run_id"], final_text],
            start_to_close_timeout=short, retry_policy=retry)

        expiry = timedelta(seconds=req.get("proposal_ttl_seconds", 7 * 24 * 3600))
        # wait_condition returns None normally and raises TimeoutError on expiry.
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None or self._cancelled or self._killed,
                timeout=expiry)
        except TimeoutError:  # proposal expired (BR-9)
            await workflow.execute_activity(
                "finalize_run",
                args=[req["tenant_id"], req["run_id"], "expired",
                      outcome.get("usage", {}), final_text],
                start_to_close_timeout=short, retry_policy=retry)
            return {"status": "completed", "outcome": "expired_proposal",
                    "proposal_id": created["proposal_id"]}

        if self._killed:
            return {"status": "killed", "proposal_id": created["proposal_id"]}
        if self._cancelled:
            return {"status": "cancelled", "proposal_id": created["proposal_id"]}

        dec = self._decision
        exec_result = None
        if dec.get("action") in ("approve", "edit_args"):
            exec_result = await workflow.execute_activity(
                "execute_proposal",
                args=[req["tenant_id"], created["proposal_id"],
                      dec.get("decided_by", "unknown"), dec.get("args", {})],
                start_to_close_timeout=short, retry_policy=retry)

        await workflow.execute_activity(
            "finalize_run",
            args=[req["tenant_id"], req["run_id"], "completed",
                  outcome.get("usage", {}), final_text],
            start_to_close_timeout=short, retry_policy=retry)
        return {"status": "completed", "proposal_id": created["proposal_id"],
                "decision": dec, "execution": exec_result, "final_text": final_text}
