# realtime-hub (Go)

The platform's single push channel to browsers: **SSE (primary)** + **WebSocket
(secondary)**. It fans in from **real Kafka (Redpanda)** and an internal publish
API, and fans out **sticky-less across pods over real Redis pub/sub**, with a
**Redis Streams replay buffer** for `Last-Event-ID` resume, **per-topic OPA
authorization**, backpressure with a slow-client drop policy, connection caps,
15s heartbeats, and the normative reconnect contract. Implements BRD 20; inherits
`docs/brd/00_MASTER_BRD.md`.

**No in-memory fan-out in the runtime path.** Every cross-pod hop is real Redis;
every Kafka hop is real Redpanda; every authz decision is the real OPA sidecar.
In-memory doubles exist only in `*_test.go`.

## Run

```bash
export PATH="/opt/homebrew/bin:$PATH"
# dev infra (do not edit the compose file)
docker compose -f ../../deploy/docker-compose.dev.yml up -d redis redpanda opa postgres

make build          # go build ./cmd/server
make test-unit      # unit tier (no infra; test doubles only)
make test           # unit + integration (needs the stack above)
make race           # -race on fanout + topics
make run            # runs the server (see env below)
```

### Environment

| Var | Default | Purpose |
|---|---|---|
| `LISTEN_ADDR` | `:8080` | public HTTP/SSE/WS listener |
| `INTERNAL_LISTEN_ADDR` | `:8090` | **separate** internal producer (publish) listener — mesh-mTLS-frontable, never shares the public port |
| `REDIS_ADDR` | `localhost:6379` | pub/sub, Streams replay, tickets, counters, leases |
| `KAFKA_BROKERS` | `localhost:9092` | fan-in (`false` disables Kafka; internal-publish only) |
| `OPA_URL` | `http://localhost:8281` | per-topic authorization sidecar |
| `JWKS_URL` / `JWT_ISSUER` / `JWT_AUDIENCE` | — | RS256 verification (identity-service) |
| `DATABASE_URL` | — | ticket audit + `routing_rules` config (optional in dev) |
| `POD_ID` | random | broadcast consumer-group suffix + lease holder id |
| `MAX_CONNS_PER_USER/TENANT/POD` | 10 / 2000 / 50000 | connection caps |

## Architecture (top 2 levels)

```
realtime-hub/
├── cmd/server/            wiring (real adapters only)
├── internal/
│   ├── api/               chi router, SSE, WebSocket, tickets, token refresh, admin, internal publish, health
│   ├── authz/             per-topic authorization (OPA + structural notifications/chat rules)
│   ├── events/            Kafka fan-in (broadcast) + routing + revocation + audit emitter
│   ├── fanout/            hub, connection backpressure, Redis bus, replay, leader lease, caps
│   ├── metrics/           Prometheus collectors
│   ├── store/             Postgres (ticket audit + routing_rules)
│   └── topics/            topic grammar, QoS, routing table
├── migrations/            forward-only SQL (stream_tickets RLS, routing_rules)
├── api/openapi.yaml       HTTP contract
├── events/consumed.md     consumed Kafka topics + routing table
├── Dockerfile             distroless/static (pure Go, CGO_ENABLED=0)
└── Makefile
```

## Adapter inventory (all real; no runtime stubs)

| Capability | Real adapter | Backing infra | Proven by |
|---|---|---|---|
| Cross-pod fan-out | `internal/fanout/redisbus.go` (`redisx` + go-redis pub/sub) | Redis 7 | `TestAC02`, `TestAC08` |
| Resume buffer | `internal/fanout/replay.go` (Redis Streams `XADD`/`XRANGE`) | Redis 7 | `TestAC03`, `TestAC04` |
| Replay leader | `internal/fanout/lease.go` (`SET NX PX` + Lua renew/release) | Redis 7 | `TestLeaderLease_SingleWriter` |
| Connection caps | `internal/fanout/caps.go` (Redis `INCR`/`DECR`) | Redis 7 | `TestAC10` |
| Tickets / sessions | `internal/api/tickets.go`, `authz/opa.go` (Redis `SETEX`/`GETDEL`/`GET`) | Redis 7 | `TestAC08` (session), ticket path |
| Kafka fan-in | `internal/events/consumer.go` (`go-common/kafka` consumer groups) | Redpanda | `TestAC02` |
| Per-topic authz | `internal/authz/opa.go` (`go-common/opaclient`) | OPA sidecar | `TestAC05` |
| JWT verify | `go-common/authjwt` (RS256, JWKS; static key in tests) | identity-service JWKS | all integration tests |
| Ticket audit + routing config | `internal/store` (`pgx`, RLS) | PostgreSQL 16 | migrations; optional in dev |
| Tracing | `go-common/otelx` (OTLP) | otel-collector | wired in `main` |

