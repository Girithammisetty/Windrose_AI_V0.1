import { randomUUID } from "node:crypto";
import { test, expect, loginAs, PERSONAS } from "./fixtures";
import type { Page } from "@playwright/test";

/**
 * Dataset -> Pipeline -> Run -> Schedule lifecycle journeys, driven against the
 * LIVE stack with nothing mocked. Covers the WS2 create->update edits shipped
 * this session (dataset rename, pipeline template edit, roles edit) plus the
 * Phase 1 pipeline-data-inputs path (a pipeline reading a real dataset's rows
 * through a real transform component) and the WS1 live-status run polling.
 *
 * Each test is self-contained (its own unique `tag`) and creates its own real
 * fixtures through the real UI/API so the file is idempotent and re-runnable,
 * and any single test can be run in isolation.
 *
 * GraphQL is used directly (via `page.request.post("/api/graphql")`, same
 * technique as hero-learning-loop.spec.ts) only for setup/verification that
 * has no dedicated UI surface (discovering/creating a source dataset,
 * capturing a mutation's raw response for the roles version-bump check); the
 * behavior actually under test is always driven through the real UI.
 */

function tag(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

interface GqlResult<T> {
  data: T | null;
  errors: Array<{ message: string }> | undefined;
}

async function gqlRaw<T = any>(
  page: Page,
  query: string,
  variables?: Record<string, unknown>,
): Promise<GqlResult<T>> {
  const resp = await page.request.post("/api/graphql", { data: { query, variables } });
  expect(resp.ok(), `POST /api/graphql transport failed: ${resp.status()}`).toBeTruthy();
  const body = await resp.json();
  return { data: body.data ?? null, errors: body.errors };
}

/** Like {@link gqlRaw} but throws with the real server error on failure — for
 * setup steps where a GraphQL error means the whole test can't proceed. */
async function gql<T = any>(page: Page, query: string, variables?: Record<string, unknown>): Promise<T> {
  const { data, errors } = await gqlRaw<T>(page, query, variables);
  if (errors?.length) throw new Error(`GraphQL error: ${errors.map((e) => e.message).join("; ")}`);
  return data as T;
}

const DATASETS_Q = /* GraphQL */ `
  query Datasets($first: Int) {
    datasets(first: $first) { nodes { id urn name workspaceId status rowCount } pageInfo { hasMore } }
  }
`;
const CONNECTIONS_Q = /* GraphQL */ `
  query Connections($first: Int) {
    connections(first: $first) { nodes { id name connectorType } pageInfo { hasMore } }
  }
`;
const CREATE_INGESTION_M = /* GraphQL */ `
  mutation CreateIngestion($input: CreateIngestionInput!) {
    createIngestion(input: $input) { id status datasetUrn mode }
  }
`;
const INGESTION_Q = /* GraphQL */ `
  query Ingestion($id: ID!) { ingestion(id: $id) { id status datasetUrn } }
`;

interface RealDataset {
  id: string;
  name: string;
  urn: string;
}

/**
 * Discover-or-create a real, tenant-scoped dataset to drive a journey from.
 * Prefers creating a FRESH one via a real query-mode ingestion against an
 * existing connection (self-contained fixture); falls back to reusing an
 * existing dataset in the tenant when no usable connection is configured.
 * Throws NO_DATASET_AVAILABLE only if the tenant has neither — callers turn
 * that into a `test.fixme` with a precise reason rather than a fabricated pass.
 */
async function ensureRealDataset(page: Page, uniq: string): Promise<RealDataset> {
  const conns = await gql<{ connections: { nodes: { id: string; name: string; connectorType: string }[] } }>(
    page,
    CONNECTIONS_Q,
    { first: 10 },
  );

  for (const connection of conns.connections.nodes.slice(0, 3)) {
    const name = `e2e-pipeline-src-${uniq}`;
    const { data, errors } = await gqlRaw<{
      createIngestion: { id: string; status: string; datasetUrn: string | null };
    }>(page, CREATE_INGESTION_M, {
      input: { mode: "query", connectionId: connection.id, statement: "SELECT 1 AS n", newDatasetName: name },
    });
    if (errors?.length || !data) continue; // this connection doesn't support query-mode ingestion; try the next

    const ingestionId = data.createIngestion.id;
    let status = data.createIngestion.status;
    let datasetUrn = data.createIngestion.datasetUrn;
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline && !["completed", "failed", "cancelled", "expired"].includes(status)) {
      await new Promise((r) => setTimeout(r, 2000));
      const poll = await gql<{ ingestion: { status: string; datasetUrn: string | null } }>(page, INGESTION_Q, {
        id: ingestionId,
      });
      status = poll.ingestion.status;
      datasetUrn = poll.ingestion.datasetUrn;
    }
    if (status === "completed" && datasetUrn) {
      return { id: datasetUrn.split("/").pop()!, name, urn: datasetUrn };
    }
    // This connection accepted the ingestion but it didn't complete in budget —
    // fall through to the next connection / the existing-dataset fallback.
  }

  const existing = await gql<{ datasets: { nodes: { id: string; urn: string; name: string; status: string }[] } }>(
    page,
    DATASETS_Q,
    { first: 25 },
  );
  const usable = existing.datasets.nodes[0];
  if (!usable) throw new Error("NO_DATASET_AVAILABLE: no connection could ingest a fresh dataset and the tenant has no existing dataset either.");
  return { id: usable.id, name: usable.name, urn: usable.urn };
}

