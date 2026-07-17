"""SLM distillation milestone 1: transcript sink + PII redaction + decision join."""

from __future__ import annotations

from app.domain.entities import Run, new_uuid
from app.domain.redact import redact, redact_text
from app.domain.transcripts import TranscriptSink
from app.graphs.base import GraphOutcome, WriteIntent
from app.store.memory import InMemoryStore

TENANT = "11111111-1111-1111-1111-111111111111"


def _run() -> Run:
    return Run(
        run_id=new_uuid(), tenant_id=TENANT, session_id=new_uuid(),
        agent_key="triage", agent_version=1, temporal_workflow_id=None,
        status="completed", principal_type="user_obo", obo_sub="user-1")


def _intent(args: dict) -> WriteIntent:
    return WriteIntent(
        tool_id="case.disposition", tool_version="1", tier="write",
        side_effects="reversible", args=args, rationale="because",
        affected_urns=["wr:t:case:case/1"], predicted_effect={},
        required_action="case.case.update")


# ---- redaction -------------------------------------------------------------

def test_redact_direct_identifiers():
    s = "email me at jane.doe@acme.com or 555-123-4567, ssn 123-45-6789"
    out = redact_text(s)
    assert "jane.doe@acme.com" not in out and "[REDACTED:email]" in out
    assert "123-45-6789" not in out and "[REDACTED:ssn]" in out
    assert "555-123-4567" not in out and "[REDACTED:phone]" in out


def test_redact_walks_nested_json():
    v = {"notes": ["reach a@b.com"], "n": 5, "nested": {"ip": "10.0.0.1"}}
    out = redact(v)
    assert "[REDACTED:email]" in out["notes"][0]
    assert out["n"] == 5
    assert "[REDACTED:ip]" in out["nested"]["ip"]


# ---- capture + decision join ----------------------------------------------

async def test_capture_records_a_redacted_transcript():
    store = InMemoryStore()
    sink = TranscriptSink(store, enabled=True)
    run = _run()
    outcome = GraphOutcome(
        final_text="contact claimant at bob@x.com",
        write_intent=_intent({"disposition": "deny", "note": "call 555-987-6543"}),
        usage={"model": "gpt-x", "tokens": 42}, evidence=[{"urn": "m1"}])

    await sink.capture(run, {"case_id": "C1", "email": "u@v.com"}, outcome, proposal_id="p-1")

    rows = await store.list_transcripts(TENANT)
    assert len(rows) == 1
    t = rows[0]
    assert t.run_id == run.run_id and t.agent_key == "triage" and t.consent is True
    assert t.model == "gpt-x" and t.proposal_id == "p-1"
    # PII redacted everywhere
    assert "[REDACTED:email]" in t.final_text
    assert "[REDACTED:email]" in t.inputs["email"]
    assert "[REDACTED:phone]" in t.proposed_action["args"]["note"]
    # no human decision yet
    assert t.decision is None and t.corrected_output is None


async def test_consent_gate_off_captures_nothing():
    store = InMemoryStore()
    sink = TranscriptSink(store, enabled=False)
    await sink.capture(_run(), {}, GraphOutcome(final_text="hi"), proposal_id=None)
    assert await store.list_transcripts(TENANT) == []


async def test_edit_decision_becomes_a_correction_pair():
    store = InMemoryStore()
    sink = TranscriptSink(store, enabled=True)
    run = _run()
    await sink.capture(run, {"case_id": "C1"},
                       GraphOutcome(final_text="deny",
                                    write_intent=_intent({"disposition": "deny"})),
                       proposal_id="p-9")

    # a human edits the agent's proposed args -> the gold correction pair
    await sink.attach_decision(
        tenant_id=TENANT, proposal_id="p-9", action="edit_args",
        edited_args={"disposition": "approve", "reason": "docs on file"},
        decided_by="reviewer-2", decided_at=run.updated_at)

    t = (await store.list_transcripts(TENANT, only_decided=True))[0]
    assert t.decision == "edit"
    assert t.corrected_output == {"disposition": "approve", "reason": "docs on file"}
    assert t.decided_by == "reviewer-2"


async def test_reject_and_approve_labels():
    store = InMemoryStore()
    sink = TranscriptSink(store, enabled=True)
    for pid, action in [("pa", "approve"), ("pr", "reject")]:
        await sink.capture(_run(), {}, GraphOutcome(final_text="x"), proposal_id=pid)
        await sink.attach_decision(tenant_id=TENANT, proposal_id=pid, action=action,
                                   edited_args=None, decided_by="u", decided_at=None)
    labels = {t.proposal_id: t.decision for t in await store.list_transcripts(TENANT)}
    assert labels["pa"] == "approve" and labels["pr"] == "reject"


async def test_capture_never_raises_on_bad_outcome():
    # a malformed outcome must not blow up the run (best-effort capture)
    store = InMemoryStore()
    sink = TranscriptSink(store, enabled=True)
    await sink.capture(_run(), {}, object(), proposal_id=None)  # object() has no attrs
    # nothing recorded, no exception
    assert await store.list_transcripts(TENANT) == []
