"""SLM distillation milestone 2: SFT template + curation → versioned dataset."""

from __future__ import annotations

import json

from app.domain.entities import Transcript, new_uuid
from app.domain.sft_curation import SftCurator
from app.domain.sft_template import to_sft_example
from app.store.memory import InMemoryStore

TENANT = "11111111-1111-1111-1111-111111111111"


def _t(*, decision, corrected=None, proposed_args=None, final="an answer",
       consent=True, agent="triage", inputs=None):
    return Transcript(
        transcript_id=new_uuid(), tenant_id=TENANT, run_id=new_uuid(), session_id=None,
        agent_key=agent, agent_version=1, principal_type="user_obo", obo_sub="u",
        inputs=inputs if inputs is not None else {"case_id": "C1", "amount": 100},
        grounding={"evidence": [{"urn": "m1"}]}, final_text=final,
        proposed_action=({"tool_id": "case.disposition", "args": proposed_args}
                         if proposed_args is not None else None),
        proposal_id=new_uuid(), model="gpt", usage={}, consent=consent,
        decision=decision, corrected_output=corrected)


# ---- template --------------------------------------------------------------

def test_edit_target_is_the_human_correction():
    ex = to_sft_example(_t(decision="edit", corrected={"disposition": "approve"}))
    assert ex["target_kind"] == "edit"
    assert json.loads(ex["messages"][2]["content"]) == {"disposition": "approve"}
    assert ex["messages"][0]["role"] == "system" and ex["messages"][1]["role"] == "user"


def test_approve_target_is_the_agents_accepted_action():
    ex = to_sft_example(_t(decision="approve", proposed_args={"disposition": "deny"}))
    assert ex["target_kind"] == "approve"
    assert json.loads(ex["messages"][2]["content"]) == {"disposition": "deny"}


def test_reject_and_unconsented_are_not_gold_pairs():
    assert to_sft_example(_t(decision="reject", proposed_args={"x": 1})) is None
    assert to_sft_example(_t(decision="approve", proposed_args={"x": 1}, consent=False)) is None
    assert to_sft_example(_t(decision=None)) is None


# ---- curation --------------------------------------------------------------

async def _seed(store, transcripts):
    for t in transcripts:
        await store.record_transcript(t)


async def test_curate_builds_a_versioned_deduped_consented_dataset():
    store = InMemoryStore()
    edit = _t(decision="edit", corrected={"disposition": "approve"})
    dup = _t(decision="edit", corrected={"disposition": "approve"})  # same input+target
    approve = _t(decision="approve", proposed_args={"disposition": "deny"})
    await _seed(store, [
        edit, dup, approve,
        _t(decision="reject", proposed_args={"x": 1}),                 # excluded
        _t(decision="approve", proposed_args={"y": 2}, consent=False),  # excluded (no consent)
    ])

    ds = await SftCurator(store).curate(tenant_id=TENANT, agent_key="triage", created_by="admin")
    assert ds.version == 1
    assert ds.row_count == 2                 # edit + approve; reject/unconsented excluded, dup deduped  # noqa: E501
    assert ds.source_count == 5              # all decided transcripts considered
    assert ds.consent_verified is True
    assert ds.curation_params["n_edit"] == 1 and ds.curation_params["n_approve"] == 1
    assert len(ds.checksum) == 16

    rows = await store.list_sft_examples(TENANT, ds.dataset_id)
    assert len(rows) == 2
    kinds = {r.target_kind for r in rows}
    assert kinds == {"edit", "approve"}
    # lineage back to the source transcript is preserved
    assert all(r.source_transcript_id for r in rows)


async def test_recuration_mints_the_next_version_and_is_deterministic():
    store = InMemoryStore()
    await _seed(store, [_t(decision="edit", corrected={"d": "a"})])
    d1 = await SftCurator(store).curate(tenant_id=TENANT, agent_key="triage", created_by="a")
    d2 = await SftCurator(store).curate(tenant_id=TENANT, agent_key="triage", created_by="a")
    assert d1.version == 1 and d2.version == 2
    # same corpus → same content checksum (content-addressable versioning)
    assert d1.checksum == d2.checksum


async def test_curation_is_archetype_scoped():
    store = InMemoryStore()
    await _seed(store, [
        _t(decision="edit", corrected={"d": "a"}, agent="triage"),
        _t(decision="edit", corrected={"d": "b"}, agent="fwa-investigator"),
    ])
    ds = await SftCurator(store).curate(tenant_id=TENANT, agent_key="triage", created_by="a")
    assert ds.row_count == 1  # only the triage transcript