test.describe("Data -> Pipeline -> Run -> Schedule lifecycle", () => {
  test("dataset rename: EditDatasetDialog renames + surfaces a 409 collision inline @dataset-rename", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const uniq = tag();
    await loginAs(page, PERSONAS().admin);

    let ds: RealDataset;
    try {
      ds = await ensureRealDataset(page, uniq);
    } catch (e) {
      test.fixme(true, `No dataset fixture available to drive the rename test: ${(e as Error).message}`);
      return;
    }

    // A second real dataset to collide the rename against, if one exists.
    // Dataset-name uniqueness is WORKSPACE-scoped, so the collision candidate
    // must be a DIFFERENT-named dataset in the SAME workspace as ds — a
    // same-named dataset in another workspace does NOT 409.
    const others = await gql<{
      datasets: { nodes: { id: string; name: string; workspaceId: string | null }[] };
    }>(page, DATASETS_Q, { first: 20 });
    const dsWorkspace = others.datasets.nodes.find((d) => d.id === ds.id)?.workspaceId;
    const collisionName = others.datasets.nodes.find(
      (d) => d.id !== ds.id && d.workspaceId === dsWorkspace && d.name !== ds.name,
    )?.name;

    await page.goto(`/data/datasets/${ds.id}`);
    await expect(page.getByRole("heading", { name: ds.name, exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const nameInput = dialog.getByLabel("Name", { exact: true });

    if (collisionName) {
      await nameInput.fill(collisionName);
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      // 409 collision (dataset-service PATCH) surfaces inline; dialog stays open.
      await expect(dialog.locator('[data-testid="mutation-error"]')).toBeVisible({ timeout: 15_000 });
      await expect(dialog).toBeVisible();
    } else {
      test.info().annotations.push({
        type: "note",
        description: "Only one dataset exists in the tenant right now — the 409 name-collision sub-check was skipped.",
      });
    }

    const newName = `e2e-renamed-${uniq}`;
    await nameInput.fill(newName);
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
    await expect(page.getByRole("heading", { name: newName, exact: true })).toBeVisible();

    // Persists across a reload — a real PATCH, not just optimistic client state.
    await page.reload();
    await expect(page.getByRole("heading", { name: newName, exact: true })).toBeVisible();
  });

  test("pipeline create -> reads a real dataset through a real transform -> run reaches a terminal status @pipeline-create-run", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    const uniq = tag();
    await loginAs(page, PERSONAS().admin);

    let ds: RealDataset;
    try {
      ds = await ensureRealDataset(page, uniq);
    } catch (e) {
      test.fixme(true, `No dataset fixture available to drive the pipeline: ${(e as Error).message}`);
      return;
    }

    await page.goto("/data/pipelines/new");
    await expect(page.locator('[data-testid="step-palette"]')).toBeVisible();
    const configPanel = page.locator('[data-testid="node-config-panel"]');
    const nodeCard = (name: string) => page.locator('[data-testid="node-card"]').filter({ hasText: name });

    // 1) read-from-warehouse: point it at the real dataset.
    await page.locator('[data-entry="read-from-warehouse"]').click();
    await expect(configPanel).toBeVisible();
    await configPanel.getByLabel("dataset", { exact: true }).selectOption({ label: ds.name });

    // 2) filter-data: a real transform component (data_prep category). Its
    // expression doesn't reference a column name, so it's robust regardless of
    // the source connector's identifier casing.
    await page.locator('[data-entry="filter-data"]').click();
    await configPanel.getByLabel("expression", { exact: true }).fill("1 == 1");

    // 3) write-to-warehouse: materialize the transformed output as a new dataset.
    // output_dataset_name is a restricted_string (Iceberg table name): only
    // [a-zA-Z0-9_\s] — hyphens are rejected, so keep the name underscore-safe.
    const outputName = `e2e_pipeline_out_${uniq.replace(/-/g, "_")}`;
    await page.locator('[data-entry="write-to-warehouse"]').click();
    await configPanel.getByLabel("output_dataset_name", { exact: true }).fill(outputName);

    // Wire the DAG: read.out -> filter.in0 -> write.in0.
    await nodeCard("Read From Warehouse").getByRole("button", { name: /^output port/ }).click();
    await nodeCard("Filter Data").getByRole("button", { name: "input port in0", exact: true }).click();
    await nodeCard("Filter Data").getByRole("button", { name: /^output port/ }).click();
    await nodeCard("Write To Warehouse").getByRole("button", { name: "input port in0", exact: true }).click();

    const pipelineName = `e2e-pipeline-${uniq}`;
    await page.getByLabel("Name", { exact: true }).fill(pipelineName);

    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await expect(page.locator('[data-testid="builder-banner"]')).toHaveText("Pipeline is valid.", {
      timeout: 20_000,
    });

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator('[data-testid="builder-banner"]')).toHaveText("Pipeline saved.", { timeout: 20_000 });

    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator('[data-testid="builder-banner"]')).toContainText("Run started", { timeout: 20_000 });

    // Live-status: the runs page polls (WS1 realtime work) until every loaded
    // run is terminal — no manual refresh. Bounded budget so a stuck run FAILS
    // the test clearly rather than hanging forever.
    await page.goto("/data/pipelines/runs");
    const row = page.locator('[role="row"]').filter({ hasText: pipelineName });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(async () => (await row.innerText()).toLowerCase(), { timeout: 150_000, intervals: [3_000] })
      .toMatch(/succeeded|failed|cancelled|expired/);
  });

  test("pipeline template edit: builder rehydrates the saved definition and persists an edit; system templates hide Edit @pipeline-edit", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const uniq = tag();
    await loginAs(page, PERSONAS().admin);

    let ds: RealDataset;
    try {
      ds = await ensureRealDataset(page, uniq);
    } catch (e) {
      test.fixme(true, `No dataset fixture available to drive the pipeline: ${(e as Error).message}`);
      return;
    }

    // Create a minimal real 2-node template directly (this test's focus is the
    // EDIT/rehydrate/persist path, not authoring — that's @pipeline-create-run).
    const definition = {
      nodes: [
        {
          alias: "read_0",
          component: "read-from-warehouse",
          parameters: { dataset: ds.urn },
          outputs: [{ name: "out", type: "dataframe" }],
        },
        {
          alias: "write_0",
          component: "write-to-warehouse",
          parameters: { output_dataset_name: `e2e-edit-out-${uniq}` },
          outputs: [],
        },
      ],
      edges: [{ from: "read_0.out", to: "write_0.in0", type: "dataframe" }],
    };
    const CREATE_PIPELINE_M = /* GraphQL */ `
      mutation CreatePipeline($input: CreatePipelineInput!, $idempotencyKey: String!) {
        createPipeline(input: $input, idempotencyKey: $idempotencyKey) { id name }
      }
    `;
    const originalName = `e2e-edit-pipeline-${uniq}`;
    const created = await gql<{ createPipeline: { id: string; name: string } }>(page, CREATE_PIPELINE_M, {
      input: { name: originalName, pipelineType: "data_prep", definition },
      idempotencyKey: randomUUID(),
    });
    const templateId = created.createPipeline.id;

    await page.goto(`/data/pipelines/${templateId}/edit`);
    const canvas = page.locator('[data-testid="pipeline-canvas"]');
    // Rehydration: both saved nodes appear on the canvas without us adding them.
    await expect(canvas.locator('[data-testid="node-card"]')).toHaveCount(2, { timeout: 20_000 });
    await expect(canvas.locator('[data-testid="node-card"]').filter({ hasText: "Read From Warehouse" })).toBeVisible();
    await expect(canvas.locator('[data-testid="node-card"]').filter({ hasText: "Write To Warehouse" })).toBeVisible();
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue(originalName);

    // A small real change: rename the template and update.
    const newName = `${originalName}-renamed`;
    await page.getByLabel("Name", { exact: true }).fill(newName);
    await page.getByRole("button", { name: "Update", exact: true }).click();
    await expect(page.locator('[data-testid="builder-banner"]')).toHaveText("Pipeline updated.", { timeout: 20_000 });

    // Persists: navigate away and back to the list (a real PATCH).
    await page.goto("/data/pipelines");
    await expect(page.locator('[role="row"]').filter({ hasText: newName })).toBeVisible({ timeout: 20_000 });

    // Immutable-by-design: a system/template pipeline correctly hides Edit.
    // Match the exact "system" BADGE (not a substring of a row that merely
    // contains the word — e.g. a pipeline named "…system…").
    const systemRow = page
      .locator('[role="row"]')
      .filter({ has: page.getByText("system", { exact: true }) })
      .filter({ hasNotText: "archived" })
      .first();
    if ((await systemRow.count()) === 0) {
      test.info().annotations.push({
        type: "note",
        description: "No non-archived system pipeline template exists in this tenant right now — the immutable-Edit-hidden check was skipped.",
      });
    } else {
      await expect(systemRow).toBeVisible({ timeout: 20_000 });
      await expect(systemRow.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    }
  });

  test("pipeline schedule: create -> run now -> pause/resume -> delete, each transition via the real API @pipeline-schedule", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const uniq = tag();
    await loginAs(page, PERSONAS().admin);

    const TEMPLATES_Q = /* GraphQL */ `
      query T($first: Int) {
        pipelineTemplates(first: $first) { nodes { id name archived } }
      }
    `;
    const templates = await gql<{ pipelineTemplates: { nodes: { id: string; name: string; archived: boolean | null }[] } }>(
      page,
      TEMPLATES_Q,
      { first: 25 },
    );
    // Prefer a real seeded template (runnable/validated) over leftover
    // `e2e-*` artifacts from prior runs, which are drafts — "run now" on a draft
    // correctly refuses ("active version is a draft"), so scheduling one would
    // fail the run-now step for the wrong reason.
    const target =
      templates.pipelineTemplates.nodes.find((t) => !t.archived && !t.name.startsWith("e2e-")) ??
      templates.pipelineTemplates.nodes.find((t) => !t.archived);
    if (!target) {
      test.fixme(true, "No pipeline template exists in this tenant to attach a schedule to.");
      return;
    }

    await page.goto("/data/pipelines/schedules");
    await page.getByRole("button", { name: "New schedule", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    const scheduleName = `e2e-schedule-${uniq}`;
    await dialog.getByLabel("Pipeline", { exact: true }).selectOption(target.id);
    await dialog.getByLabel("Name", { exact: true }).fill(scheduleName);
    await dialog.getByLabel("Cron expression", { exact: true }).fill("0 3 * * *");
    await dialog.getByRole("button", { name: "Create schedule", exact: true }).click();
    await expect(dialog).toBeHidden({ timeout: 20_000 });
    await expect(page.locator('[data-testid="notice-banner"]')).toHaveText("Schedule created.", { timeout: 20_000 });

    const row = page.locator('[role="row"]').filter({ hasText: scheduleName });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row.getByText("enabled", { exact: true })).toBeVisible();

    // run now — a real, immediate pipeline run.
    await row.getByRole("button", { name: "Run now", exact: true }).click();
    await expect(page.locator('[data-testid="notice-banner"]')).toHaveText("Fired — pipeline run created.", {
      timeout: 20_000,
    });

    // pause
    await row.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.locator('[data-testid="notice-banner"]')).toHaveText("Schedule paused.", { timeout: 20_000 });
    await expect(row.getByText("paused", { exact: true })).toBeVisible();
    await expect(row.getByRole("button", { name: "Run now", exact: true })).toBeDisabled();

    // resume
    await row.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(page.locator('[data-testid="notice-banner"]')).toHaveText("Schedule resumed.", { timeout: 20_000 });
    await expect(row.getByText("enabled", { exact: true })).toBeVisible();

    // delete
    await row.getByRole("button", { name: "Delete", exact: true }).click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toBeVisible();
    await confirmDialog.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.locator('[data-testid="notice-banner"]')).toHaveText("Schedule deleted.", { timeout: 20_000 });
    await expect(row).toBeHidden({ timeout: 20_000 });
  });

  test("roles edit: unified PATCH edits name+actions; system roles hide Edit; version bumps only on action changes @roles-edit", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const uniq = tag();
    await loginAs(page, PERSONAS().admin);

    const CREATE_ROLE_M = /* GraphQL */ `
      mutation CreateRole($input: CreateRoleInput!) {
        createRole(input: $input) { id name system version actions }
      }
    `;
    const roleName = `e2e-role-${uniq}`;
    const created = await gql<{ createRole: { id: string; name: string; version: number | null; actions: string[] } }>(
      page,
      CREATE_ROLE_M,
      { input: { name: roleName, actions: ["dataset.dataset.read", "dataset.profile.read"] } },
    );
    const originalVersion = created.createRole.version;
    expect(originalVersion, "a freshly created role should carry a version").not.toBeNull();

    await page.goto("/admin/roles");
    const row = page.locator('[role="row"]').filter({ hasText: roleName });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.click();

    // --- name-only edit: must NOT bump the version -----------------------
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const dialog1 = page.getByRole("dialog");
    await expect(dialog1.getByText("Edit role", { exact: true })).toBeVisible();
    const renamedTo = `${roleName}-renamed`;
    await dialog1.getByLabel("Name", { exact: true }).fill(renamedTo);
    // Actions textarea is left untouched (same action set) on purpose.
    const [nameEditResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("UpdateRole") ?? false),
      ),
      dialog1.getByRole("button", { name: "Save changes", exact: true }).click(),
    ]);
    const nameEditBody = await nameEditResp.json();
    const afterNameEdit = nameEditBody?.data?.updateRole;
    expect(afterNameEdit, `updateRole response: ${JSON.stringify(nameEditBody)}`).toBeTruthy();
    expect(afterNameEdit.name).toBe(renamedTo);
    expect(afterNameEdit.version, "a name-only edit must not bump the role version").toBe(originalVersion);
    await expect(dialog1).toBeHidden({ timeout: 15_000 });

    // --- action-set edit: MUST bump the version ---------------------------
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const dialog2 = page.getByRole("dialog");
    await expect(dialog2.getByText("Edit role", { exact: true })).toBeVisible();
    const actionsField = dialog2.getByLabel("Actions (one per line)", { exact: true });
    const currentActions = await actionsField.inputValue();
    await actionsField.fill(`${currentActions}\ncase.case.read`);
    const [actionsEditResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("UpdateRole") ?? false),
      ),
      dialog2.getByRole("button", { name: "Save changes", exact: true }).click(),
    ]);
    const actionsEditBody = await actionsEditResp.json();
    const afterActionsEdit = actionsEditBody?.data?.updateRole;
    expect(afterActionsEdit, `updateRole response: ${JSON.stringify(actionsEditBody)}`).toBeTruthy();
    expect(afterActionsEdit.actions).toContain("case.case.read");
    expect(
      afterActionsEdit.version,
      "an action-set change must bump the role version",
    ).toBeGreaterThan(afterNameEdit.version);
    await expect(dialog2).toBeHidden({ timeout: 15_000 });

    // --- immutable-by-design: a SYSTEM role hides Edit/Delete -------------
    // Exact "system" badge match, not a substring of a role whose name/actions
    // merely contain the word.
    const systemRow = page.locator('[role="row"]').filter({ has: page.getByText("system", { exact: true }) }).first();
    await expect(systemRow).toBeVisible({ timeout: 20_000 });
    await systemRow.click();
    // The selected-role detail panel shows the immutability notice. Match its
    // specific copy — the page-level description also contains "immutable".
    await expect(page.getByText(/reject every mutation/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Delete", exact: true })).toHaveCount(0);
  });
});
