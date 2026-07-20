"""Event-driven decisioning — the real-time intelligence path (inc 1).

Windrose's agents are triggered by a human (chat/copilot) or a schedule
(retrain watches, batch runs). That caps decision latency at the schedule
interval. This dispatcher closes the gap: a domain event (e.g. ``case.created``)
fires a GOVERNED autonomous agent run immediately, so a decision is drafted within
seconds of the data arriving.

Crucially this does NOT bypass governance. The run produces a WriteIntent →
ProposalService, where the existing spine still applies: the agent's toolset
allow-list, the risk tiering (irreversible / bulk / write-direct never auto-
execute), the Rule-of-Two untrusted-input gate, the low-confidence escalation, and
the tenant's auto-execute policy. Net effect: safe, high-confidence events are
decided in real time; risky ones are queued for a human — both fully audited.

Transport: this is the ``handler`` for ``windrose_common.kafka.KafkaConsumer``,
which already supplies consumer-group semantics, Redis dedup (at-least-once
safety), retry/backoff and a real DLQ — so this module owns only the governed
decision to fire, never the plumbing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.errors import AgentKilled, NotFound
from app.runtime.orchestrator import Orchestrator

logger = logging.getLogger("agent-runtime.event_triggers")

# event_type -> agent that decides it. Platform defaults; a deployment may override
# via EventTriggerDispatcher(triggers=...). A tenant still opts in per-agent via
# TenantAgentConfig.enabled, so an unconfigured tenant is never auto-triggered.
DEFAULT_TRIGGERS: dict[str, str] = {
    "case.created": "case-triage",
}


@dataclass(frozen=True, slots=True)
class TriggerOutcome:
    fired: bool
    reason: str
    agent_key: str | None = None
    run_id: str | None = None


class EventTriggerDispatcher:
    def __init__(self, container, *, triggers: dict[str, str] | None = None) -> None:
        self._c = container
        self._triggers = dict(triggers if triggers is not None else DEFAULT_TRIGGERS)

    async def handle(self, envelope: dict) -> TriggerOutcome:
        """Decide whether a domain event should fire an agent run, and fire it.

        Never raises on a bad/unmapped event — returns a skip outcome so a poison
        message can't stall the consumer group (the consumer's DLQ handles real
        processing failures).
        """
        if not isinstance(envelope, dict):
            return TriggerOutcome(False, "malformed_envelope")
        tenant_id = envelope.get("tenant_id")
        event_type = envelope.get("event_type")
        # Master envelope (MASTER-FR-031) carries the body under `payload` and the
        # subject under top-level `resource_urn`; accept `data` as an alias so a
        # hand-published/simplified event still works.
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            payload = envelope.get("data") or {}
        if not tenant_id or not event_type:
            return TriggerOutcome(False, "malformed_envelope")

        agent_key = self._triggers.get(event_type)
        if not agent_key:
            return TriggerOutcome(False, "no_trigger_for_event_type")

        # Tenant opt-in: an agent explicitly disabled for this tenant is never
        # auto-triggered (a tenant with no config uses the platform default).
        cfg = await self._c.store.get_tenant_config(tenant_id, agent_key)
        if cfg is not None and not cfg.enabled:
            return TriggerOutcome(False, "agent_disabled_for_tenant", agent_key)

        orch = Orchestrator(self._c)
        try:
            # resolve_version() inside enforces the kill switch + published version.
            session = await orch.get_or_create_session(
                tenant_id=tenant_id, user_id=None, agent_key=agent_key,
                session_id=None,
                context_urn=envelope.get("resource_urn") or payload.get("urn"))
        except AgentKilled:
            return TriggerOutcome(False, "agent_killed", agent_key)
        except NotFound:
            return TriggerOutcome(False, "agent_has_no_published_version", agent_key)

        # Carry the event payload as inputs + explicit trigger provenance so the
        # run/proposal records WHY it ran (auditable evidence→action linkage).
        inputs = {
            **payload,
            "trigger": {"event_type": event_type, "event_id": envelope.get("event_id"),
                        "resource_urn": envelope.get("resource_urn")},
        }
        run, _ = await orch.start_run(
            principal=None, agent_key=agent_key, inputs=inputs, session=session,
            principal_type="agent_autonomous")
        logger.info("event trigger fired: event=%s tenant=%s agent=%s run=%s",
                    event_type, tenant_id, agent_key, run.run_id)
        return TriggerOutcome(True, "fired", agent_key, run.run_id)
