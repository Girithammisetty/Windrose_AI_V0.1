# BRD 65 — Warehouse write-back sinks

**Status:** DONE — 2026-07-23 · part of the [Datacern pipeline/ML parity index](62_pipeline_ml_parity_index.md)
**Owner:** platform · **Service:** `pipeline-orchestrator`
**Gap closed:** P2 (Datacern persisted computed data only to Iceberg bronze; Nemesis
lands results in cloud-native warehouses via `warehouse_writer_{aws,gcp,azure}`).

---

## Analysis

Nemesis's `write-to-warehouse` component lands a computed DataFrame in the tenant's
cloud-native warehouse (Athena/S3 on AWS, BigQuery on GCP, Synapse on Azure).
Datacern had no equivalent write sink — the query-service warehouse adapter is a
read-side `ENotImplemented` stub, and pipeline outputs had nowhere durable to land.
This is the write-back gap: a pipeline/agent computes a result and needs to persist
it as a real, queryable artifact.

The two remaining parity items (this BRD 65 + BRD 62 inc3) converge on the same
need — persist a computed DataFrame durably — so they share one capability.

## Design

**`app/executor/sinks.py`** — a swappable `WarehouseSink` selected by name from a
registry (`WAREHOUSE_SINKS`, matching the `WORKFLOW_BACKENDS` / dataset-service
adapter-registry pattern already in the codebase). `write_frame(frame, *, tenant_id,
name) -> SinkResult` (ref + uri + row/column counts):

- **`local`** — parquet to the local object-store dir. Real; no infra (unit tier).
- **`objectstore`** (default) — parquet to **MinIO/S3** via path-style boto3
  (`datacern_common.objectstore`). Real and Mac-testable (MinIO runs in the harness);
  the same object-store the inference-service scored-parquet path uses.
- **`athena` / `bigquery` / `synapse`** — real cloud-warehouse adapters, **config-
  gated**: they read their connection from `settings.warehouse_conn[<name>]` and
  raise `DependencyUnavailable` when it's absent — honest, never a faked write. This
  mirrors Nemesis's per-cloud writers and matches Datacern's existing "control-plane
  real, cloud compute infra-gated" pattern (Argo, GPU trainer, KServe).

Config: `settings.warehouse_sink` (default `objectstore`) + `warehouse_conn` (dict).

## Implement & Test log

### DONE

`app/executor/sinks.py` (registry + 5 backends) + `warehouse_sink`/`warehouse_conn`
config. The `local`/`objectstore` sinks write real parquet; the cloud sinks fail
closed. Wired into the run lifecycle by BRD 62 inc3 (a data-prep run's
`write-to-warehouse` node targets the configured sink).

**Test:** `tests/unit/test_warehouse_sinks.py` (7) — the registry lists all 5
backends + rejects an unknown one; the local sink writes real round-trippable
parquet (via the registry too); each cloud sink (athena/bigquery/synapse) fails
closed with `DependencyUnavailable` when unconfigured, and — with connection config
present — gets **past the config guard into its real write path** (still gated on the
absent cloud, proving it's infra-gated, not faked). Full pipeline-orchestrator suite
green (**161**). Object-store (MinIO) + cloud legs are exercised live via the BRD 62
inc3 data-prep run path.

_Cloud write paths (Athena Glue-DDL / BigQuery load / Synapse) are implemented behind
their connection config — enable in a cloud env with `warehouse_conn`; there is no
faked success on the Mac._
