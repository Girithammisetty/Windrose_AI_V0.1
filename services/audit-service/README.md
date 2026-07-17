# audit-service

The Windrose platform's immutable system of record (BRD 18): "who did what, when,
to which resource, via which agent." It consumes every `*.events.v1` domain topic
plus the `ai.*` and `security.*` topics off **real Kafka**, writes them to an
**append-only ClickHouse** store with payload digests and a per-tenant-per-day
tamper-evidence hash chain, exports daily **WORM** batches to **MinIO/S3** under
Object-Lock with integrity manifests, serves an admin-only search API with
dual-attribution queries, and produces SOC 2 / EU AI Act compliance evidence
packs.

Every adapter is REAL and wired by default — no env flag selects a fake. The only
in-memory doubles live in `*_test.go` (unit tier) and are unreachable from
`cmd/server`.

## Run

```
# infra: deploy/docker-compose.dev.yml (clickhouse, redpanda, postgres, redis, minio, opa)
make run          # boots with real adapters against localhost infra
make test-unit    # no Docker (integration package auto-skips under -short)
make test-integration   # real ClickHouse/Kafka/Postgres/Redis/MinIO/OPA; auto-skips if down
```

Default config connects Postgres as the **non-owner** `audit_rw` role (RLS FORCE);
the superuser `ADMIN_DATABASE_URL` is used once at boot to create the `audit` DB +
`audit_rw` role, then dropped. ClickHouse native :9010, db `audit`. MinIO bucket
`windrose-audit` (Object-Lock). OPA sidecar :8281.

## Architecture

```
Kafka (*.events.v1, ai.*, security.*) ──▶ ingest.Consumer (regex sub, dedup, DLQ)
   └▶ Processor: validate envelope ▶ PII gate ▶ canonical digest ▶ chain.Append ▶ ClickHouse
Postgres (chain_heads, export_manifests, async_jobs, dlq_redrives)  ◀ chain checkpoint / seals
export.Scheduler ─ daily ▶ Parquet+manifest ▶ MinIO WORM (Object-Lock, 7y)
API (chi): search / agent-activity / event / verify / exports / compliance / operations / dlq-redrive
   └ OPA sidecar authz + JWKS ; every call emits a meta-audit event to audit.events.v1
```

## FR traceability

| FR | Requirement | Code | Test |
|---|---|---|---|
| AUD-FR-001 | Regex subscription to all domain+ai topics, zero-code new topics | `domain/topics.go`, `ingest/consumer.go` (`DiscoverTopics`/rescan) | `domain_test.go:TestSubscriptionMatching`, `TestAC01` |
| AUD-FR-002 | Envelope validation → DLQ `ENVELOPE_INVALID` | `domain/domain.go:ValidateEnvelope`, `ingest/consumer.go:toDLQ` | `TestAC04`, `processor_test.go` |
| AUD-FR-003 | Payload digest always; body inline iff PII-clean + ≤64KB; else payload_ref | `domain/{domain,pii}.go`, `ingest/processor.go` | `TestAC03`, `TestProcessor*` |
| AUD-FR-004 | Idempotent ingest (Redis SETNX + ReplacingMergeTree + existence recovery) | `ingest/consumer.go:processMsg`, `chstore` FINAL | `TestAC02` (replay→1 row), `TestAC01`, `TestAC15` |
| AUD-FR-006 | Per-topic DLQ (never committed unless publish succeeds) + audited redrive | `ingest/consumer.go:{toDLQ,Redrive}`, `api/handlers.go:handleRedrive` | `TestAC04`, `TestAC15` |
| AUD-FR-010/011 | Append-only ClickHouse, month partitions, 7y TTL | `chstore/chstore.go:Migrate` | `TestAC01`, `TestAC06` |
| AUD-FR-020/021/022 | Daily WORM Parquet+manifest (Object-Lock), chained manifests, supplements | `export/export.go`, `worm/worm.go` | `TestAC06` |
| AUD-FR-023 | List sealed batches + signed URLs | `api/handlers.go:handleListExports` | (exercised via `pgstore.ListSealedManifests`) |
| AUD-FR-030 | Admin search (filters, ≤92d, cursor, -occurred_at) | `chstore.Search`, `api/handlers.go:handleSearch` | `TestAC09`, `TestAC07` |
| AUD-FR-031 | Dual attribution + agent-activity convenience | `chstore.Search` (dual branch), `handleAgentActivity` | `TestAC07` |
| AUD-FR-032 | CSV/NDJSON export; every search audited | `handlers.go:streamExport`, `meta.Searched` | `TestAC09`, `TestAC08` |
| AUD-FR-033 | Single event + chain position/seal status | `handleGetEvent` | `TestAC03`, `TestAC09` |
| AUD-FR-050 | Hash chain: idempotent assignment + distributed single-writer lock + ClickHouse-anchored recovery | `chain/chain.go:Append`, `chstore.ChainTip`, `domain.ChainHash` | `chain_test.go`, `TestAC05`, `TestAC11` (transient-CH no-gap), `TestAC11b` (concurrent single-writer) |
| AUD-FR-051 | Verify endpoint (unsealed→CONFLICT); tamper + gap detection; integrity_violation on any invalid | `chain.Verify`, `handleVerify` | `chain_test.go:TestVerifyDetectsTamper`, `TestAC05`, `TestBR9` |
| AUD-FR-032 | Distinct export action bound to `/audit/export`; every search/export audited | `api/handlers.go:handleExport`, `meta.Searched` | `TestAC10` (auditors audited), drift test |
| AUD-FR-060 | SOC 2 evidence pack (async, zip, signed URL) | `compliance/compliance.go:SOC2Pack` | (`RawSelect` path shared with AC08) |
| AUD-FR-061/062 | EU AI Act decision log, reproducible | `compliance.AIDecisionLog` | `TestAC08` |
| AUD-FR-070/071 | PII gate on ingest (drop body, keep digest, emit meta) | `domain/pii.go`, `ingest/processor.go`, `meta.PIIRejected` | `domain_test.go:TestPIIGate`, `TestAC03` |
| MASTER-FR-001/003 | RLS FORCE, non-owner role, cross-tenant→404 | `migrations/000002,000003`, `pgstore` | `TestAC09` (404), `TestAC09b` (RLS non-owner) |
| MASTER-FR-012 | OPA sidecar authz on admin API | `authz/opa_client.go`, `api/middleware.go` | `TestAC09` |
| RBC-FR-022 | Action-manifest registration with rbac at startup | `register/register.go` | wired in `cmd/server` |

## Credential-gated exception

None. Every adapter is verifiable against the local compose stack. The one honest
ceiling per CONVENTIONS (a live cloud object store with cloud-native Object-Lock)
is exercised here against MinIO's real S3 Object-Lock API; a production S3/Azure/GCS
target only swaps endpoint + credentials.
