"""SQL store (Postgres + single-schema RLS) — the runtime store.

Tenant-scoped operations run in a session that sets ``app.tenant_id`` (RLS GUC)
so the non-privileged ``agent_runtime_app`` role only ever sees its tenant's rows
(MASTER-FR-001). ``decide_proposal`` uses ``UPDATE ... WHERE status='pending'
RETURNING`` for first-wins semantics (BR-12).
"""
# ruff: noqa: E501  (inline SQL strings are naturally wide)

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.entities import (
    AgentDefinition,
    AgentVersion,
    KillSwitch,
    Proposal,
    Rollout,
    Run,
    Session,
    SftDataset,
    SftExample,
    SlmAdapter,
    TenantAgentConfig,
    TrainingJob,
    Transcript,
    new_uuid,
)


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _j(v) -> str:
    return json.dumps(v, default=str)


def _load(v):
    if v is None:
        return None
    return v if isinstance(v, (dict, list)) else json.loads(v)


class SqlStore:
    def __init__(self, session_factory: async_sessionmaker, admin_session_factory: async_sessionmaker | None = None) -> None:
        self._sf = session_factory
        # BYPASSRLS session factory (defaults to the app-role factory when the
        # caller doesn't wire one, e.g. unit tests) — used only for cross-tenant
        # kill-switch control-plane reads/by-id ops (see _admin()).
        self._admin_sf = admin_session_factory or session_factory

    async def connect(self) -> None:
        return None

    @asynccontextmanager
    async def _plain(self):
        s = self._sf()
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    @asynccontextmanager
    async def _admin(self):
        """A session on the privileged (BYPASSRLS) engine — no app.tenant_id GUC
        is set nor needed, since this role bypasses the tenant_id-isolation
        policies entirely. Reserved for kill-switch control-plane ops (ART-FR-073)
        where the caller is already authz-gated at the route layer (operator scope,
        or "any authenticated principal" for delete/unkill by opaque id) and either
        doesn't know the row's tenant ahead of time (by-id lookup) or explicitly
        needs cross-tenant visibility (operator's kill-switch list)."""
        s = self._admin_sf()
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    @asynccontextmanager
    async def _tenant(self, tenant_id: str):
        s = self._sf()
        try:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": tenant_id})
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    # ---- agent registry ----------------------------------------------------
    async def upsert_agent_definition(self, d: AgentDefinition) -> None:
        async with self._plain() as s:
            await s.execute(text(
                """INSERT INTO agent_definitions
                   (agent_key, display_name, description, owner_team, default_write_mode,
                    status, owner_tenant)
                   VALUES (:k,:n,:d,:o,:w,:st,:ot)
                   ON CONFLICT (agent_key) DO UPDATE SET
                     display_name=:n, description=:d, owner_team=:o,
                     default_write_mode=:w, status=:st, owner_tenant=:ot, updated_at=now()"""),
                {"k": d.agent_key, "n": d.display_name, "d": d.description,
                 "o": d.owner_team, "w": d.default_write_mode, "st": d.status,
                 "ot": d.owner_tenant})

    async def get_agent_definition(self, agent_key: str) -> AgentDefinition | None:
        async with self._plain() as s:
            r = (await s.execute(text("SELECT * FROM agent_definitions WHERE agent_key=:k"),
                                 {"k": agent_key})).mappings().first()
        return _def(r) if r else None

    async def list_agent_definitions(self) -> list[AgentDefinition]:
        async with self._plain() as s:
            rows = (await s.execute(text("SELECT * FROM agent_definitions ORDER BY agent_key"))
                    ).mappings().all()
        return [_def(r) for r in rows]

    async def create_agent_version(self, v: AgentVersion) -> None:
        async with self._plain() as s:
            await s.execute(text(
                """INSERT INTO agent_versions
                   (agent_key, version, graph_ref, graph_digest, prompt_refs, toolset,
                    model_config, guardrail_profile, memory_policy, eval_gate,
                    eval_gate_result_id, a2a_card, card_signature, principal_ref, status)
                   VALUES (:k,:v,:gr,:gd, cast(:pr as jsonb), cast(:ts as jsonb),
                           cast(:mc as jsonb), :gp, cast(:mp as jsonb), cast(:eg as jsonb),
                           :egr, cast(:card as jsonb), :sig, :pref, :st)"""),
                {"k": v.agent_key, "v": v.version, "gr": v.graph_ref, "gd": v.graph_digest,
                 "pr": _j(v.prompt_refs), "ts": _j(v.toolset), "mc": _j(v.model_config),
                 "gp": v.guardrail_profile, "mp": _j(v.memory_policy), "eg": _j(v.eval_gate),
                 "egr": v.eval_gate_result_id, "card": _j(v.a2a_card),
                 "sig": v.card_signature, "pref": v.principal_ref, "st": v.status})

    async def get_agent_version(self, agent_key: str, version: int) -> AgentVersion | None:
        async with self._plain() as s:
            r = (await s.execute(text(
                "SELECT * FROM agent_versions WHERE agent_key=:k AND version=:v"),
                {"k": agent_key, "v": version})).mappings().first()
        return _ver(r) if r else None

    async def list_agent_versions(self, agent_key: str) -> list[AgentVersion]:
        async with self._plain() as s:
            rows = (await s.execute(text(
                "SELECT * FROM agent_versions WHERE agent_key=:k ORDER BY version"),
                {"k": agent_key})).mappings().all()
        return [_ver(r) for r in rows]

    async def update_agent_version(self, v: AgentVersion) -> None:
        async with self._plain() as s:
            await s.execute(text(
                """UPDATE agent_versions SET status=:st, eval_gate_result_id=:egr,
                     a2a_card=cast(:card as jsonb), card_signature=:sig,
                     principal_ref=:pref, updated_at=now()
                   WHERE agent_key=:k AND version=:v"""),
                {"st": v.status, "egr": v.eval_gate_result_id, "card": _j(v.a2a_card),
                 "sig": v.card_signature, "pref": v.principal_ref,
                 "k": v.agent_key, "v": v.version})

    async def latest_published_version(self, agent_key: str) -> int | None:
        async with self._plain() as s:
            r = (await s.execute(text(
                "SELECT max(version) m FROM agent_versions WHERE agent_key=:k AND status='published'"),
                {"k": agent_key})).mappings().first()
        return int(r["m"]) if r and r["m"] is not None else None

    # ---- tenant config -----------------------------------------------------
    async def get_tenant_config(self, tenant_id: str, agent_key: str) -> TenantAgentConfig | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT * FROM tenant_agent_configs WHERE tenant_id=cast(:t as uuid) AND agent_key=:k"),
                {"t": tenant_id, "k": agent_key})).mappings().first()
        return _cfg(r) if r else None

    async def put_tenant_config(self, c: TenantAgentConfig) -> None:
        async with self._tenant(c.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO tenant_agent_configs
                   (tenant_id, agent_key, enabled, pinned_version, prompt_params,
                    auto_execute_policy, self_approval)
                   VALUES (cast(:t as uuid), :k, :en, :pv, cast(:pp as jsonb),
                           cast(:ap as jsonb), :sa)
                   ON CONFLICT (tenant_id, agent_key) DO UPDATE SET
                     enabled=:en, pinned_version=:pv, prompt_params=cast(:pp as jsonb),
                     auto_execute_policy=cast(:ap as jsonb), self_approval=:sa, updated_at=now()"""),
                {"t": c.tenant_id, "k": c.agent_key, "en": c.enabled, "pv": c.pinned_version,
                 "pp": _j(c.prompt_params), "ap": _j(c.auto_execute_policy), "sa": c.self_approval})

    # ---- decision models (BRD 54) ------------------------------------------
    async def create_decision_model(self, m) -> None:
        from app.domain.decisions import _outcome_to_dict, rules_to_json
        async with self._plain() as s:
            await s.execute(text(
                """INSERT INTO decision_models
                   (id, tenant_id, workspace_id, name, dataset_urn, version, status,
                    rules, default_outcome, created_by)
                   VALUES (cast(:id as uuid), :t, :ws, :n, :du, :v, :st,
                           cast(:r as jsonb), cast(:d as jsonb), :cb)"""),
                {"id": m.model_id, "t": m.tenant_id, "ws": m.workspace_id, "n": m.name,
                 "du": m.dataset_urn, "v": m.version, "st": m.status,
                 "r": _j(rules_to_json(m.rules)), "d": _j(_outcome_to_dict(m.default_outcome)),
                 "cb": getattr(m, "created_by", None)})

    async def get_decision_model(self, tenant_id: str, model_id: str):
        async with self._plain() as s:
            r = (await s.execute(text(
                "SELECT * FROM decision_models WHERE id=cast(:id as uuid) AND tenant_id=:t"),
                {"id": model_id, "t": tenant_id})).mappings().first()
        return _decision_model(r) if r else None

    async def list_decision_models(self, tenant_id: str) -> list:
        async with self._plain() as s:
            rows = (await s.execute(text(
                "SELECT * FROM decision_models WHERE tenant_id=:t ORDER BY name, version DESC"),
                {"t": tenant_id})).mappings().all()
        return [_decision_model(r) for r in rows]

    # ---- outcome labels (BRD 55) -------------------------------------------
    async def upsert_outcome_label(self, lab) -> None:
        async with self._plain() as s:
            await s.execute(text(
                """INSERT INTO outcome_labels
                   (id, tenant_id, decision_ref, decision_type, producer,
                    decided_outcome, realized_outcome, correct, label_source,
                    note, labeled_by)
                   VALUES (cast(:id as uuid), :t, :dr, :dt, :pr, :do, :ro, :co,
                           :ls, :n, :lb)
                   ON CONFLICT (tenant_id, decision_ref) DO UPDATE SET
                     realized_outcome=:ro, decided_outcome=:do, correct=:co,
                     label_source=:ls, note=:n, labeled_by=:lb, labeled_at=now()"""),
                {"id": lab.label_id, "t": lab.tenant_id, "dr": lab.decision_ref,
                 "dt": lab.decision_type, "pr": lab.producer, "do": lab.decided_outcome,
                 "ro": lab.realized_outcome, "co": lab.correct, "ls": lab.label_source,
                 "n": lab.note, "lb": lab.labeled_by})

    async def list_outcome_labels(self, tenant_id: str, *, decision_type=None) -> list:
        q = "SELECT * FROM outcome_labels WHERE tenant_id=:t"
        params = {"t": tenant_id}
        if decision_type:
            q += " AND decision_type=:dt"
            params["dt"] = decision_type
        async with self._plain() as s:
            rows = (await s.execute(text(q + " ORDER BY labeled_at DESC"),
                                    params)).mappings().all()
        return [_outcome_label(r) for r in rows]

    async def get_outcome_label(self, tenant_id: str, decision_ref: str):
        async with self._plain() as s:
            r = (await s.execute(text(
                "SELECT * FROM outcome_labels WHERE tenant_id=:t AND decision_ref=:dr"),
                {"t": tenant_id, "dr": decision_ref})).mappings().first()
        return _outcome_label(r) if r else None

    # ---- rollouts ----------------------------------------------------------
    async def create_rollout(self, r: Rollout) -> None:
        async with self._plain() as s:
            await s.execute(text(
                """INSERT INTO rollouts (rollout_id, agent_key, cell, mode, candidate_version,
                     baseline_version, pct, tenant_filter, status)
                   VALUES (cast(:id as uuid),:k,:c,:m,:cv,:bv,:pct,cast(:tf as jsonb),:st)"""),
                {"id": r.rollout_id, "k": r.agent_key, "c": r.cell, "m": r.mode,
                 "cv": r.candidate_version, "bv": r.baseline_version, "pct": r.pct,
                 "tf": _j(r.tenant_filter), "st": r.status})

    async def get_rollout(self, rollout_id: str) -> Rollout | None:
        async with self._plain() as s:
            r = (await s.execute(text("SELECT * FROM rollouts WHERE rollout_id=cast(:id as uuid)"),
                                 {"id": rollout_id})).mappings().first()
        return _rollout(r) if r else None

    async def active_rollout(self, agent_key: str, cell: str) -> Rollout | None:
        async with self._plain() as s:
            r = (await s.execute(text(
                "SELECT * FROM rollouts WHERE agent_key=:k AND cell=:c AND status='active' LIMIT 1"),
                {"k": agent_key, "c": cell})).mappings().first()
        return _rollout(r) if r else None

    async def update_rollout(self, r: Rollout) -> None:
        async with self._plain() as s:
            await s.execute(text(
                "UPDATE rollouts SET status=:st, updated_at=now() WHERE rollout_id=cast(:id as uuid)"),
                {"st": r.status, "id": r.rollout_id})

    # ---- kill switches -----------------------------------------------------
    # NOTE (RLS remediation, same bug class as MASTER outbox-relay fixes): 0004
    # put kill_switches under FORCE RLS (`tenant_id IS NULL OR tenant_id = GUC`),
    # but the app.tenant_id GUC is only ever set by _tenant() sessions. Using
    # _plain() to INSERT/UPDATE a tenant-scoped row (the DEFAULT create scope,
    # agent_version_tenant) fails WITH CHECK — the non-superuser agent_runtime_app
    # role can never see/write its own tenant's row. Fixed below: create sets the
    # GUC to the row's own tenant; get/deactivate use the privileged BYPASSRLS
    # _admin() session since by-id ops don't know the tenant ahead of time and
    # the route-level authz (operator scope / plain authN for unkill) is already
    # the real gate here, per 0004's own "defense-in-depth" rationale.
    async def create_kill_switch(self, ks: KillSwitch) -> None:
        ctx = self._tenant(ks.tenant_id) if ks.tenant_id else self._plain()
        async with ctx as s:
            await s.execute(text(
                """INSERT INTO kill_switches (kill_id, scope, agent_key, version, tenant_id,
                     active, reason, set_by)
                   VALUES (cast(:id as uuid),:sc,:k,:v,:t,:a,:r,:sb)"""),
                {"id": ks.kill_id, "sc": ks.scope, "k": ks.agent_key, "v": ks.version,
                 "t": ks.tenant_id, "a": ks.active, "r": ks.reason, "sb": ks.set_by})

    async def get_kill_switch(self, kill_id: str) -> KillSwitch | None:
        async with self._admin() as s:
            r = (await s.execute(text("SELECT * FROM kill_switches WHERE kill_id=cast(:id as uuid)"),
                                 {"id": kill_id})).mappings().first()
        return _kill(r) if r else None

    async def deactivate_kill_switch(self, kill_id: str) -> None:
        async with self._admin() as s:
            await s.execute(text(
                "UPDATE kill_switches SET active=false, updated_at=now() WHERE kill_id=cast(:id as uuid)"),
                {"id": kill_id})

    async def list_kill_switches(self, tenant_id: str | None) -> list[KillSwitch]:
        """List active kill switches. tenant_id set (tenant-admin, own scope) ->
        real RLS session, sees own-tenant + global rows. tenant_id None (operator)
        -> privileged BYPASSRLS session, sees every tenant's active kills."""
        ctx = self._tenant(tenant_id) if tenant_id else self._admin()
        async with ctx as s:
            rows = (await s.execute(text(
                "SELECT * FROM kill_switches WHERE active=true ORDER BY kill_id"))).mappings().all()
        return [_kill(r) for r in rows]

    # ---- sessions ----------------------------------------------------------
    async def create_session(self, ss: Session) -> None:
        async with self._tenant(ss.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO sessions (session_id, tenant_id, user_id, agent_key, agent_version,
                     context_urn, status, created_at, last_activity_at, expires_hard_at)
                   VALUES (cast(:id as uuid), cast(:t as uuid), :u, :k, :v, :ctx, :st,
                           :ca, :la, :eh)"""),
                {"id": ss.session_id, "t": ss.tenant_id, "u": ss.user_id, "k": ss.agent_key,
                 "v": ss.agent_version, "ctx": ss.context_urn, "st": ss.status,
                 "ca": ss.created_at, "la": ss.last_activity_at, "eh": ss.expires_hard_at})

    async def get_session(self, tenant_id: str, session_id: str) -> Session | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text("SELECT * FROM sessions WHERE session_id=cast(:id as uuid)"),
                                 {"id": session_id})).mappings().first()
        return _session(r) if r else None

    async def update_session(self, ss: Session) -> None:
        async with self._tenant(ss.tenant_id) as s:
            await s.execute(text(
                """UPDATE sessions SET status=:st, last_activity_at=:la, updated_at=now()
                   WHERE session_id=cast(:id as uuid)"""),
                {"st": ss.status, "la": ss.last_activity_at, "id": ss.session_id})

    # ---- runs --------------------------------------------------------------
    async def create_run(self, r: Run) -> None:
        async with self._tenant(r.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO runs (run_id, tenant_id, session_id, agent_key, agent_version,
                     temporal_workflow_id, status, principal_type, obo_sub, parent_run_id, usage)
                   VALUES (cast(:id as uuid), cast(:t as uuid), cast(:sid as uuid), :k, :v,
                           :wf, :st, :pt, :obo, :parent, cast(:usage as jsonb))"""),
                {"id": r.run_id, "t": r.tenant_id, "sid": r.session_id, "k": r.agent_key,
                 "v": r.agent_version, "wf": r.temporal_workflow_id, "st": r.status,
                 "pt": r.principal_type, "obo": r.obo_sub, "parent": r.parent_run_id,
                 "usage": _j(r.usage)})

    async def get_run(self, tenant_id: str, run_id: str) -> Run | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text("SELECT * FROM runs WHERE run_id=cast(:id as uuid)"),
                                 {"id": run_id})).mappings().first()
        return _run(r) if r else None

    async def list_runs(
        self, tenant_id: str, *, agent_key: str | None = None, limit: int = 50
    ) -> list[Run]:
        # Tenant scoping rides on RLS via _tenant (same as get_run).
        q = "SELECT * FROM runs"
        params: dict = {"lim": limit}
        if agent_key:
            q += " WHERE agent_key=:k"
            params["k"] = agent_key
        q += " ORDER BY created_at DESC LIMIT :lim"
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(q), params)).mappings().all()
        return [_run(r) for r in rows]

    async def update_run(self, r: Run) -> None:
        async with self._tenant(r.tenant_id) as s:
            await s.execute(text(
                """UPDATE runs SET status=:st, temporal_workflow_id=:wf,
                     usage=cast(:usage as jsonb), error=cast(:err as jsonb),
                     final_text=:ft, updated_at=now()
                   WHERE run_id=cast(:id as uuid)"""),
                {"st": r.status, "wf": r.temporal_workflow_id, "usage": _j(r.usage),
                 "err": _j(r.error) if r.error else None, "ft": r.final_text,
                 "id": r.run_id})

    async def save_checkpoint(
        self, *, tenant_id: str, run_id: str, checkpoint_id: str, seq: int, state_ref: dict
    ) -> None:
        async with self._tenant(tenant_id) as s:
            await s.execute(text(
                """INSERT INTO checkpoints (run_id, checkpoint_id, tenant_id, seq, state_ref)
                   VALUES (cast(:r as uuid), :cid, cast(:t as uuid), :seq, cast(:sr as jsonb))
                   ON CONFLICT (run_id, checkpoint_id) DO UPDATE SET
                     seq=:seq, state_ref=cast(:sr as jsonb)"""),
                {"r": run_id, "cid": checkpoint_id, "t": tenant_id, "seq": seq,
                 "sr": _j(state_ref)})

    async def load_checkpoints(self, run_id: str) -> list[dict]:
        # run_id-scoped; RLS still applies via the run's tenant on the row.
        async with self._plain() as s:
            rows = (await s.execute(text(
                "SELECT checkpoint_id, seq, state_ref FROM checkpoints WHERE run_id=cast(:r as uuid) ORDER BY seq"),
                {"r": run_id})).mappings().all()
        return [{"checkpoint_id": r["checkpoint_id"], "seq": r["seq"],
                 "state_ref": _load(r["state_ref"])} for r in rows]

    # ---- proposals ---------------------------------------------------------
    async def create_proposal(self, p: Proposal) -> None:
        async with self._tenant(p.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO proposals (proposal_id, tenant_id, session_id, run_id, agent_key,
                     agent_version, obo_user, tool_id, tool_version, tier, side_effects, args,
                     rationale, affected_urns, predicted_effect, expires_at, status, workspace_id)
                   VALUES (cast(:id as uuid), cast(:t as uuid), :sid, cast(:rid as uuid), :k, :v,
                           :obo, :tid, :tv, :tier, :se, cast(:args as jsonb), :rat, :urns,
                           cast(:pe as jsonb), :exp, :st, cast(:ws as uuid))"""),
                {"id": p.proposal_id, "t": p.tenant_id,
                 "sid": p.session_id, "rid": p.run_id, "k": p.agent_key, "v": p.agent_version,
                 "obo": p.obo_user, "tid": p.tool_id, "tv": p.tool_version, "tier": p.tier,
                 "se": p.side_effects, "args": _j(p.args), "rat": p.rationale,
                 "urns": list(p.affected_urns), "pe": _j(p.predicted_effect),
                 "exp": p.expires_at, "st": p.status, "ws": p.workspace_id})

    async def get_proposal(self, tenant_id: str, proposal_id: str) -> Proposal | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT * FROM proposals WHERE proposal_id=cast(:id as uuid)"),
                {"id": proposal_id})).mappings().first()
        return _proposal(r) if r else None

    async def list_proposals(
        self, tenant_id: str, *, status: str | None = None, agent_key: str | None = None,
        resource_urns: list[str] | None = None, limit: int = 50,
    ) -> list[Proposal]:
        clauses = []
        params: dict = {"lim": limit}
        if status:
            clauses.append("status=:st")
            params["st"] = status
        if agent_key:
            clauses.append("agent_key=:k")
            params["k"] = agent_key
        if resource_urns:
            # bff filter[resource_urn]: proposals whose affected_urns overlap
            # any requested URN (GIN-indexed && overlap on text[]).
            clauses.append("affected_urns && :urns")
            params["urns"] = list(resource_urns)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(
                f"SELECT * FROM proposals{where} ORDER BY created_at DESC LIMIT :lim"),
                params)).mappings().all()
        return [_proposal(r) for r in rows]

    async def decide_proposal(
        self, *, tenant_id: str, proposal_id: str, new_status: str, decision: dict,
        decided_at: datetime,
    ) -> Proposal | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text(
                """UPDATE proposals SET status=:ns, decision=cast(:d as jsonb), updated_at=:at
                   WHERE proposal_id=cast(:id as uuid) AND status='pending'
                   RETURNING *"""),
                {"ns": new_status, "d": _j(decision), "at": decided_at,
                 "id": proposal_id})).mappings().first()
        return _proposal(r) if r else None

    async def supersede_pending(
        self, *, tenant_id: str, run_id: str, tool_id: str, urns: list[str], except_id: str
    ) -> None:
        async with self._tenant(tenant_id) as s:
            await s.execute(text(
                """UPDATE proposals SET status='superseded', updated_at=now()
                   WHERE run_id=cast(:rid as uuid) AND tool_id=:tid AND status='pending'
                     AND proposal_id <> cast(:ex as uuid) AND affected_urns && :urns"""),
                {"rid": run_id, "tid": tool_id, "ex": except_id, "urns": list(urns)})

    # ---- SLM transcript corpus (ART-FR / SLM milestone 1) ------------------
    async def record_transcript(self, t: Transcript) -> None:
        async with self._tenant(t.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO agent_transcripts
                     (transcript_id, tenant_id, run_id, session_id, agent_key, agent_version,
                      principal_type, obo_sub, inputs, grounding, final_text, proposed_action,
                      proposal_id, model, usage, consent)
                   VALUES (cast(:id as uuid), cast(:t as uuid), cast(:rid as uuid),
                      cast(:sid as uuid), :ak, :av, :pt, :obo, cast(:inp as jsonb),
                      cast(:gr as jsonb), :ft, cast(:pa as jsonb),
                      cast(:pid as uuid), :model, cast(:usage as jsonb), :consent)
                   ON CONFLICT (transcript_id) DO NOTHING"""),
                {"id": t.transcript_id, "t": t.tenant_id, "rid": t.run_id,
                 "sid": t.session_id, "ak": t.agent_key, "av": t.agent_version,
                 "pt": t.principal_type, "obo": t.obo_sub, "inp": _j(t.inputs),
                 "gr": _j(t.grounding), "ft": t.final_text,
                 "pa": _j(t.proposed_action) if t.proposed_action is not None else None,
                 "pid": t.proposal_id, "model": t.model, "usage": _j(t.usage),
                 "consent": t.consent})

    async def attach_transcript_decision(
        self, *, tenant_id: str, proposal_id: str, decision: str,
        corrected_output: dict | None, decided_by: str, decided_at: datetime,
    ) -> None:
        """Join the human decision onto the run's transcript (the training
        signal). No-op if no transcript exists for the proposal."""
        async with self._tenant(tenant_id) as s:
            await s.execute(text(
                """UPDATE agent_transcripts
                     SET decision=:d, corrected_output=cast(:co as jsonb),
                         decided_by=:by, decided_at=:at, updated_at=now()
                   WHERE proposal_id=cast(:pid as uuid)"""),
                {"d": decision,
                 "co": _j(corrected_output) if corrected_output is not None else None,
                 "by": decided_by, "at": decided_at, "pid": proposal_id})

    async def get_transcript(self, tenant_id: str, transcript_id: str) -> Transcript | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(
                text("SELECT * FROM agent_transcripts WHERE transcript_id=cast(:id as uuid)"),
                {"id": transcript_id})).mappings().first()
        return _transcript(r) if r else None

    async def list_transcripts(
        self, tenant_id: str, *, agent_key: str | None = None,
        only_decided: bool = False, limit: int = 50,
    ) -> list[Transcript]:
        q = "SELECT * FROM agent_transcripts"
        conds: list[str] = []
        params: dict = {"lim": limit}
        if agent_key:
            conds.append("agent_key=:ak")
            params["ak"] = agent_key
        if only_decided:
            conds.append("decision IS NOT NULL")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC LIMIT :lim"
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(q), params)).mappings().all()
        return [_transcript(r) for r in rows]

    # ---- SLM SFT datasets (milestone 2) ------------------------------------
    async def next_sft_version(self, tenant_id: str, agent_key: str) -> int:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM sft_datasets WHERE agent_key=:ak"),
                {"ak": agent_key})).mappings().first()
        return int(r["v"])

    async def record_sft_dataset(self, ds: SftDataset, rows: list[SftExample]) -> None:
        async with self._tenant(ds.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO sft_datasets
                     (dataset_id, tenant_id, agent_key, version, status, row_count,
                      source_count, curation_params, checksum, consent_verified, created_by)
                   VALUES (cast(:id as uuid), cast(:t as uuid), :ak, :v, :st, :rc,
                      :sc, cast(:cp as jsonb), :cs, :cv, :by)"""),
                {"id": ds.dataset_id, "t": ds.tenant_id, "ak": ds.agent_key, "v": ds.version,
                 "st": ds.status, "rc": ds.row_count, "sc": ds.source_count,
                 "cp": _j(ds.curation_params), "cs": ds.checksum, "cv": ds.consent_verified,
                 "by": ds.created_by})
            for row in rows:
                await s.execute(text(
                    """INSERT INTO sft_examples
                         (dataset_id, tenant_id, ord, messages, target_kind,
                          source_transcript_id, example_hash)
                       VALUES (cast(:did as uuid), cast(:t as uuid), :ord, cast(:m as jsonb),
                          :tk, cast(:src as uuid), :h)"""),
                    {"did": row.dataset_id, "t": row.tenant_id, "ord": row.ord,
                     "m": _j(row.messages), "tk": row.target_kind,
                     "src": row.source_transcript_id, "h": row.example_hash})

    async def get_sft_dataset(self, tenant_id: str, dataset_id: str) -> SftDataset | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(
                text("SELECT * FROM sft_datasets WHERE dataset_id=cast(:id as uuid)"),
                {"id": dataset_id})).mappings().first()
        return _sft_dataset(r) if r else None

    async def list_sft_datasets(
        self, tenant_id: str, *, agent_key: str | None = None, limit: int = 50,
    ) -> list[SftDataset]:
        q = "SELECT * FROM sft_datasets"
        params: dict = {"lim": limit}
        if agent_key:
            q += " WHERE agent_key=:ak"
            params["ak"] = agent_key
        q += " ORDER BY created_at DESC LIMIT :lim"
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(q), params)).mappings().all()
        return [_sft_dataset(r) for r in rows]

    async def list_sft_examples(
        self, tenant_id: str, dataset_id: str, *, limit: int = 1000,
    ) -> list[SftExample]:
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(
                """SELECT * FROM sft_examples WHERE dataset_id=cast(:did as uuid)
                   ORDER BY ord LIMIT :lim"""),
                {"did": dataset_id, "lim": limit})).mappings().all()
        return [_sft_example(r) for r in rows]

    # ---- SLM training jobs + adapters (milestone 3/4) ----------------------
    async def record_training_job(self, j: TrainingJob) -> None:
        async with self._tenant(j.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO slm_training_jobs
                     (job_id, tenant_id, archetype, sft_dataset_id, base_model, status,
                      params, mlflow_run_ref, adapter_id, error, created_by, started_at, finished_at)
                   VALUES (cast(:id as uuid), cast(:t as uuid), :ak, cast(:ds as uuid), :bm, :st,
                      cast(:p as jsonb), :mr, cast(:ad as uuid), cast(:err as jsonb), :by, :sa, :fa)"""),
                {"id": j.job_id, "t": j.tenant_id, "ak": j.archetype, "ds": j.sft_dataset_id,
                 "bm": j.base_model, "st": j.status, "p": _j(j.params), "mr": j.mlflow_run_ref,
                 "ad": j.adapter_id, "err": _j(j.error) if j.error is not None else None,
                 "by": j.created_by, "sa": j.started_at, "fa": j.finished_at})

    async def update_training_job(self, j: TrainingJob) -> None:
        async with self._tenant(j.tenant_id) as s:
            await s.execute(text(
                """UPDATE slm_training_jobs SET status=:st, mlflow_run_ref=:mr,
                     adapter_id=cast(:ad as uuid), error=cast(:err as jsonb),
                     started_at=:sa, finished_at=:fa, updated_at=now()
                   WHERE job_id=cast(:id as uuid)"""),
                {"id": j.job_id, "st": j.status, "mr": j.mlflow_run_ref, "ad": j.adapter_id,
                 "err": _j(j.error) if j.error is not None else None,
                 "sa": j.started_at, "fa": j.finished_at})

    async def get_training_job(self, tenant_id: str, job_id: str) -> TrainingJob | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(
                text("SELECT * FROM slm_training_jobs WHERE job_id=cast(:id as uuid)"),
                {"id": job_id})).mappings().first()
        return _training_job(r) if r else None

    async def list_training_jobs(
        self, tenant_id: str, *, archetype: str | None = None, limit: int = 50,
    ) -> list[TrainingJob]:
        q = "SELECT * FROM slm_training_jobs"
        params: dict = {"lim": limit}
        if archetype:
            q += " WHERE archetype=:ak"
            params["ak"] = archetype
        q += " ORDER BY created_at DESC LIMIT :lim"
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(q), params)).mappings().all()
        return [_training_job(r) for r in rows]

    async def record_slm_adapter(self, a: SlmAdapter) -> None:
        async with self._tenant(a.tenant_id) as s:
            await s.execute(text(
                """INSERT INTO slm_adapters
                     (adapter_id, tenant_id, training_job_id, archetype, base_model,
                      adapter_uri, checksum, model_alias, promotion_status, eval_result_ref, target_rung_alias)
                   VALUES (cast(:id as uuid), cast(:t as uuid), cast(:jid as uuid), :ak, :bm,
                      :uri, :cs, :ma, :ps, :er, :tr)"""),
                {"id": a.adapter_id, "t": a.tenant_id, "jid": a.training_job_id, "ak": a.archetype,
                 "bm": a.base_model, "uri": a.adapter_uri, "cs": a.checksum, "ma": a.model_alias,
                 "ps": a.promotion_status, "er": a.eval_result_ref, "tr": a.target_rung_alias})

    async def update_slm_adapter(self, a: SlmAdapter) -> None:
        async with self._tenant(a.tenant_id) as s:
            await s.execute(text(
                """UPDATE slm_adapters SET promotion_status=:ps, eval_result_ref=:er,
                     target_rung_alias=:tr, updated_at=now()
                   WHERE adapter_id=cast(:id as uuid)"""),
                {"id": a.adapter_id, "ps": a.promotion_status, "er": a.eval_result_ref,
                 "tr": a.target_rung_alias})

    async def get_slm_adapter(self, tenant_id: str, adapter_id: str) -> SlmAdapter | None:
        async with self._tenant(tenant_id) as s:
            r = (await s.execute(
                text("SELECT * FROM slm_adapters WHERE adapter_id=cast(:id as uuid)"),
                {"id": adapter_id})).mappings().first()
        return _slm_adapter(r) if r else None

    async def list_slm_adapters(
        self, tenant_id: str, *, archetype: str | None = None, limit: int = 50,
    ) -> list[SlmAdapter]:
        q = "SELECT * FROM slm_adapters"
        params: dict = {"lim": limit}
        if archetype:
            q += " WHERE archetype=:ak"
            params["ak"] = archetype
        q += " ORDER BY created_at DESC LIMIT :lim"
        async with self._tenant(tenant_id) as s:
            rows = (await s.execute(text(q), params)).mappings().all()
        return [_slm_adapter(r) for r in rows]

    # ---- outbox ------------------------------------------------------------
    async def enqueue_outbox(self, *, tenant_id: str, topic: str, envelope: dict) -> None:
        async with self._tenant(tenant_id) as s:
            await s.execute(text(
                """INSERT INTO outbox (id, tenant_id, topic, payload)
                   VALUES (cast(:id as uuid), cast(:t as uuid), :top, cast(:p as jsonb))"""),
                {"id": new_uuid(), "t": tenant_id, "top": topic, "p": _j(envelope)})


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes them to Kafka
    (MASTER-FR-034 transactional outbox; same relay pattern as
    pipeline-orchestrator's OutboxDispatcher). Runs under the permissive
    ``worker_outbox`` RLS policy (``app.worker`` GUC), locking rows with
    FOR UPDATE SKIP LOCKED so multiple replicas never double-publish."""

    def __init__(self, session_factory: async_sessionmaker, bus, batch_size: int = 100):
        self._sf = session_factory
        self._bus = bus
        self._batch = batch_size

    async def run_once(self) -> int:
        async with self._sf() as session:
            await session.execute(
                text("SELECT set_config('app.worker', 'true', true)"))
            rows = (await session.execute(text(
                """SELECT id, topic, payload FROM outbox
                   WHERE published_at IS NULL
                   ORDER BY occurred_at ASC
                   LIMIT :lim
                   FOR UPDATE SKIP LOCKED"""),
                {"lim": self._batch})).mappings().all()
            for row in rows:
                await self._bus.publish(row["topic"], _load(row["payload"]))
            if rows:
                await session.execute(text(
                    "UPDATE outbox SET published_at = now() WHERE id = ANY(:ids)"),
                    {"ids": [row["id"] for row in rows]})
            await session.commit()
            return len(rows)


# ---- row mappers ------------------------------------------------------------
def _outcome_label(r):
    from app.domain.outcomes import OutcomeLabel
    return OutcomeLabel(
        label_id=str(r["id"]), tenant_id=r["tenant_id"], decision_ref=r["decision_ref"],
        decision_type=r["decision_type"], realized_outcome=r["realized_outcome"],
        decided_outcome=r.get("decided_outcome"), correct=r.get("correct"),
        label_source=r["label_source"], note=r.get("note"),
        labeled_by=r.get("labeled_by"), producer=r.get("producer"))


def _decision_model(r):
    from app.domain.decisions import DecisionModel, parse_outcome, rules_from_json
    return DecisionModel(
        model_id=str(r["id"]), tenant_id=r["tenant_id"], name=r["name"],
        version=int(r["version"]), rules=rules_from_json(r["rules"]),
        default_outcome=parse_outcome(r["default_outcome"]),
        workspace_id=r.get("workspace_id"), dataset_urn=r.get("dataset_urn"),
        status=r["status"])


def _def(r) -> AgentDefinition:
    return AgentDefinition(agent_key=r["agent_key"], display_name=r["display_name"],
                           description=r["description"], owner_team=r["owner_team"],
                           default_write_mode=r["default_write_mode"], status=r["status"],
                           owner_tenant=r.get("owner_tenant"))


def _ver(r) -> AgentVersion:
    return AgentVersion(
        agent_key=r["agent_key"], version=int(r["version"]), graph_ref=r["graph_ref"],
        graph_digest=r["graph_digest"], prompt_refs=_load(r["prompt_refs"]) or [],
        toolset=_load(r["toolset"]) or [], model_config=_load(r["model_config"]) or {},
        guardrail_profile=r["guardrail_profile"], memory_policy=_load(r["memory_policy"]) or {},
        eval_gate=_load(r["eval_gate"]) or {}, eval_gate_result_id=r["eval_gate_result_id"],
        a2a_card=_load(r["a2a_card"]) or {}, card_signature=r["card_signature"],
        principal_ref=r["principal_ref"], status=r["status"])


def _cfg(r) -> TenantAgentConfig:
    return TenantAgentConfig(
        tenant_id=str(r["tenant_id"]), agent_key=r["agent_key"], enabled=r["enabled"],
        pinned_version=r["pinned_version"], prompt_params=_load(r["prompt_params"]) or {},
        auto_execute_policy=_load(r["auto_execute_policy"]) or {}, self_approval=r["self_approval"])


def _rollout(r) -> Rollout:
    return Rollout(rollout_id=str(r["rollout_id"]), agent_key=r["agent_key"], cell=r["cell"],
                   mode=r["mode"], candidate_version=int(r["candidate_version"]),
                   baseline_version=int(r["baseline_version"]), pct=int(r["pct"]),
                   tenant_filter=_load(r["tenant_filter"]) or {}, status=r["status"])


def _kill(r) -> KillSwitch:
    return KillSwitch(kill_id=str(r["kill_id"]), scope=r["scope"], agent_key=r["agent_key"],
                      version=r["version"], tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None,
                      active=r["active"], reason=r["reason"], set_by=r["set_by"],
                      created_at=r["created_at"])


def _session(r) -> Session:
    return Session(session_id=str(r["session_id"]), tenant_id=str(r["tenant_id"]),
                   user_id=r["user_id"], agent_key=r["agent_key"],
                   agent_version=int(r["agent_version"]), context_urn=r["context_urn"],
                   status=r["status"], created_at=r["created_at"],
                   last_activity_at=r["last_activity_at"], expires_hard_at=r["expires_hard_at"])


def _run(r) -> Run:
    return Run(run_id=str(r["run_id"]), tenant_id=str(r["tenant_id"]),
               session_id=str(r["session_id"]), agent_key=r["agent_key"],
               agent_version=int(r["agent_version"]), temporal_workflow_id=r["temporal_workflow_id"],
               status=r["status"], principal_type=r["principal_type"], obo_sub=r["obo_sub"],
               parent_run_id=str(r["parent_run_id"]) if r["parent_run_id"] else None,
               usage=_load(r["usage"]) or {}, error=_load(r["error"]),
               final_text=r["final_text"],
               created_at=r["created_at"], updated_at=r["updated_at"])


def _transcript(r) -> Transcript:
    return Transcript(
        transcript_id=str(r["transcript_id"]), tenant_id=str(r["tenant_id"]),
        run_id=str(r["run_id"]),
        session_id=str(r["session_id"]) if r["session_id"] else None,
        agent_key=r["agent_key"], agent_version=int(r["agent_version"]),
        principal_type=r["principal_type"], obo_sub=r["obo_sub"],
        inputs=_load(r["inputs"]) or {}, grounding=_load(r["grounding"]) or {},
        final_text=r["final_text"], proposed_action=_load(r["proposed_action"]),
        proposal_id=str(r["proposal_id"]) if r["proposal_id"] else None,
        model=r["model"], usage=_load(r["usage"]) or {}, consent=bool(r["consent"]),
        decision=r["decision"], corrected_output=_load(r["corrected_output"]),
        decided_by=r["decided_by"], decided_at=r["decided_at"],
        created_at=r["created_at"], updated_at=r["updated_at"])


def _sft_dataset(r) -> SftDataset:
    return SftDataset(
        dataset_id=str(r["dataset_id"]), tenant_id=str(r["tenant_id"]),
        agent_key=r["agent_key"], version=int(r["version"]), status=r["status"],
        row_count=int(r["row_count"]), source_count=int(r["source_count"]),
        curation_params=_load(r["curation_params"]) or {}, checksum=r["checksum"],
        consent_verified=bool(r["consent_verified"]), created_by=r["created_by"],
        created_at=r["created_at"], updated_at=r["updated_at"])


def _sft_example(r) -> SftExample:
    return SftExample(
        dataset_id=str(r["dataset_id"]), tenant_id=str(r["tenant_id"]),
        ord=int(r["ord"]), messages=_load(r["messages"]) or [],
        target_kind=r["target_kind"],
        source_transcript_id=str(r["source_transcript_id"]) if r["source_transcript_id"] else None,
        example_hash=r["example_hash"], created_at=r["created_at"])


def _training_job(r) -> TrainingJob:
    return TrainingJob(
        job_id=str(r["job_id"]), tenant_id=str(r["tenant_id"]), archetype=r["archetype"],
        sft_dataset_id=str(r["sft_dataset_id"]), base_model=r["base_model"], status=r["status"],
        params=_load(r["params"]) or {}, mlflow_run_ref=r["mlflow_run_ref"],
        adapter_id=str(r["adapter_id"]) if r["adapter_id"] else None,
        error=_load(r["error"]), created_by=r["created_by"],
        created_at=r["created_at"], updated_at=r["updated_at"],
        started_at=r["started_at"], finished_at=r["finished_at"])


def _slm_adapter(r) -> SlmAdapter:
    return SlmAdapter(
        adapter_id=str(r["adapter_id"]), tenant_id=str(r["tenant_id"]),
        training_job_id=str(r["training_job_id"]), archetype=r["archetype"],
        base_model=r["base_model"], adapter_uri=r["adapter_uri"], checksum=r["checksum"],
        model_alias=r["model_alias"], promotion_status=r["promotion_status"],
        eval_result_ref=r["eval_result_ref"], target_rung_alias=r["target_rung_alias"],
        created_at=r["created_at"], updated_at=r["updated_at"])


def _proposal(r) -> Proposal:
    return Proposal(
        proposal_id=str(r["proposal_id"]), tenant_id=str(r["tenant_id"]),
        session_id=str(r["session_id"]) if r["session_id"] else None,
        run_id=str(r["run_id"]), agent_key=r["agent_key"], agent_version=int(r["agent_version"]),
        obo_user=r["obo_user"], tool_id=r["tool_id"], tool_version=r["tool_version"],
        tier=r["tier"], side_effects=r["side_effects"], args=_load(r["args"]) or {},
        rationale=r["rationale"], affected_urns=list(r["affected_urns"] or []),
        predicted_effect=_load(r["predicted_effect"]) or {}, expires_at=r["expires_at"],
        status=r["status"], decision=_load(r["decision"]),
        workspace_id=str(r["workspace_id"]) if r["workspace_id"] else None,
        created_at=r["created_at"], updated_at=r["updated_at"])
