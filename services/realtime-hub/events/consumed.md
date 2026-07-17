# realtime-hub — consumed events

The hub **emits nothing to Kafka** (it is transport; §6). It **consumes** the
producer topics below in **broadcast mode** (a unique consumer group `hub-fanout-<pod_id>`
per pod, so every pod sees every event — RTH-FR-041) and routes them to client
topics via the RTH-FR-020 routing table. A separate group `hub-rbac-<pod_id>`
consumes `rbac.events.v1` for subscription revocation (RTH-FR-013).

Unroutable/oversize events are **skipped-and-counted** — no DLQ semantics apply
to fan-out (documented deviation from MASTER-FR-033, per RTH-FR-020: the hub
never blocks a Kafka partition on slow clients).

## Fan-out topics → client topic (routing table, RTH-FR-020)

| Kafka topic | event_type (matched) | Client topic template |
|---|---|---|
| `pipeline.events.v1` | `pipeline.run.*`, `pipeline.step.*` | `run-status:{resource_urn}` |
| `ingestion.events.v1` | `ingestion.started\|progress\|completed\|failed` | `run-status:{resource_urn}` |
| `inference.events.v1` | `inference.started\|completed\|failed` | `run-status:{resource_urn}` |
| `chart.events.v1` | `chart.export.completed\|failed` | `run-status:{payload.operation_urn}` |
| `case.events.v1` | `case.bulk.completed` | `run-status:{payload.operation_urn}` |
| `notification.events.v1` | `notification.created` (with push block) | `notifications:{payload.user_id}` |
| `ai.events.v1` | `proposal.created\|approved\|rejected\|expired` | `proposal:{resource_id}` |
| `ai.events.v1` | `agent.run.status_changed` | `run-status:{resource_urn}` |

## Revocation topic (RTH-FR-013)

| Kafka topic | Handling |
|---|---|
| `rbac.events.v1` | grant/role/membership changes → re-evaluate active subscriptions on the changed `resource_urn`; terminate revoked ones with a `revoked` control event (index: resource_urn → subscriptions). |

## Envelope

All consumed messages use the master event envelope (MASTER-FR-031, `libs/go-common/event`).
The client receives a compact body `{event_type, payload, occurred_at, resource_urn}`
with `id` = the producer `event_id` (uuidv7) so ordering/resume is stable end-to-end
(RTH-FR-004).
