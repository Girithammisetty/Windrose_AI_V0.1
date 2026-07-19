"""SLM distillation milestone 3/4: training-job control plane + honest GPU gate."""

from __future__ import annotations

import pytest

from app.adapters.modal_trainer import ModalGpuTrainer
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
    await store.record_transcript(_t(decision="edit", corrected={"disposition": "approve"}, agent=agent))  # noqa: E501
    await store.record_transcript(_t(decision="approve", agent=agent))
    ds = await SftCurator(store).curate(tenant_id=TENANT, agent_key=agent, created_by="u", params={})  # noqa: E501
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
    # "modal" is implemented: it returns the real trainer, which fails honestly
    # at EXECUTION (not selection) when the SDK/deploy is missing.
    assert isinstance(build_trainer("modal"), ModalGpuTrainer)
    # A still-unimplemented real backend resolves to the honest unconfigured one.
    assert isinstance(build_trainer("sagemaker"), UnconfiguredGpuTrainer)


# ---- modal backend: real invocation, honest failure, never a fake artifact ---

def _spec(jsonl='{"messages":[]}'):
    return TrainingSpec(tenant_id=TENANT, archetype="triage", base_model="Qwen/Qwen2.5-7B",
                        sft_dataset_id="d", sft_examples_jsonl=jsonl, params={"epochs": 1})


async def test_modal_trainer_unconfigured_when_function_unavailable():
    """No SDK / no token / not deployed -> typed GpuTrainerNotConfigured, so the
    control plane records `failed` with a real reason instead of hanging."""
    def _boom(_app, _fn):
        raise RuntimeError("not deployed")

    with pytest.raises(GpuTrainerNotConfigured) as e:
        await ModalGpuTrainer(lookup=_boom).train(_spec())
    assert "modal deploy" in str(e.value)


async def test_modal_trainer_maps_remote_result_to_artifact():
    """A successful GPU run maps to a real adapter URI + checksum."""
    captured = {}

    class _Remote:
        async def aio(self, **kwargs):
            captured.update(kwargs)
            return {"adapter_path": "Qwen_Qwen2.5-7B/abc123", "checksum": "deadbeef",
                    "call_id": "fc-123", "rows": 2}

    class _Fn:
        remote = _Remote()

    res = await ModalGpuTrainer(lookup=lambda *_: _Fn()).train(_spec())
    assert res.adapter_uri == "modal://windrose-slm-adapters/Qwen_Qwen2.5-7B/abc123"
    assert res.checksum == "deadbeef"
    assert res.mlflow_run_ref == "fc-123"
    # The corpus is passed INLINE — Modal needs no path into MinIO/MLflow.
    assert captured["sft_jsonl"] == '{"messages":[]}'
    assert captured["base_model"] == "Qwen/Qwen2.5-7B"


async def test_modal_trainer_refuses_malformed_remote_result():
    """A remote result with no adapter_path must NOT become a fake success."""
    class _Remote:
        async def aio(self, **_kwargs):
            return {"metrics": {"loss": 0.1}}  # no adapter_path

    class _Fn:
        remote = _Remote()

    with pytest.raises(GpuTrainerNotConfigured):
        await ModalGpuTrainer(lookup=lambda *_: _Fn()).train(_spec())


async def test_submit_records_failed_job_on_real_training_error():
    """With a REAL backend a GPU failure is not GpuTrainerNotConfigured; the job
    must land in `failed` (not stay `running`) and forge no adapter."""
    class _Exploding:
        async def train(self, _spec):
            raise RuntimeError("CUDA out of memory")

    store = InMemoryStore()
    ds = await _dataset_with_rows(store)
    job = await TrainingJobService(store, _Exploding()).submit(
        tenant_id=TENANT, agent_key="triage", sft_dataset_id=ds.dataset_id)
    assert job.status == "failed"
    assert job.error["reason"] == "training_failed"
    assert "CUDA out of memory" in job.error["detail"]
    assert job.adapter_id is None
    assert await store.list_slm_adapters(TENANT) == []


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

    promoted = await svc.promote(tenant_id=TENANT, adapter_id=job.adapter_id, eval_result_ref="eval-1")  # noqa: E501
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
