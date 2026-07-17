# Design — SIEM / Audit Export (`audit.export.v1`)

Phase 3 of `docs/design/byo-infra-hardening.md`. Lets a customer's SIEM
(Splunk, Sentinel, Chronicle, Datadog Security, or any generic Kafka/webhook
consumer) consume Windrose's audit trail without polling the search API.

## What this is (and isn't)

audit-service's ingest path (`internal/ingest/processor.go`) already does
consume → validate → PII-gate → digest → hash-chain → ClickHouse insert, then
daily WORM export to S3/MinIO under Object-Lock. **None of that changes.**
`audit.export.v1` is an **additional sink**: after a record is durably chained
and inserted, the exact same record is normalized into a stable external
schema and republished on its own versioned Kafka topic
(`internal/siemexport/siemexport.go`). It carries zero authority — it cannot
affect ingest, the chain, or WORM retention, and a publish failure there is
logged and swallowed, never surfaced as an ingest error.

This is deliberately a generic **egress** surface, not a first-party
Splunk/Sentinel/Chronicle connector. Wiring a specific vendor's ingestion
(Splunk's Kafka Connect, Sentinel's Event Hub-compatible ingestion, a
Chronicle forwarder, ...) against this topic — or against the webhook path —
is left to the customer or their integrator, matching the platform's existing
integration posture (BYO connectors, not maintained-by-us adapters).

## Schema reference

Every exported event is a standard Windrose platform envelope (the same
`event.Envelope` shape — `libs/go-common/event/envelope.go` — every other
service publishes with), so a raw-Kafka consumer and a webhook consumer
receive **byte-identical JSON**: the webhook sender POSTs the marshaled
envelope as its body, and the Kafka producer publishes the same marshaled
envelope as the message value.

### Envelope (top level)

| Field | Type | Notes |
|---|---|---|
| `event_id` | string (uuid) | **Not** the source record's `event_id`. A stable id deterministically derived as `uuid5("audit.export.v1:" + source_event_id)` — see "Why a derived event_id" below. Same source record always yields the same export `event_id` (idempotent under DLQ redrive/reprocessing). |
| `event_type` | string | Always the constant `"audit.export.v1"` — not the original per-action event type — so one webhook subscription / one Kafka subscription matches every exported record regardless of what produced it. |
| `tenant_id` | string (uuid) | The tenant the underlying audit record belongs to. |
| `actor` | object `{type, id}` | `type` ∈ `user \| service \| agent \| platform`. The actor of the *original* event (not audit-service). |
| `via_agent` | object `{agent_id, version}` or `null` | Dual attribution (MASTER-FR-041) when the original action was an agent acting on behalf of a user. |
| `resource_urn` | string | `wr:<tenant>:<service>:<resource_type>/<resource_id>` — the resource the original action touched. |
| `occurred_at` | string (RFC3339) | When the original action happened (not when it was exported). |
| `trace_id` | string | Correlates back to the originating request trace, when present. |
| `payload` | object | See below. |

