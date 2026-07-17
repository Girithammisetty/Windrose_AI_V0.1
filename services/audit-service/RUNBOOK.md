# audit-service RUNBOOK

Operational failure modes and procedures (BRD 18 §8, MASTER-FR-072).

## Boot / dependencies
Real adapters, all wired by default (no flags): ClickHouse (`CLICKHOUSE_ADDR`,
native :9010, db `audit`), Redpanda/Kafka (`KAFKA_BROKERS`), Postgres
(`DATABASE_URL` — the non-owner `audit_rw` role; `ADMIN_DATABASE_URL` — superuser
used once at boot to create the `audit` DB + `audit_rw` role), Redis
(`REDIS_ADDR`), MinIO (`MINIO_ENDPOINT`, bucket `AUDIT_BUCKET`), OPA
(`OPA_URL`), JWKS (`JWKS_URL`).

## DLQ triage / redrive
- DLQ topics: `<source_topic>.<INGEST_GROUP>.dlq`. Poison payload carries
  `reason` (`ENVELOPE_INVALID` / `PAYLOAD_DECODE`), `source_topic`, `raw`.
- Inspect: consume the DLQ topic; group by `reason`. `ENVELOPE_INVALID` almost
  always means a producer bug (fix the producer, then redrive).
- Redrive after fix: `POST /api/v1/admin/dlq/redrive {"topic":"<source_topic>"}`
  (platform operator, `audit.dlq.redrive`). Redriven events re-enter the current
  day's chain and emit `audit.dlq_redriven`.

## Chain-checkpoint recovery after unclean shutdown / rebalance
- The chain counter + head live in Redis (`audit:chain:{seq,head}:<tenant>:<date>`)
  and are checkpointed to Postgres `chain_heads` on every event. On a cold Redis
  key the manager reseeds from `chain_heads` (BR-10), so `chain_seq` continues
  without gaps or duplicates. To force reseed: `DEL` the Redis chain keys for the
  affected `<tenant>:<date>`; the next event reloads from Postgres.
- If Postgres `chain_heads` is lost, rebuild by replaying ClickHouse rows for the
  day ordered by `chain_seq` and taking the last `chain_hash` as the head.

## Re-export / supplement (late events)
- `export_manifests` rows are immutable; a supplement writes a NEW revision
  (`events-<rev>.parquet` + `manifest-r<rev>.json`) — originals are never
  overwritten (WORM). Re-run the export for the day; `ExportDay` picks the next
  revision automatically.

## Integrity-violation incident response (P1)
- `POST /api/v1/audit/verify {tenant_id, date}` → `valid:false` with
  `first_mismatch_seq` means a stored row diverges from the chain. This is a P1.
  1. Preserve the ClickHouse partition and the sealed WORM manifest for the day.
  2. Compare the tampered row's `payload_digest`/`chain_hash` at `first_mismatch_seq`
     against the WORM Parquet copy (immutable) to establish the authoritative value.
  3. The WORM manifest's `chain_head` + `prev_manifest_sha256` chain proves which
     side was altered. Escalate per security policy.

## ClickHouse replica rebuild from Kafka + WORM
- Historical (sealed) days: restore Parquet objects from the WORM bucket.
- Recent (unsealed) days: reset the `audit-ingest` consumer group to the earliest
  retained offset and let ingestion replay — dedup (Redis SETNX + event-existence
  check) and ReplacingMergeTree make replay idempotent (no double chain counts).

## Backpressure / ClickHouse down (BR-6)
- The consumer PAUSES (does not commit offsets) on ClickHouse/Redis outages;
  Kafka buffers (≥ 7-day retention prerequisite). No producer ever fails. Lag
  recovers automatically when the store returns.