## Functional-requirement traceability

| FR | M/S | Implementation | Test(s) |
|---|---|---|---|
| RTH-FR-001 SSE + incremental subscribe | M | `api/sse.go`, `api/subscribe.go`, `api/handlers.go` | `TestAC01`, `TestAC03` |
| RTH-FR-002 WebSocket | M | `api/ws.go` | `TestWebSocketSubscribeReceive` |
| RTH-FR-003 topic grammar (4 schemes) | M | `topics/topics.go` | `TestParse_SchemesAndGrammar`, `TestAC05` |
| RTH-FR-004 ids = producer event_id | M | `events/consumer.go`, `fanout/types.go` | `TestAC01`, `TestAC02` |
| RTH-FR-010 JWT verify + token-refresh contract | M | `api/server.go`, `fanout/conn.go`, `api/handlers.go`, `api/ws.go` | `TestAC11_TokenRefreshAndExpiry`, `TestStatic_*` |
| RTH-FR-011 single-use stream tickets | M | `api/tickets.go` | `TestAC12_TicketSingleUse` |
| RTH-FR-012 per-topic OPA authz + fail-closed | M | `authz/opa.go`, `api/subscribe.go` | `TestAC05`, `TestStatic_StructuralRules` |
| RTH-FR-013 revocation via rbac.events.v1 (re-evaluate, terminate only denied) | M | `events/consumer.go`, `fanout/hub.go` (`Revoke` + `Reauthorizer`) | `TestAC06_RevocationViaRbacEvent`, `TestAC06_AdditiveGrantDoesNotRevoke` |
| RTH-FR-020 Kafka fan-in + routing (skip-and-count) | M | `events/consumer.go`, `topics/routing.go` | `TestAC15_RoutingTableContract`, `TestAC02` |
| RTH-FR-021 internal publish API (authenticated, separate listener) | M | `api/internal_publish.go`, `api/server.go` (`InternalRouter`), `fanout/hub.go` | `TestAC08`, `TestSecurity_InternalPublishRequiresProducerAuth` |
| RTH-FR-022 64KB payload cap | M | `events/consumer.go`, `api/internal_publish.go` | enforced in code |
| RTH-FR-030 backpressure: drop-oldest+gap / chat disconnect | M | `fanout/conn.go` | `TestAC07_BackpressureGapAndIsolation`, `TestAC07_ChatDisconnectsOnOverflow` |
| RTH-FR-031 Last-Event-ID resume + reset | M | `fanout/replay.go`, `fanout/hub.go` | `TestAC03`, `TestAC04` |
| RTH-FR-032 per-topic ordering + dedup | M | `fanout/conn.go`, `fanout/replay.go` | `TestAC16_ExactlyOnceDedup`, `TestAC03` |
| RTH-FR-033 heartbeat / reconnect / drain | M | `fanout/conn.go`, `fanout/hub.go` (`Drain`) | `TestAC09_HeartbeatEmitted`, `TestDrain_SendsReconnectThenCloses` |
| RTH-FR-034 per-topic-class QoS table | M | `topics/topics.go` (`QoS`) | `TestQoS_ChatDisconnectsOthersGap` |
| RTH-FR-040 connection caps (user/tenant/pod) | M | `fanout/caps.go`, `api/subscribe.go` | `TestAC10_PerUserConnectionCap` |
| RTH-FR-041 sticky-less scale-out (Redis pub/sub, broadcast Kafka) | M | `fanout/redisbus.go`, `events/consumer.go` | `TestAC02`, `TestAC08` |
| RTH-FR-042 leader lease for replay writes | M | `fanout/lease.go`, `fanout/hub.go` | `TestLeaderLease_SingleWriter` |
| RTH-FR-044 admin API | S | `api/handlers.go` | scope-gated `GET/DELETE /admin/connections` |
| RTH-FR-050 metrics + connect/disconnect logs | M | `internal/metrics`, structured slog | `/metrics` endpoint |
| RTH-FR-035 TypeScript UI SDK | S | **deferred** | frontend package (`@windrose/realtime`) — out of this repo |
| RTH-FR-043 HPA + cell capacity targets | S | **deferred** | Helm/infra concern (dashboards, HPA) |

