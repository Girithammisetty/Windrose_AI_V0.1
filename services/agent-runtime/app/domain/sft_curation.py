"""SFT curation (distillation milestone 2).

Reads the governed transcript corpus (milestone 1) for one archetype (agent_key)
and produces a versioned, deduped, consent-verified SFT dataset of gold
chat-format pairs. A built dataset is an immutable snapshot; re-curation mints
the next version. Human-corrected (edit) pairs are the highest-value signal;
approvals are included as accepted-as-is pairs.

Curation steps: pull decided+consented transcripts → template each into a chat
example → drop degenerate rows → dedup by (input,target) hash → checksum the
ordered set (content-addressable version) → persist the dataset + rows.
"""

from __future__ import annotations

import hashlib

from app.domain.entities import SftDataset, SftExample, new_uuid
from app.domain.sft_template import to_sft_example


class SftCurator:
    def __init__(self, store) -> None:
        self._store = store

    async def curate(
        self, *, tenant_id: str, agent_key: str, created_by: str | None,
        params: dict | None = None,
    ) -> SftDataset:
        params = params or {}
        limit = int(params.get("max_transcripts", 5000))
        transcripts = await self._store.list_transcripts(
            tenant_id, agent_key=agent_key, only_decided=True, limit=limit)
        source_count = len(transcripts)

        examples: list[dict] = []
        seen: set[str] = set()
        for t in transcripts:
            ex = to_sft_example(t)
            if ex is None or ex["example_hash"] in seen:
                continue
            seen.add(ex["example_hash"])
            examples.append(ex)

        checksum = hashlib.sha256(
            "".join(e["example_hash"] for e in examples).encode()
        ).hexdigest()[:16]
        version = await self._store.next_sft_version(tenant_id, agent_key)
        n_edit = sum(1 for e in examples if e["target_kind"] == "edit")
        n_approve = sum(1 for e in examples if e["target_kind"] == "approve")

        ds = SftDataset(
            dataset_id=new_uuid(), tenant_id=tenant_id, agent_key=agent_key,
            version=version, status="built", row_count=len(examples),
            source_count=source_count,
            curation_params={**params, "n_edit": n_edit, "n_approve": n_approve},
            checksum=checksum, consent_verified=True, created_by=created_by,
        )
        rows = [
            SftExample(
                dataset_id=ds.dataset_id, tenant_id=tenant_id, ord=i,
                messages=e["messages"], target_kind=e["target_kind"],
                source_transcript_id=e["source_transcript_id"], example_hash=e["example_hash"],
            )
            for i, e in enumerate(examples)
        ]
        await self._store.record_sft_dataset(ds, rows)
        return ds
