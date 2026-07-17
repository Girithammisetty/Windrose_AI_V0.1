"""SLM distillation training control plane (milestone 3/4).

Submit a distillation run against a versioned SFT dataset -> track its lifecycle
-> (eval-gate) -> promote the resulting adapter to the tenant's cheapest ladder
rung. The DB/API/lifecycle here are fully real; the GPU LoRA compute runs behind
the ``GpuTrainer`` port and fails honestly (GpuTrainerNotConfigured) with no GPU
wired — a submitted job then lands in ``failed`` with reason
``gpu_trainer_not_configured`` rather than a fabricated adapter (Rule 2).
"""

from __future__ import annotations

import json

from app.domain import archetypes
from app.domain.entities import (
    ADAPTER_PROMOTION_STATUSES,
    SlmAdapter,
    TrainingJob,
    new_uuid,
    now,
)
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.ports import GpuTrainer, GpuTrainerNotConfigured, TrainingSpec


class TrainingJobService:
    def __init__(self, store, trainer: GpuTrainer) -> None:
        self._store = store
        self._trainer = trainer

    async def submit(
        self, *, tenant_id: str, agent_key: str, sft_dataset_id: str,
        base_model: str | None = None, params: dict | None = None, created_by: str | None = None,
    ) -> TrainingJob:
        """Submit a distillation run. Validates the versioned SFT dataset exists,
        resolves the archetype's student base, then hands the frozen corpus to
        the GPU trainer. On a stack with no trainer backend the job is recorded
        and immediately marked ``failed`` with an honest reason."""
        try:
            arch = archetypes.resolve_archetype(agent_key, base_model=base_model)
        except ValueError as e:
            raise ValidationFailed(str(e)) from e

        ds = await self._store.get_sft_dataset(tenant_id, sft_dataset_id)
        if ds is None:
            raise NotFound("sft dataset not found")
        if ds.agent_key != agent_key:
            raise ValidationFailed(
                f"sft dataset is for archetype {ds.agent_key!r}, not {agent_key!r}")
        if ds.row_count <= 0:
            raise ValidationFailed("sft dataset has no training rows")

        examples = await self._store.list_sft_examples(tenant_id, sft_dataset_id, limit=100000)
        jsonl = "\n".join(json.dumps({"messages": e.messages}, ensure_ascii=False) for e in examples)

        job = TrainingJob(
            job_id=new_uuid(), tenant_id=tenant_id, archetype=agent_key,
            sft_dataset_id=sft_dataset_id, base_model=arch.base_model, status="running",
            params=params or {}, created_by=created_by, started_at=now())
        await self._store.record_training_job(job)

        try:
            result = await self._trainer.train(TrainingSpec(
                tenant_id=tenant_id, archetype=agent_key, base_model=arch.base_model,
                sft_dataset_id=sft_dataset_id, sft_examples_jsonl=jsonl, params=params or {}))
        except GpuTrainerNotConfigured as e:
            job.status = "failed"
            job.error = {"reason": "gpu_trainer_not_configured", "detail": str(e)}
            job.finished_at = now()
            await self._store.update_training_job(job)
            return job

        adapter = SlmAdapter(
            adapter_id=new_uuid(), tenant_id=tenant_id, training_job_id=job.job_id,
            archetype=agent_key, base_model=arch.base_model, adapter_uri=result.adapter_uri,
            checksum=result.checksum, model_alias=arch.model_alias, promotion_status="candidate")
        await self._store.record_slm_adapter(adapter)

        job.status = "succeeded"
        job.adapter_id = adapter.adapter_id
        job.mlflow_run_ref = result.mlflow_run_ref
        job.finished_at = now()
        await self._store.update_training_job(job)
        return job

    async def promote(
        self, *, tenant_id: str, adapter_id: str, eval_result_ref: str | None = None,
    ) -> SlmAdapter:
        """Promote a distilled adapter to the tenant's ladder rung (milestone 4).
        Requires a real artifact (adapter_uri) and a candidate/gated status; the
        eval-gate result that cleared it is recorded. NOTE: registering the rung
        in ai-gateway (POST /providers + PUT /ladders) is a thin follow-up — it
        needs a served adapter endpoint, which only exists once real GPU training
        has produced one."""
        a = await self._store.get_slm_adapter(tenant_id, adapter_id)
        if a is None:
            raise NotFound("adapter not found")
        if a.promotion_status not in ("candidate", "gated"):
            raise Conflict(f"adapter is {a.promotion_status}, not promotable")
        if not a.adapter_uri:
            raise Conflict("adapter has no trained artifact to serve")
        a.promotion_status = "promoted"
        a.target_rung_alias = a.model_alias
        a.eval_result_ref = eval_result_ref
        await self._store.update_slm_adapter(a)
        return a

    async def demote(self, *, tenant_id: str, adapter_id: str) -> SlmAdapter:
        """Roll back a promoted rung (design §M4 rollback)."""
        a = await self._store.get_slm_adapter(tenant_id, adapter_id)
        if a is None:
            raise NotFound("adapter not found")
        if a.promotion_status != "promoted":
            raise Conflict(f"adapter is {a.promotion_status}, not demotable")
        a.promotion_status = "demoted"
        await self._store.update_slm_adapter(a)
        return a


_ = ADAPTER_PROMOTION_STATUSES  # documents the state space (used by tests)