## Acceptance-criteria coverage

| AC | Test | Real component hit |
|---|---|---|
| AC-1 topics stream, id = event_id | `TestAC01_SSEStreamsTopicsWithProducerIDs` | Redis + OPA |
| AC-2 Kafka status change → client (across pods) | `TestAC02_KafkaFansOutAcrossInstancesThroughRedis` | **Redpanda + Redis + OPA** |
| AC-3 resume with Last-Event-ID | `TestAC03_ResumeAfterLastEventID` | Redis Streams + OPA |
| AC-4 aged-out id → reset | `TestAC04_ResetWhenAgedOut` | Redis Streams + OPA |
| AC-5 per-topic OPA deny (no existence leak) | `TestAC05_PerTopicOPADeny` | **OPA** + Redis |
| AC-6 revocation via rbac event (re-evaluate, terminate only denied) | `TestAC06_RevocationViaRbacEvent`, `TestAC06_AdditiveGrantDoesNotRevoke` | **Redpanda + OPA + Redis** |
| AC-7 slow-client gap / chat 4409 | `TestAC07_BackpressureGapAndIsolation`, `TestAC07_ChatDisconnectsOnOverflow` | (logic) |
| AC-8 100 batches cross pods in order | `TestAC08_InternalPublishCrossPodInOrder` | **Redis pub/sub** |
| AC-9 heartbeat emitted + drain reconnect | `TestAC09_HeartbeatEmitted`, `TestDrain_SendsReconnectThenCloses` | Redis / (logic) |
| AC-10 per-user cap + X-Replace-Oldest | `TestAC10_PerUserConnectionCap` | Redis counters |
| AC-11 token_refresh + 4401 on expiry | `TestAC11_TokenRefreshAndExpiry` | Redis |
| AC-12 ticket single-use (reuse rejected) | `TestAC12_TicketSingleUse` | Redis |
| AC-13 Redis-outage degraded readyz | `TestAC13_ReadyzDegradedWhenRedisDown` | (logic; Redis Ping) |
| AC-15 routing-table contract (every row) | `TestAC15_RoutingTableContract` | (logic) |
| AC-16 exactly-once dedup | `TestAC16_ExactlyOnceDedup` | (logic; Redis XADD dedup in `replay.go`) |
| Internal-publish producer auth (HIGH) | `TestSecurity_InternalPublishRequiresProducerAuth` | Redis + JWT |
| WebSocket transport | `TestWebSocketSubscribeReceive` | Redis |

## Documented deviations (BRD-sanctioned or scoped)

1. **Internal publish transport** (RTH-FR-021): implemented as **HTTP+JSON**
   rather than gRPC. Delivery semantics are identical (idempotent by `event_id`,
   64KB cap, `ttl`, real Redis fan-out). It runs on a **separate listener**
   (`INTERNAL_LISTEN_ADDR`), never the public port, so mesh mTLS can front it,
   **and** every publish is authenticated at the app layer: the caller must
   present a service/agent JWT carrying the `realtime.publish` scope
   (`authenticatePublisher`). User tokens and unauthenticated callers are
   rejected `401` — a browser cannot forge events into any tenant.
2. **No DLQ for fan-out** (RTH-FR-020): unroutable/oversize events are
   skip-and-counted — the hub is transport and never blocks a Kafka partition on
   slow clients (explicit deviation from MASTER-FR-033, per the BRD).
3. **Incremental-subscribe / token-refresh side channels are pod-local**: they
   target the pod holding the connection (the edge routes `POST /stream/{conn_id}/…`
   by `conn_id`); a request landing on another pod returns 404.
4. **Kafka consumer offsets**: broadcast groups (`hub-<pod>`) start at the
   earliest offset (go-common default) and are not the resume source of record —
   resume state lives in the Redis replay buffer (RTH-FR-041), so this is
   behaviorally safe.

## No-stub status

Zero `NotImplementedError` / `ErrNotWired` / `panic("TODO")` / fake adapters are
reachable from `cmd/server`. The only test doubles (`memBus`, `fakeSink`,
`authz.Static`) live in `*_test.go` and are never wired into the runtime.
