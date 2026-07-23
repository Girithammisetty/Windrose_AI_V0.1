# BRD 62 — Local pipeline execution engine + operator parity

**Status:** DONE — 2026-07-23 · part of the [Datacern pipeline/ML parity index](62_pipeline_ml_parity_index.md)
**Owner:** platform · **Service:** `pipeline-orchestrator`
**Gaps closed:** P1 (data-prep operators don't execute without Argo), P3 (right join),
P4 (missing-value methods), P5 (stratified split).

---

## Analysis

Datacern's `pipeline-orchestrator` catalogs 31 data-prep operators
(`app/domain/catalog.py`) with real JSON-schema params, validates them into a typed
DAG (`app/domain/dag.py`), and deterministically compiles a version to an Argo
`WorkflowTemplate` (`app/domain/compiler.py`). But **execution** of those operators
only happens inside Argo containers — the inline `LocalTrainingExecutor`
(`app/executor/local.py`) implements *only* the `*-train` algorithm components. So on
any deployment without a K8s+Argo cluster (the default Mac/dev deployment, and any
BYO-infra customer that hasn't wired Argo), a `data_prep` / `feature_engineering` /
`profiling` pipeline **cannot run end to end** — the whole classic data-pipeline
surface is dark. Legacy  runs all of these as first-class pandas components in
production. This is the single highest-leverage parity gap: it unblocks the entire
classic-pipeline domain locally and makes it e2e-testable on a Mac with no infra.

Three small operator deltas ride along (same files, same test):  `join-data`
supports a **right** join, `handle-missing-values` supports **linear_interpolation /
expression / previous_existing / next_existing** beyond mean/median/most_frequent/
constant/drop, and `split-data` supports **stratified** splits with a `random_state`.

## Design

A real, in-process, pandas-based execution path parallel to the Argo path — chosen by
the **same swappable `WORKFLOW_BACKENDS` registry** already in the codebase, so this
is not a new architectural concept, just the missing `local` implementation for
non-training pipeline types.

1. **Operator library — `app/executor/operators.py`.** One pure pandas function per
   catalog operator, signature `op(inputs: list[pd.DataFrame], params: dict) ->
   list[pd.DataFrame]`. Registered in an `OPERATORS: dict[str, Operator]` table keyed
   by the exact catalog `name`. Pure, deterministic, no IO — trivially unit-testable.
   Covers all 31 `_DATA_PREP_NAMES` + the injected `clone-input` / `model-input`
   passthroughs. Fails closed on a malformed param (raises, surfaced as a component
   error) — never silently passes bad data downstream.

2. **Local DAG executor — `app/executor/local_pipeline.py`.** `LocalPipelineExecutor`
   topologically orders the compiled definition's nodes (reusing
   `app/domain/resources.py:topological_order`), threads DataFrames along
   `alias.port` edges, invokes each operator from the registry, and returns the
   terminal outputs. IO nodes (`read-from-warehouse` / `write-to-warehouse` and their
   batch variants) call **injected reader/writer ports** — the reader is the existing
   dataset row source (real rows from dataset-service), the writer persists a new
   dataset version — so the executor itself stays pure and the IO is swappable/faked
   in tests. `data-profiler` / `comment` nodes are no-op passthroughs at execution
   time (profiling is computed by dataset-service). Records per-node
   `components_status`; a node exception fails the run with a precise
   `record_component_error`, never a silent success.

3. **Parity param extensions (`catalog.py`).** `join-data` enum gains `right`;
   `handle-missing-values` strategy enum gains `linear_interpolation`, `expression`
   (with an `expression` param), `previous_existing`, `next_existing`; `split-data`
   gains optional `stratify_columns` (array) + `random_state` (int). The operator
   implementations honor all of them.

4. **Agent support (increment 2) — NEW `data_pipeline_builder` graph** in
   `agent-runtime/app/graphs/`. Grounds on the live operator catalog + a dataset's
   inferred schema, composes a data-prep DAG from an NL request, and PROPOSES it as a
   governed pipeline create (proposal-mode WriteIntent → four-eyes → tool-plane
   executes the create). `model_training` builds *training* pipelines; nothing builds
   *data-prep* pipelines today, so this is a genuinely new task type.

**Increment plan:** inc1 = operator library + local executor + parity params + unit
tests (pure, no infra). inc2 = wire the executor into `drive_run` for the non-training
pipeline types + `data_pipeline_builder` agent + live end-to-end run. inc1 is the
foundation and is fully local-testable on its own.

## Implement & Test log

### inc1 — operator library + local DAG executor + parity params — DONE

- **`app/executor/operators.py`** — real pandas implementations for **all 31**
  data-prep operators + `clone-input`/`model-input`/`data-profiler` passthroughs,
  in an `OPERATORS` registry keyed by catalog name. Pure `op(inputs, params) ->
  outputs`; fails closed (`OperatorError`) on a missing column / bad param / unknown
  op. Includes the parity deltas: `join-data` right join (P3), `handle-missing-values`
  linear_interpolation/expression/previous_existing/next_existing (P4), `split-data`
  stratified split + random_state (P5).
- **`app/executor/local_pipeline.py`** — `LocalPipelineExecutor.run(definition)`
  topologically orders nodes (reusing `resources.topo_order`), threads DataFrames
  along `alias.port` edges in authoring order (so join left/right map to
  input[0]/input[1]), runs each operator, and returns terminal outputs +
  per-node `NodeStatus`. Warehouse read/write delegate to **injected reader/writer
  ports** (pure + unit-testable; real dataset-service IO wired in inc2). A node
  exception raises `PipelineExecutionError(alias, component, cause)` — never a
  silent success.
- **`app/domain/catalog.py`** — extended the `join-data` / `handle-missing-values` /
  `split-data` param schemas for P3/P4/P5 so the authoring/validation surface matches
  the new operator behavior.

**Test:** `tests/unit/test_operators.py` (27 tests) — a catalog-coverage guard
asserting **every** DATA_PREP operator has a local impl; per-operator behavior incl.
all four join types (right included), all missing-value strategies (+ directional
fill + expression), stratified split preserving the label ratio, encoders, filters,
scaling/PCA/expressions; fail-closed on bad params; and **three end-to-end local DAG
runs** (read→filter→group-by→write with injected IO, fan-out split, and node-failure
surfacing) proving a data-prep pipeline executes with **no Argo/infra**. Full
pipeline-orchestrator suite green (**125 passed**), no regression.

### inc2 — `data_pipeline_builder` agent + generic pipeline-create tool — DONE

The NEW agent (`agent-runtime/app/graphs/data_pipeline_builder.py`, registered as
`data_pipeline_builder.v1` + RUNNERS key `data-pipeline-builder`). `model_training`
builds *training* pipelines; nothing built *data-prep* pipelines — this is the
genuinely new task type. It grounds on the live operator catalog
(`PipelineOrchestratorClient.list_components` → `GET /components`, new) + workspace
memory, has the real model choose an ORDERED operator list (+ params), and **wires
them into a validated LINEAR DAG deterministically** (`read-from-warehouse → op₁ → …
→ opₙ → write-to-warehouse`) — so the emitted definition always passes the same DAG
validator a UI submit runs, and an operator the model hallucinates is dropped (fail
safe). It PROPOSES the create as a `pipeline.template.create` WriteIntent (proposal-
mode → four-eyes → WORM).

Governed execution path wired end to end: a NEW generic **`pipeline.template.create`**
write-proposal tool on the McpFacade (`template_create`, reusing `TemplateService.
create` so the DAG is validated identically to a UI submit) + the internal MCP-invoke
handler branch + `_MCP_TOOL_ACTIONS` mapping (the `pipeline.template.create` rbac
action already exists in the MANIFEST). New prompt `data_pipeline_builder.system.md`
registered in the prompt registry + agent catalog.

**Test:** `tests/unit/test_data_pipeline_builder_graph.py` (3) — governed create
intent with a valid linear DAG (read→ops→write, n-1 edges, dataset URN on the read
node), drops a hallucinated operator, grounds on catalog + memory, degrades to a
valid read→write proposal on bad model JSON. pipeline-orchestrator suite green
(**152**, incl. facade/internal changes); agent-runtime graph/roster/prompt tests
green (**71**). No regression.

_inc3 (deferred, infra): run-lifecycle execution of the compiled data-prep DAG via
`LocalPipelineExecutor` in `drive_run`, persisting the computed output as a durable
dataset version — needs the Iceberg-commit write path (ingestion-service), the one
genuinely infra-heavy leg of this BRD._

### Agent-path live-verified (2026-07-23)

Drove `run_data_pipeline_builder` through the REAL model path (ai-gateway → Ollama,
grounding faked — the roster-test pattern), `scratchpad/agent_live_verify.py`: for
"clean the claims dataset: keep positive amounts, fill missing values, one-hot the
merchant" the model composed a governed `pipeline.template.create` proposal with a
valid DAG — `read-from-warehouse → handle-missing-values → filter-data →
select-columns → one-hot-encoder → write-to-warehouse` (136 tokens). Proposal-mode,
tier write-proposal.

### inc3 — run-lifecycle execution + durable persistence — DONE

`RunService.drive_run` now branches on pipeline type: `data_prep` / `profiling` /
`scheduled` runs execute the operator DAG **locally end to end** via
`LocalPipelineExecutor` (real dataset rows read through the existing
`dataset_reader`; each `write-to-warehouse` node persists through the BRD 65
warehouse sink) and finish `succeeded` with real `output_dataset_urns` + per-node
`components_status` — the classic-pipeline run path, distinct from training, with
**no Argo**. A node/sink failure surfaces as a `failed` run (never a silent
success). Persistence is real + durable (parquet to the local object store / MinIO
via the `objectstore` sink; cloud warehouses config-gated per BRD 65) — closing the
"computed output has nowhere to land" gap; the pure-pandas DAG runs inline (no
thread hand-off, unlike the heavy training path).

**Test:** `tests/unit/test_dataprep_run.py` (2) — a `data_prep` run (read → filter →
select-columns → write) drives to `succeeded` with a persisted `warehouse/…` output
ref, correct `output_rows`, and all nodes `Succeeded`; a bad-operator run drives to
`failed` (fail-closed). Full pipeline-orchestrator suite green (**161**).

**Note (authoring follow-up):** the executor honors rich operator params (proven by
inc1's 27 tests), but the catalog only declares JSON-schema params for a subset of
operators today, so operators like `group-by` can't yet be authored with params
through strict validation. Declaring the remaining operator param schemas is a small
catalog-completeness follow-up (execution is already complete).