### Payload (normalized SIEM fields)

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | `"1.0"` today. See "Versioning policy". |
| `source_event_id` | string (uuid) | The **original** internal `event_id` this export was derived from — use this to correlate back to a specific row via audit-service's search API (`GET /audit/event/{id}`) if you need the full record. |
| `source_event_type` | string | The original internal event type, e.g. `"case.created"`, `"proposal.approved"`, `"identity.user.created"`. |
| `action` | string | `"<service>.<verb>"`, e.g. `"case.case.created"` — audit-service's derived action name (`domain.ActionFromEventType`). |
| `resource_service` | string | The service namespace parsed from `resource_urn` (e.g. `"case"`). |
| `resource_type` | string | The resource type parsed from `resource_urn` (e.g. `"case"`). |
| `outcome` | string | Best-effort classification derived from `source_event_type`: one of `success \| denied \| rejected \| failed \| expired \| recorded`. See "Outcome derivation" below — audit-service has no dedicated outcome column today, so this is inferred, not stored. |
| `payload_digest` | string (sha256 hex) | The SHA-256 of the original event's canonical-JSON payload (`domain.PayloadDigest`) — an integrity reference. **The raw payload itself is intentionally NOT exported** (see "Why no raw payload" below). |
| `source_topic` | string | The Kafka topic the original event arrived on, e.g. `"case.events.v1"`. |
| `chain_date` | string (`YYYY-MM-DD`) | The hash-chain day this record belongs to (BR-2/BR-3). |
| `chain_seq` | uint64 | This record's position in that day's per-tenant hash chain — pair with `chain_date` to look up the exact chain position via `GET /audit/verify`. |
| `via_agent` | object, optional | Duplicated here (in addition to the envelope's top-level `via_agent`) for consumers that only parse `payload`. |
| `obo_user_id` | string, optional | The human user an agent acted on behalf of, when applicable (dual attribution). |

### Outcome derivation

`outcome` is a heuristic classification of `source_event_type`'s suffix
(mirrors the style of `internal/compliance/compliance.go:decisionOutcome`,
used for the EU AI Act decision log):

| `source_event_type` contains | `outcome` |
|---|---|
| `denied` | `denied` |
| `rejected` | `rejected` |
| `failed` | `failed` |
| `expired` | `expired` |
| `approved`, `succeeded`, `completed`, `created`, `updated`, `deleted` | `success` |
| (anything else) | `recorded` |

### Why a derived `event_id`

The export envelope's `event_id` is **not** the source record's `event_id` —
it's `uuid5("audit.export.v1:" + source_event_id)`, a stable, deterministic
derivation (same input always produces the same output, so DLQ redrive /
reprocessing doesn't produce a different id for the same underlying record).

This matters because several platform consumers (notification-service, among
others) run a single Kafka consumer group across *every* topic they
subscribe to, deduping on a **global, cross-topic** key —
`"evt:dedup:" + event_id"` — that is not scoped per-topic. A service that
already consumes both a source domain topic (say `case.events.v1`) *and*
`audit.export.v1` would see the *same* `event_id` twice if we reused it
verbatim, and the second arrival (whichever came second) would be silently
treated as an already-processed duplicate and dropped before ever reaching
its handler. Deriving a distinct, topic-namespaced id sidesteps that
collision entirely. `source_event_id` in the payload preserves the
correlation.

### Why no raw payload

The original event's `payload` (arbitrary JSON, PII-gated on ingest per
AUD-FR-070/071) is deliberately **not** included in the export event — only
its `payload_digest`. This keeps the external contract small, avoids
re-litigating the PII gate for a second sink, and avoids widening the blast
radius of a payload class that turns out to contain something sensitive after
all. A SIEM that needs the full record can fetch it via audit-service's
existing authenticated search API (`GET /audit/event/{event_id}`), using
`source_event_id` (not the export `event_id`) as the lookup key.

## Publishing (audit-service side)

`internal/siemexport/siemexport.go` (`Exporter.Publish`) is called from
`internal/ingest/processor.go:Processor.Handle`, **strictly after** the
record's ClickHouse insert succeeds:

```go
if err := p.CH.Insert(ctx, rec); err != nil {
    return fmt.Errorf("clickhouse insert: %w", err) // transient → pause
}
if p.Export != nil {
    p.Export.Publish(ctx, rec) // additive: logged + swallowed on failure
}
return nil
```

`Export` is optional (nil-safe) and reuses the **same** `*gckafka.Producer`
already wired in `cmd/server/main.go` for DLQ + meta-event publishing — no new
Kafka client. A publish failure here is logged (`"siem export publish
failed"`) and never returned from `Handle`, so it can never trigger a
retry/DLQ of an event that has already been correctly ingested. This sink has
**zero** bearing on the hash chain (`internal/chain/chain.go`, untouched by
this change) or WORM export (`internal/export/export.go`, also untouched).

## Integration path 1: subscribe to the Kafka topic directly

Any Kafka-consuming SIEM connector (Splunk's Kafka Connect, Sentinel's Event
Hub-compatible ingestion, Chronicle's forwarder, or a bespoke consumer) can
subscribe to `audit.export.v1` the same way any Windrose service subscribes to
a platform topic. A minimal generic consumer:

```python
from kafka import KafkaConsumer
import json

consumer = KafkaConsumer(
    "audit.export.v1",
    bootstrap_servers=["kafka.customer-vpc.internal:9092"],  # or your Kafka-Connect worker
    group_id="siem-forwarder",
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    auto_offset_reset="earliest",
)
for msg in consumer:
    event = msg.value
    print(event["event_id"], event["payload"]["source_event_type"],
          event["payload"]["outcome"], event["resource_urn"])
    # forward `event` to your SIEM's ingestion API / index here
```

## Integration path 2: the webhook forwarder (push instead of pull)

For a SIEM that prefers push (e.g. Splunk HTTP Event Collector), a tenant
configures a webhook endpoint via notification-service's **existing** webhook
admin API (`internal/api/handlers_webhooks.go`) — the same
delivery/retry/circuit-breaker infrastructure already built for every other
webhook-eligible event (`internal/channels/webhook/`,
`internal/pipeline/webhook.go`). **No new delivery code was written for this
integration** — audit-service publishing `audit.export.v1` onto a topic
notification-service already knows how to consume, plus one registry mapping
(`internal/registry/registry.go`) so `Process` reaches `deliverWebhooks`, is
the entire integration surface:

- `internal/events/events.go:ConsumedTopics()` includes `"audit.export.v1"`.
- `internal/registry/registry.go` maps `"audit.export.v1"` to an empty
  audience/channel set — it exists *only* so `Registry.Lookup` succeeds and
  `Process` reaches `deliverWebhooks`; it never resolves an in-app/email
  recipient or renders a template. Delivery is exclusively via a tenant's
  `webhook_endpoints` row.

A tenant self-serves the per-tenant "forward `audit.export.v1` to this HTTPS
endpoint" configuration with the **existing** webhook admin API — this doubles
as the "per-tenant config" for Phase 3; no new table was needed since
`webhook_endpoints` (tenant-scoped, RLS-protected) already stores exactly
"this tenant's URL + which event types to forward":

```bash
curl -X POST https://notification.<tenant>.windrose.ai/api/v1/webhooks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
        "url": "https://http-inputs-<splunk-host>.splunkcloud.com/services/collector/event",
        "event_types": ["audit.export.v1"]
      }'
```

Registration performs a synchronous verification handshake (NOTIF-FR-022):
notification-service POSTs `{"type":"endpoint.verify","challenge":"<random>"}`
to the URL and requires it echoed back (raw or as `{"challenge": "..."}`)
within 30s before the endpoint is marked `verified_at`/active. A Splunk HEC
endpoint doesn't speak this handshake natively, so in practice you put a thin
receiver in front of HEC that echoes the challenge and otherwise forwards the
delivered envelope to HEC, translating shape as needed, e.g.:

```python
# thin Splunk HEC bridge: verify handshake + forward audit.export.v1 deliveries
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
HEC_URL = "https://splunk.internal:8088/services/collector/event"
HEC_TOKEN = "..."

@app.post("/hooks/audit-export")
def receive():
    body = request.get_json(force=True)
    if body.get("type") == "endpoint.verify":
        return jsonify({"challenge": body["challenge"]})
    # real delivery: forward as a Splunk HEC event
    evt = {
        "event": body["payload"] | {
            "event_id": body["event_id"], "tenant_id": body["tenant_id"],
            "actor": body["actor"], "resource_urn": body["resource_urn"],
            "occurred_at": body["occurred_at"], "trace_id": body["trace_id"],
        },
        "sourcetype": "windrose:audit:export",
        "time": body["occurred_at"],
    }
    requests.post(HEC_URL, json=evt,
                  headers={"Authorization": f"Splunk {HEC_TOKEN}"}, timeout=5)
    return jsonify({"status": "received"})
```

Deliveries are HMAC-signed (`X-Windrose-Signature: v1=<hex>`, over
`"<unix_timestamp>.<raw body>"`) with a rotatable per-endpoint secret
(`internal/channels/webhook/sign.go`); verify it before trusting the payload.
Delivery status/history is queryable via
`GET /api/v1/webhooks/{id}/deliveries`, and a failed delivery retries on the
existing schedule (1m, 5m, 30m, 2h, 6h, 24h) with a circuit breaker that opens
after 10 consecutive failures and auto-disables after 72h open
(`internal/channels/webhook/sender.go`) — all pre-existing behavior, unchanged
by this integration.

## Versioning / deprecation policy

`audit.export.v1` is now an **external contract** — unlike audit-service's
other, incidental internal topics, customers integrate their own tooling
against it, so it needs an explicit compatibility policy:

- **Within v1**: only **additive, backward-compatible** changes are allowed —
  new optional payload fields, new `outcome`/classification values a consumer
  should treat as an open set (don't hardcode an exhaustive switch on
  `outcome` without a default branch), new envelope fields. Existing fields
  never change type or meaning, and no field is removed within v1.
- **Breaking changes** (removing/renaming a field, changing a field's type or
  semantics, changing `event_id` derivation) require a **new topic**,
  `audit.export.v2`, published *alongside* `audit.export.v1` — never an
  in-place breaking change to v1.
- **Deprecation window**: once `v2` ships, `v1` stays fully supported (same
  publish cadence, same guarantees) for a minimum of **6 months** before any
  deprecation notice, and remains readable for at least **12 months** total
  before removal is even considered — long enough for a customer's SIEM
  integration/SI engagement to migrate on their own schedule. Deprecation (if
  it ever happens) is announced via this document and the platform's release
  notes, never a silent removal.
- `schema_version` in every payload lets a consumer detect which revision of
  the v1 contract it's reading even before a v2 topic exists, in case a
  future v1 revision needs to signal a meaningfully different field set.

## Live verification (this build)

Verified end-to-end against the running dev stack (2026-07-16): a real case
creation in case-service produced a `case.created` event that flowed through
audit-service's normal ingest path (chain + ClickHouse, unaffected) and was
additionally published to `audit.export.v1`; a throwaway Kafka consumer
received it with the correct fields, and a tenant-configured webhook endpoint
(pointed at a throwaway local HTTP receiver, using notification-service's
existing webhook admin API) received a correctly HMAC-signed HTTP POST of the
same event, recorded as `status: "delivered"` via the existing deliveries API.
audit-service's unit test suite (`go test ./... -short`, all packages)
continued to pass unmodified, confirming the hash chain, PII gate and WORM
export paths are unaffected.
