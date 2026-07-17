"""SLM distillation milestone 3/4: training-job control plane + honest GPU gate."""

from __future__ import annotations

import pytest

from app.adapters.trainer import FakeGpuTrainer, UnconfiguredGpuTrainer, build_trainer
from app.domain import archetypes
from app.domain.entities import Transcript, new_uuid
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.ports import GpuTrainerNotConfigured, TrainingSpec
from app.domain.sft_curation import SftCurator
from app.domain.training import TrainingJobService
from app.store.memory import InMemoryStore

TENANT = "11111111-1111-1111-1111-111111111111"


def _t(*, decision, corrected=None, agent="triage"):
    return Transcript(
        transcript_id=new_uuid(), tenant_id=TENANT, run_id=new_uuid(), session_id=None,
        agent_key=agent, agent_version=1, principal_type="user_obo", obo_sub="u",
        inputs={"case_id": "C1", "amount": 100}, grounding={"evidence": [{"urn": "m1"}]},
        final_text="an answer",
        proposed_action={"tool_id": "case.disposition", "args": {"disposition": "deny"}},
        proposal_id=new_uuid(), model="gpt", usage={}, consent=True,
        decision=decision, corrected_output=corrected)


async def _dataset_with_rows(store: InMemoryStore, agent="triage"):
    await store.record_transcript(_t(decision="edit", corrected={"disposition": "approve"}, agent=agent))
    await store.record_transcript(_t(decision="approve", agent=agent))
    ds = await SftCurator(store).curate(tenant_id=TENANT, agent_key=agent, created_by="u", params={})
    assert ds.row_count > 0
    return ds


# ---- archetypes -------------------------------------------------------------

def test_resolve_archetype_defaults_and_alias():
    a = archetypes.resolve_archetype("triage")
    assert a.base_model == archetypes.DEFAULT_BASE_MODEL
    assert a.model_alias == "slm-triage"


def test_resolve_archetype_rejects_unknown_base():
    with pytest.raises(ValueError):
        archetypes.resolve_archetype("triage", base_model="gpt-4o-huge-closed")


# ---- the honest GPU gate ----------------------------------------------------

async def test_submit_fails_honestly_when_no_gpu_trainer():
    store = InMemoryStore()
    ds = await _dataset_with_rows(store)
    svc = TrainingJobService(store, UnconfiguredGpuTrainer())
    job = await svc.submit(tenant_id=TENANT, agent_key="triage", sft_dataset_id=ds.dataset_id)
    assert job.status == "failed"
    assert job.error["reason"] == "gpu_trainer_not_configured"
    assert job.adapter_id is None
    # No fabricated adapter.
    assert await store.list_slm_adapters(TENANT) == []


async def test_unconfigured_trainer_raises_typed():
    spec = TrainingSpec(tenant_id=TENANT, archetype="triage", base_model="m",
                        sft_dataset_id="d", sft_examples_jsonl="", params={})
    with pytest.raises(GpuTrainerNotConfigured):
        await UnconfiguredGpuTrainer().train(spec)


def test_build_trainer_selects_backend():
    assert isinstance(build_trainer(None), UnconfiguredGpuTrainer)
    assert isinstance(build_trainer("fake"), FakeGpuTrainer)
    # An unimplemented real backend resolves to the honest unconfigured trainer.
    assert isinstance(build_trainer("modal"), UnconfiguredGpuTrainer)


# ---- the success path (fake trainer) + promotion lifecycle ------------------

async def test_submit_succeeds_and_creates_candidate_adapter():
    store = InMemoryStore()
    ds = await _dataset_with_rows(store)
    svc = TrainingJobService(store, FakeGpuTrainer())
    job = await svc.submit(tenant_id=TENANT, agent_key="triage", sft_dataset_id=ds.dataset_id)
    assert job.status == "succeeded" and job.adapter_id
    adapters = await store.list_slm_adapters(TENANT)
    assert len(adapters) == 1
    a = adapters[0]
    assert a.promotion_status == "candidate"
    assert a.adapter_uri and a.model_alias == "slm-triage"


async def test_promote_and_demote_lifecycle():
    store = InMemoryStore()
    ds = await _dataset_with_rows(store)
    svc = TrainingJobService(store, FakeGpuTrainer())
    job = await svc.submit(tenant_id=TENANT, agent_key="triage", sft_dataset_id=ds.dataset_id)

    promoted = await svc.promote(tenant_id=TENANT, adapter_id=job.adapter_id, eval_result_ref="eval-1")
    assert promoted.promotion_status == "promoted"
    assert promoted.target_rung_alias == "slm-triage" and promoted.eval_result_ref == "eval-1"

    # Can't promote twice.
    with pytest.raises(Conflict):
        await svc.promote(tenant_id=TENANT, adapter_id=job.adapter_id)

    demoted = await svc.demote(tenant_id=TENANT, adapter_id=job.adapter_id)
    assert demoted.promotion_status == "demoted"


# ---- validation -------------------------------------------------------------

async def test_submit_rejects_missing_dataset():
    store = InMemoryStore()
    svc = TrainingJobService(store, FakeGpuTrainer())
    with pytest.raises(NotFound):
        await svc.submit(tenant_id=TENANT, agent_key="triage", sft_dataset_id=new_uuid())


async def test_submit_rejects_archetype_mismatch():
    store = InMemoryStore()
    ds = await _dataset_with_rows(store, agent="triage")
    svc = TrainingJobService(store, FakeGpuTrainer())
    with pytest.raises(ValidationFailed):
        await svc.submit(tenant_id=TENANT, agent_key="copilot", sft_dataset_id=ds.dataset_id)
