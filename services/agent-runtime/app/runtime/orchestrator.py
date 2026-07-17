"""Run orchestration: kill-check (ART-FR-063) -> version resolution (BR-3) ->
session create/resume (ART-FR-020/023) -> run create -> execute (Temporal
workflow when enabled, else inline). Shared by the chat API and autonomous runs."""

from __future__ import annotations

from datetime import timedelta

from app.domain import policy as policy_mod
from app.domain.entities import Run, Session, new_uuid, now
from app.domain.errors import AgentKilled, NotFound, SessionExpired


class Orchestrator:
    def __init__(self, container) -> None:
        self._c = container

    async def resolve_version(self, tenant_id: str, agent_key: str, session_seed: str) -> int:
        c = self._c
        cfg = await c.store.get_tenant_config(tenant_id, agent_key)
        default_v = await c.store.latest_published_version(agent_key)
        if default_v is None:
            raise NotFound(f"agent {agent_key} has no published version")
        pinned = cfg.pinned_version if cfg else None
        canary_v = None
        rollout = await c.store.active_rollout(agent_key, c.settings.env)
        if rollout and rollout.mode == "canary" and pinned is None:
            if policy_mod.canary_assignment(session_seed, rollout.pct):
                canary_v = rollout.candidate_version
        version = policy_mod.resolve_version(
            kill_active=False, pinned_version=pinned, canary_version=canary_v,
            default_version=default_v)
        # Kill switch check (refuse new sessions) — BR-3 highest precedence.
        if await c.kill_registry.is_killed(agent_key=agent_key, version=version,
                                           tenant_id=tenant_id):
            raise AgentKilled("this assistant is temporarily unavailable")
        return version

    async def get_or_create_session(
        self, *, tenant_id: str, user_id: str | None, agent_key: str,
        session_id: str | None, context_urn: str | None,
    ) -> Session:
        c = self._c
        if session_id:
            s = await c.store.get_session(tenant_id, session_id)
            if s is None:
                raise NotFound("session not found")  # cross-tenant -> 404 (AC-14)
            if s.status == "expired":
                raise SessionExpired("session expired; start a new one")
            if s.status == "terminated":
                raise SessionExpired("session terminated")
            if user_id and s.user_id and s.user_id != user_id:
                raise NotFound("session not found")  # BR-11 non-leak
            s.last_activity_at = now()
            if s.status == "idle":
                s.status = "active"
            await c.store.update_session(s)
            await self._project_session_owner(s)
            return s
        sid = new_uuid()
        version = await self.resolve_version(tenant_id, agent_key, sid)
        s = Session(
            session_id=sid, tenant_id=tenant_id, user_id=user_id, agent_key=agent_key,
            agent_version=version, context_urn=context_urn, status="active",
            created_at=now(), last_activity_at=now(),
            expires_hard_at=now() + timedelta(seconds=c.settings.max_lifetime_seconds))
        await c.store.create_session(s)
        await self._project_session_owner(s)
        return s

    async def _project_session_owner(self, s: Session) -> None:
        """Write/refresh the realtime-hub chat-authz ownership key
        ``rt:session:{tenant}/{session_id}`` -> owner sub (RTH-FR-003: the hub
        only READS this projection; agent-runtime is its writer). TTL = the
        session's remaining hard lifetime."""
        if not s.user_id:
            return  # autonomous sessions have no chat owner
        ttl = int((s.expires_hard_at - now()).total_seconds())
        await self._c.session_proj.put(
            tenant_id=s.tenant_id, session_id=s.session_id,
            owner_sub=s.user_id, ttl_seconds=ttl)

    async def start_run(
        self, *, principal, agent_key: str, inputs: dict, session: Session,
        principal_type: str = "user_obo",
    ) -> tuple[Run, dict]:
        c = self._c
        run = Run(
            run_id=new_uuid(), tenant_id=session.tenant_id, session_id=session.session_id,
            agent_key=agent_key, agent_version=session.agent_version,
            temporal_workflow_id=None, status="queued", principal_type=principal_type,
            obo_sub=(principal.sub if principal else None))
        await c.store.create_run(run)

        cfg = await c.store.get_tenant_config(session.tenant_id, agent_key)
        prompt_params = cfg.prompt_params if cfg else {}
        auto_policy = cfg.auto_execute_policy if cfg else {}
        obo_token = _obo_token(c, principal, session, agent_key)
        inputs = {**inputs, "tenant_id": session.tenant_id}

        if c.settings.use_temporal and "temporal_client" in c.extras:
            wf_id = f"run:{run.run_id}"
            run.temporal_workflow_id = wf_id
            await c.store.update_run(run)
            req = {
                "tenant_id": session.tenant_id, "run_id": run.run_id, "inputs": inputs,
                "obo_token": obo_token, "obo_user": (principal.sub if principal else None),
                "prompt_params": prompt_params, "auto_execute_policy": auto_policy,
                "proposal_ttl_seconds": c.settings.proposal_default_ttl_seconds,
            }
            await c.extras["temporal_client"].start_workflow(
                "AgentRunWorkflow", req, id=wf_id, task_queue=c.settings.temporal_task_queue)
            return run, {"mode": "temporal", "workflow_id": wf_id}

        summary = await c.run_engine.execute(
            run, inputs, obo_token=obo_token,
            obo_user=(principal.sub if principal else None),
            prompt_params=prompt_params, auto_execute_policy=auto_policy)
        return run, {"mode": "inline", **summary}


def _obo_token(c, principal, session: Session, agent_key: str) -> str | None:
    if principal is None:
        return c.token_minter.mint_agent_autonomous(
            tenant_id=session.tenant_id, agent_key=agent_key,
            agent_version=session.agent_version, scopes=["*"])
    return c.token_minter.mint_agent_obo(
        tenant_id=session.tenant_id, obo_sub=principal.sub, agent_key=agent_key,
        agent_version=session.agent_version,
        workspace_id=getattr(principal, "workspace_id", None), scopes=["*"])
