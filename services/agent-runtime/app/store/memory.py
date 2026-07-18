"""In-memory store — unit-tier double ONLY (never wired from app.main).

Mirrors the SqlStore method surface so services are storage-agnostic. Enforces
tenant isolation (cross-tenant reads return None) and first-wins proposal
decisions, so the unit tier can exercise BR-11/BR-12/AC-14 without Postgres.
"""

from __future__ import annotations

import copy
from datetime import datetime

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
    now,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._defs: dict[str, AgentDefinition] = {}
        self._versions: dict[tuple[str, int], AgentVersion] = {}
        self._configs: dict[tuple[str, str], TenantAgentConfig] = {}
        self._rollouts: dict[str, Rollout] = {}
        self._kills: dict[str, KillSwitch] = {}
        self._sessions: dict[str, Session] = {}
        self._runs: dict[str, Run] = {}
        self._proposals: dict[str, Proposal] = {}
        self._transcripts: dict[str, Transcript] = {}
        self._sft_datasets: dict[str, SftDataset] = {}
        self._sft_examples: dict[str, list[SftExample]] = {}
        self._training_jobs: dict[str, TrainingJob] = {}
        self._slm_adapters: dict[str, SlmAdapter] = {}
        self._checkpoints: dict[str, list[dict]] = {}
        self._decision_models: dict[str, object] = {}  # BRD 54
        self._outcome_labels: dict[tuple, object] = {}  # BRD 55
        self._retrain_watches: dict[str, object] = {}  # BRD 52 inc3
        self.outbox: list[dict] = []

    # ---- decision models (BRD 54) ------------------------------------------
    async def create_decision_model(self, m) -> None:
        import copy as _c
        self._decision_models[m.model_id] = _c.copy(m)

    async def get_decision_model(self, tenant_id: str, model_id: str):
        m = self._decision_models.get(model_id)
        return m if m and m.tenant_id == tenant_id else None

    async def list_decision_models(self, tenant_id: str) -> list:
        return [m for m in self._decision_models.values() if m.tenant_id == tenant_id]

    async def list_decision_model_versions(self, tenant_id: str, name: str,
                                           workspace_id: str | None) -> list:
        out = [m for m in self._decision_models.values()
               if m.tenant_id == tenant_id and m.name == name
               and m.workspace_id == workspace_id]
        return sorted(out, key=lambda m: m.version, reverse=True)

    async def approve_decision_model(self, tenant_id: str, model_id: str,
                                     approver: str) -> None:
        from datetime import UTC, datetime
        m = self._decision_models.get(model_id)
        if m is None or m.tenant_id != tenant_id:
            return
        for other in self._decision_models.values():
            if (other.tenant_id == tenant_id and other.name == m.name
                    and other.workspace_id == m.workspace_id
                    and other.status == "published"):
                other.status = "archived"
        m.status = "published"
        m.approved_by = approver
        m.approved_at = datetime.now(UTC).isoformat()

    # ---- outcome labels (BRD 55) -------------------------------------------
    async def upsert_outcome_label(self, lab) -> None:
        import copy as _c
        self._outcome_labels[(lab.tenant_id, lab.decision_ref)] = _c.copy(lab)

    async def list_outcome_labels(self, tenant_id: str, *, decision_type=None) -> list:
        out = [lbl for (t, _), lbl in self._outcome_labels.items() if t == tenant_id]
        if decision_type:
            out = [lbl for lbl in out if lbl.decision_type == decision_type]
        return out

    async def get_outcome_label(self, tenant_id: str, decision_ref: str):
        return self._outcome_labels.get((tenant_id, decision_ref))

    async def connect(self) -> None:  # parity with SqlStore
        return None

    # ---- agent registry ----------------------------------------------------
    async def upsert_agent_definition(self, d: AgentDefinition) -> None:
        self._defs[d.agent_key] = copy.copy(d)

    async def get_agent_definition(self, agent_key: str) -> AgentDefinition | None:
        return self._defs.get(agent_key)

    async def list_agent_definitions(self) -> list[AgentDefinition]:
        return list(self._defs.values())

    async def create_agent_version(self, v: AgentVersion) -> None:
        self._versions[(v.agent_key, v.version)] = copy.copy(v)

    async def get_agent_version(self, agent_key: str, version: int) -> AgentVersion | None:
        return self._versions.get((agent_key, version))

    # ---- retrain watches (BRD 52 inc3) -------------------------------------
    async def create_retrain_watch(self, w) -> None:
        self._retrain_watches[w.id] = copy.copy(w)

    async def list_retrain_watches(self, tenant_id: str) -> list:
        return [w for w in self._retrain_watches.values() if w.tenant_id == tenant_id]

    async def delete_retrain_watch(self, tenant_id: str, watch_id: str) -> bool:
        w = self._retrain_watches.get(watch_id)
        if w is not None and w.tenant_id == tenant_id:
            del self._retrain_watches[watch_id]
            return True
        return False

    async def list_due_retrain_watches(self, now_ts, limit: int = 100) -> list:
        from datetime import timedelta
        due = []
        for w in self._retrain_watches.values():
            if not w.enabled:
                continue
            if w.last_checked_at is None or (
                    w.last_checked_at + timedelta(seconds=w.cadence_seconds) <= now_ts):
                due.append(w)
        return due[:limit]

    async def touch_retrain_watch(self, watch_id: str, checked_at, signal: dict) -> None:
        w = self._retrain_watches.get(watch_id)
        if w is not None:
            w.last_checked_at = checked_at
            w.last_signal = dict(signal)

    async def count_corrections(self, tenant_id: str, agent_key: str, since) -> tuple[int, int]:
        corr = total = 0
        for p in self._proposals.values():
            if getattr(p, "tenant_id", None) != tenant_id or getattr(p, "agent_key", None) != agent_key:  # noqa: E501
                continue
            st = getattr(p, "status", None)
            if st in ("rejected", "edited_approved"):
                corr += 1
                total += 1
            elif st == "approved":
                total += 1
        return corr, total

    async def list_agent_versions(self, agent_key: str) -> list[AgentVersion]:
        return [v for (k, _), v in self._versions.items() if k == agent_key]

    async def update_agent_version(self, v: AgentVersion) -> None:
        self._versions[(v.agent_key, v.version)] = copy.copy(v)

    async def latest_published_version(self, agent_key: str) -> int | None:
        pubs = [v.version for (k, _), v in self._versions.items()
                if k == agent_key and v.status == "published"]
        return max(pubs) if pubs else None

    # ---- tenant config -----------------------------------------------------
    async def get_tenant_config(self, tenant_id: str, agent_key: str) -> TenantAgentConfig | None:
        return self._configs.get((tenant_id, agent_key))

    async def put_tenant_config(self, c: TenantAgentConfig) -> None:
        self._configs[(c.tenant_id, c.agent_key)] = copy.copy(c)

    # ---- platform agent ceilings (BRD 53 inc3) -----------------------------
    async def get_platform_ceilings(self) -> dict:
        return dict(getattr(self, "_ceilings", None)
                    or {"max_budget_tokens": 200000, "max_tier": "write-proposal"})

    async def set_platform_ceilings(self, *, max_budget_tokens: int, max_tier: str,
                                    updated_by: str | None = None) -> None:
        self._ceilings = {"max_budget_tokens": max_budget_tokens, "max_tier": max_tier,
                          "updated_by": updated_by}

    # ---- rollouts ----------------------------------------------------------
    async def create_rollout(self, r: Rollout) -> None:
        self._rollouts[r.rollout_id] = copy.copy(r)

    async def get_rollout(self, rollout_id: str) -> Rollout | None:
        return self._rollouts.get(rollout_id)

    async def active_rollout(self, agent_key: str, cell: str) -> Rollout | None:
        for r in self._rollouts.values():
            if r.agent_key == agent_key and r.cell == cell and r.status == "active":
                return r
        return None

    async def update_rollout(self, r: Rollout) -> None:
        self._rollouts[r.rollout_id] = copy.copy(r)

    # ---- kill switches -----------------------------------------------------
    async def create_kill_switch(self, ks: KillSwitch) -> None:
        ks = copy.copy(ks)
        ks.created_at = ks.created_at or now()
        self._kills[ks.kill_id] = ks

    async def get_kill_switch(self, kill_id: str) -> KillSwitch | None:
        return self._kills.get(kill_id)

    async def deactivate_kill_switch(self, kill_id: str) -> None:
        if kill_id in self._kills:
            self._kills[kill_id].active = False

    async def list_kill_switches(self, tenant_id: str | None) -> list[KillSwitch]:
        """Mirrors SqlStore's visibility rule: tenant_id given -> that tenant's
        own rows + global (tenant_id is None) rows; tenant_id None (operator) ->
        every active kill."""
        out = [k for k in self._kills.values() if k.active]
        if tenant_id is not None:
            out = [k for k in out if k.tenant_id is None or k.tenant_id == tenant_id]
        return sorted(out, key=lambda k: k.kill_id)

    # ---- sessions ----------------------------------------------------------
    async def create_session(self, s: Session) -> None:
        self._sessions[s.session_id] = copy.copy(s)

    async def get_session(self, tenant_id: str, session_id: str) -> Session | None:
        s = self._sessions.get(session_id)
        if s is None or s.tenant_id != tenant_id:
            return None  # cross-tenant → not found (AC-14)
        return s

    async def update_session(self, s: Session) -> None:
        self._sessions[s.session_id] = copy.copy(s)

    # ---- runs --------------------------------------------------------------
    async def create_run(self, r: Run) -> None:
        self._runs[r.run_id] = copy.copy(r)

    async def get_run(self, tenant_id: str, run_id: str) -> Run | None:
        r = self._runs.get(run_id)
        if r is None or r.tenant_id != tenant_id:
            return None
        return r

    async def update_run(self, r: Run) -> None:
        r.updated_at = now()
        self._runs[r.run_id] = copy.copy(r)

    async def list_runs(
        self, tenant_id: str, *, agent_key: str | None = None, limit: int = 50
    ) -> list[Run]:
        out = [r for r in self._runs.values() if r.tenant_id == tenant_id]
        if agent_key:
            out = [r for r in out if r.agent_key == agent_key]
        return sorted(out, key=lambda r: r.created_at, reverse=True)[:limit]

    async def save_checkpoint(
        self, *, tenant_id: str, run_id: str, checkpoint_id: str, seq: int, state_ref: dict
    ) -> None:
        self._checkpoints.setdefault(run_id, []).append(
            {"checkpoint_id": checkpoint_id, "seq": seq, "state_ref": state_ref}
        )

    async def load_checkpoints(self, run_id: str) -> list[dict]:
        return sorted(self._checkpoints.get(run_id, []), key=lambda c: c["seq"])

    # ---- proposals ---------------------------------------------------------
    async def create_proposal(self, p: Proposal) -> None:
        self._proposals[p.proposal_id] = copy.copy(p)

    async def get_proposal(self, tenant_id: str, proposal_id: str) -> Proposal | None:
        p = self._proposals.get(proposal_id)
        if p is None or p.tenant_id != tenant_id:
            return None
        return p

    async def get_proposal_unscoped(self, proposal_id: str) -> Proposal | None:
        return self._proposals.get(proposal_id)

    async def list_proposals(
        self, tenant_id: str, *, status: str | None = None, agent_key: str | None = None,
        resource_urns: list[str] | None = None, limit: int = 50,
    ) -> list[Proposal]:
        out = [p for p in self._proposals.values() if p.tenant_id == tenant_id]
        if status:
            out = [p for p in out if p.status == status]
        if agent_key:
            out = [p for p in out if p.agent_key == agent_key]
        if resource_urns:
            wanted = set(resource_urns)
            out = [p for p in out if wanted & set(p.affected_urns)]
        out.sort(key=lambda p: p.created_at, reverse=True)
        return out[:limit]

    async def decide_proposal(
        self, *, tenant_id: str, proposal_id: str, new_status: str, decision: dict,
        decided_at: datetime,
    ) -> Proposal | None:
        """Atomic first-wins: only transitions a PENDING proposal. Returns the
        updated proposal on success, None if it was already decided (BR-12)."""
        p = self._proposals.get(proposal_id)
        if p is None or p.tenant_id != tenant_id or p.status != "pending":
            return None
        p.status = new_status
        p.decision = decision
        p.updated_at = decided_at
        return copy.copy(p)

    # ---- SLM transcript corpus (milestone 1) -------------------------------
    async def record_transcript(self, t: Transcript) -> None:
        self._transcripts.setdefault(t.transcript_id, copy.copy(t))

    async def attach_transcript_decision(
        self, *, tenant_id: str, proposal_id: str, decision: str,
        corrected_output: dict | None, decided_by: str, decided_at: datetime,
    ) -> None:
        for t in self._transcripts.values():
            if t.tenant_id == tenant_id and t.proposal_id == proposal_id:
                t.decision = decision
                t.corrected_output = corrected_output
                t.decided_by = decided_by
                t.decided_at = decided_at
                t.updated_at = decided_at

    async def get_transcript(self, tenant_id: str, transcript_id: str) -> Transcript | None:
        t = self._transcripts.get(transcript_id)
        if t is None or t.tenant_id != tenant_id:
            return None
        return copy.copy(t)

    async def list_transcripts(
        self, tenant_id: str, *, agent_key: str | None = None,
        only_decided: bool = False, limit: int = 50,
    ) -> list[Transcript]:
        out = [t for t in self._transcripts.values() if t.tenant_id == tenant_id]
        if agent_key:
            out = [t for t in out if t.agent_key == agent_key]
        if only_decided:
            out = [t for t in out if t.decision is not None]
        out.sort(key=lambda t: t.created_at, reverse=True)
        return [copy.copy(t) for t in out[:limit]]

    # ---- SLM SFT datasets (milestone 2) ------------------------------------
    async def next_sft_version(self, tenant_id: str, agent_key: str) -> int:
        existing = [d.version for d in self._sft_datasets.values()
                    if d.tenant_id == tenant_id and d.agent_key == agent_key]
        return (max(existing) + 1) if existing else 1

    async def record_sft_dataset(self, ds: SftDataset, rows: list[SftExample]) -> None:
        self._sft_datasets[ds.dataset_id] = copy.copy(ds)
        self._sft_examples[ds.dataset_id] = [copy.copy(r) for r in rows]

    async def get_sft_dataset(self, tenant_id: str, dataset_id: str) -> SftDataset | None:
        d = self._sft_datasets.get(dataset_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return copy.copy(d)

    async def list_sft_datasets(
        self, tenant_id: str, *, agent_key: str | None = None, limit: int = 50,
    ) -> list[SftDataset]:
        out = [d for d in self._sft_datasets.values() if d.tenant_id == tenant_id]
        if agent_key:
            out = [d for d in out if d.agent_key == agent_key]
        out.sort(key=lambda d: d.created_at, reverse=True)
        return [copy.copy(d) for d in out[:limit]]

    async def list_sft_examples(
        self, tenant_id: str, dataset_id: str, *, limit: int = 1000,
    ) -> list[SftExample]:
        d = self._sft_datasets.get(dataset_id)
        if d is None or d.tenant_id != tenant_id:
            return []
        return [copy.copy(r) for r in self._sft_examples.get(dataset_id, [])[:limit]]

    # ---- SLM training jobs + adapters (milestone 3/4) ----------------------
    async def record_training_job(self, j: TrainingJob) -> None:
        self._training_jobs[j.job_id] = copy.copy(j)

    async def update_training_job(self, j: TrainingJob) -> None:
        self._training_jobs[j.job_id] = copy.copy(j)

    async def get_training_job(self, tenant_id: str, job_id: str) -> TrainingJob | None:
        j = self._training_jobs.get(job_id)
        return copy.copy(j) if j and j.tenant_id == tenant_id else None

    async def list_training_jobs(
        self, tenant_id: str, *, archetype: str | None = None, limit: int = 50,
    ) -> list[TrainingJob]:
        out = [j for j in self._training_jobs.values() if j.tenant_id == tenant_id]
        if archetype:
            out = [j for j in out if j.archetype == archetype]
        out.sort(key=lambda j: j.created_at, reverse=True)
        return [copy.copy(j) for j in out[:limit]]

    async def record_slm_adapter(self, a: SlmAdapter) -> None:
        self._slm_adapters[a.adapter_id] = copy.copy(a)

    async def update_slm_adapter(self, a: SlmAdapter) -> None:
        self._slm_adapters[a.adapter_id] = copy.copy(a)

    async def get_slm_adapter(self, tenant_id: str, adapter_id: str) -> SlmAdapter | None:
        a = self._slm_adapters.get(adapter_id)
        return copy.copy(a) if a and a.tenant_id == tenant_id else None

    async def list_slm_adapters(
        self, tenant_id: str, *, archetype: str | None = None, limit: int = 50,
    ) -> list[SlmAdapter]:
        out = [a for a in self._slm_adapters.values() if a.tenant_id == tenant_id]
        if archetype:
            out = [a for a in out if a.archetype == archetype]
        out.sort(key=lambda a: a.created_at, reverse=True)
        return [copy.copy(a) for a in out[:limit]]

    async def supersede_pending(
        self, *, tenant_id: str, run_id: str, tool_id: str, urns: list[str],
        except_id: str,
    ) -> None:
        for p in self._proposals.values():
            if (p.tenant_id == tenant_id and p.run_id == run_id and p.tool_id == tool_id
                    and p.status == "pending" and p.proposal_id != except_id
                    and set(p.affected_urns) & set(urns)):
                p.status = "superseded"

    # ---- outbox ------------------------------------------------------------
    async def enqueue_outbox(self, *, tenant_id: str, topic: str, envelope: dict) -> None:
        self.outbox.append({"tenant_id": tenant_id, "topic": topic, "payload": envelope})
