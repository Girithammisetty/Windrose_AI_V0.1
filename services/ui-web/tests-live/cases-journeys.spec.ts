import { test, expect, loginAs, logout, expectPageHealthy, PERSONAS } from "./fixtures";
import type { Page } from "@playwright/test";

/**
 * CASES journeys — CRUD, worklist-from-rows, and the create→update edit flows,
 * driven against the LIVE stack with NOTHING mocked.
 *
 * Real chain exercised: real UI (:3000) → real bff-graphql (:4000) →
 * case-service (+ dataset-service for the row browse, rbac/OPA for the
 * capability gates) → Postgres (RLS) / the search index for case search.
 *
 * Fixture strategy: rather than re-deriving the dataset-browse UI in every
 * test, most tests create their one throwaway case directly via the real
 * `createCases` mutation (same technique as hero-learning-loop.spec.ts) and
 * then drive the UI flow actually under test. Test 1 is the exception — it
 * IS the dataset-browse → select rows → create UI flow, so it stays fully
 * UI-driven end to end.
 *
 * Every test tags its fixtures with a unique, timestamp-derived string so the
 * file is idempotent/re-runnable without colliding with a prior run's data.
 */

let tagCounter = 0;
function uniqueTag(prefix: string): string {
  tagCounter += 1;
  return `${prefix}-${Date.now()}-${tagCounter}`;
}

/** Deterministic-ish row offset derived from a fixture tag, so repeated runs
 * tend to land on a fresh (never-before-cased) row instead of always hitting
 * the same one and falling into the dedup path. */
function offsetFromTag(tag: string, totalRows: number): number {
  if (totalRows <= 0) return 0;
  const digits = tag.replace(/\D/g, "").slice(-6) || "0";
  return Number(digits) % totalRows;
}

interface DatasetRef {
  id: string;
  urn: string;
  name: string;
}

interface GqlEnvelope<T> {
  data?: T;
  errors?: Array<{ message: string }>;
}

/** POST straight to the real BFF GraphQL endpoint using the logged-in page's
 * own session cookie — the same authenticated path the UI itself uses. */
async function graphql<T>(
  page: Page,
  query: string,
  variables?: Record<string, unknown>,
): Promise<T> {
  const res = await page.request.post("/api/graphql", { data: { query, variables } });
  const body = (await res.json()) as GqlEnvelope<T>;
  if (body.errors?.length) {
    throw new Error(`GraphQL error for query [${query.slice(0, 60)}...]: ${JSON.stringify(body.errors)}`);
  }
  expect(body.data, "GraphQL response carried neither data nor errors").toBeTruthy();
  return body.data as T;
}

const DATASETS_QUERY = /* GraphQL */ `
  query DatasetsForCasesE2E($first: Int) {
    datasets(first: $first) { nodes { id urn name status } }
  }
`;

const DATASET_ROWS_QUERY = /* GraphQL */ `
  query DatasetRowsForCasesE2E($datasetId: ID!, $offset: Int, $limit: Int) {
    datasetRows(datasetId: $datasetId, offset: $offset, limit: $limit) {
      columns
      rows
      total
    }
  }
`;

const CREATE_CASES_MUTATION = /* GraphQL */ `
  mutation CreateCasesE2E($input: CreateCasesInput!) {
    createCases(input: $input) {
      created { id caseNumber status }
      deduplicated { id rowPk caseNumber }
    }
  }
`;

const CASE_SEARCH_QUERY = /* GraphQL */ `
  query CaseSearchE2E($q: String, $first: Int) {
    caseSearch(q: $q, first: $first) { nodes { id caseNumber } }
  }
`;

const USERS_QUERY = /* GraphQL */ `
  query UsersForCasesE2E($first: Int) {
    users(first: $first) { nodes { id email fullName } }
  }
`;

/** Find a real dataset in the live catalog that actually has browsable rows.
 * The list projection carries no reliable rowCount (BFF-FR gap noted
 * elsewhere in this codebase), so probe datasetRows for the first several
 * candidates and use whichever answers with total > 0. */
async function findDatasetWithRows(
  page: Page,
): Promise<{ dataset: DatasetRef; columns: string[]; totalRows: number }> {
  const { datasets } = await graphql<{ datasets: { nodes: DatasetRef[] } }>(page, DATASETS_QUERY, {
    first: 40,
  });
  for (const d of datasets.nodes.slice(0, 25)) {
    const { datasetRows } = await graphql<{ datasetRows: { columns: string[]; total: number } }>(
      page,
      DATASET_ROWS_QUERY,
      { datasetId: d.id, offset: 0, limit: 3 },
    );
    if (datasetRows.total > 0) {
      return { dataset: d, columns: datasetRows.columns, totalRows: datasetRows.total };
    }
  }
  throw new Error(
    "live stack has no dataset with browsable rows among the first 25 datasets — cannot exercise " +
      "case-worklist creation (dataset-service datasetRows returned total=0 for all of them)",
  );
}

function caseRowInputFromCells(columns: string[], cells: (string | null)[]) {
  return {
    rowPk: String(cells[0] ?? ""),
    displayProjection: columns.map((key, i) => ({ key, value: cells[i] ?? "" })),
  };
}

const FOURTEEN_DAYS_MS = 14 * 86_400_000;

/** Create (or dedup-reuse) exactly one case from a dataset row via the real
 * createCases mutation, bypassing the browse UI for tests that only need a
 * fixture case to act on. Returns the case id either way (dedup reuse is a
 * legitimate outcome on a shared live dataset, not a test bug). */
async function createFixtureCase(
  page: Page,
  dataset: DatasetRef,
  rowOffset: number,
  opts: { severity?: "low" | "medium" | "high" | "critical"; description?: string } = {},
): Promise<{ id: string; caseNumber: number | null }> {
  const { datasetRows } = await graphql<{
    datasetRows: { columns: string[]; rows: (string | null)[][] };
  }>(page, DATASET_ROWS_QUERY, { datasetId: dataset.id, offset: rowOffset, limit: 1 });
  const cells = datasetRows.rows[0];
  expect(cells, `dataset ${dataset.name} must have a row at offset ${rowOffset}`).toBeTruthy();

  const due = new Date(Date.now() + FOURTEEN_DAYS_MS).toISOString().slice(0, 10);
  const { createCases } = await graphql<{
    createCases: {
      created: { id: string; caseNumber: number | null }[];
      deduplicated: { id: string; caseNumber: number | null }[];
    };
  }>(page, CREATE_CASES_MUTATION, {
    input: {
      datasetUrn: dataset.urn,
      dueDate: `${due}T23:59:59Z`,
      severity: opts.severity ?? "medium",
      description: opts.description,
      rows: [caseRowInputFromCells(datasetRows.columns, cells!)],
    },
  });
  const created = createCases.created[0];
  const dedup = createCases.deduplicated[0];
  const id = created?.id ?? dedup?.id;
  expect(id, `createCases must return either a created or deduplicated case: ${JSON.stringify(createCases)}`).toBeTruthy();
  return { id: id as string, caseNumber: created?.caseNumber ?? dedup?.caseNumber ?? null };
}

/** Poll the real search-index-backed caseSearch(q:) until it surfaces at
 * least `expectedCount` matches. The index is populated asynchronously off
 * the case-created event, so a short poll (not an instant assertion) is the
 * honest way to observe it. */
async function waitForSearchable(
  page: Page,
  q: string,
  expectedCount: number,
  budgetMs = 25_000,
): Promise<Array<{ id: string; caseNumber: number | null }>> {
  const deadline = Date.now() + budgetMs;
  let lastCount = 0;
  while (Date.now() < deadline) {
    const { caseSearch } = await graphql<{ caseSearch: { nodes: { id: string; caseNumber: number | null }[] } }>(
      page,
      CASE_SEARCH_QUERY,
      { q, first: 50 },
    );
    lastCount = caseSearch.nodes.length;
    if (lastCount >= expectedCount) return caseSearch.nodes;
    await new Promise((r) => setTimeout(r, 2_000));
  }
  throw new Error(
    `case search index did not surface ${expectedCount} case(s) for q="${q}" within ${budgetMs}ms ` +
      `(last seen: ${lastCount})`,
  );
}

test.describe("cases: worklist creation, detail CRUD, settings edit, bulk ops, RBAC", () => {
  test.setTimeout(120_000);

  test("creates cases from selected dataset rows via the browse UI and links them to the source dataset", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS().admin); // needs case.case.create
    const { dataset } = await findDatasetWithRows(page);

    await page.goto(`/data/datasets/${dataset.id}`);
    await expectPageHealthy(page, { notRedirectedFrom: `/data/datasets/${dataset.id}` });
    await page.getByRole("tab", { name: "data", exact: true }).click();

    const grid = page.getByRole("grid", { name: "Dataset rows" });
    await expect(grid.getByRole("checkbox", { name: "Select 0" })).toBeVisible({ timeout: 20_000 });
    await grid.getByRole("checkbox", { name: "Select 0" }).click();
    await grid.getByRole("checkbox", { name: "Select 1" }).click();

    const createBtn = page.getByRole("button", { name: /^Create 2 cases$/ });
    await expect(createBtn).toBeVisible();
    await createBtn.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("2 rows → 2 cases");

    const [createResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("CreateCases") ?? false),
      ),
      dialog.getByRole("button", { name: /^Create 2 cases$/ }).click(),
    ]);
    const createBody = await createResp.json();
    expect(createBody.errors, `createCases should not error: ${JSON.stringify(createBody.errors)}`).toBeFalsy();
    const { created, deduplicated } = createBody.data.createCases;
    // Either outcome is a legitimate real result on a shared live dataset —
    // what matters is the two selected rows produced two tracked cases.
    expect(created.length + deduplicated.length, `expected 2 rows to map to 2 cases: ${JSON.stringify(createBody.data)}`).toBe(2);

    // The dialog reflects the real (not optimistic) counts.
    await expect(dialog.getByText(new RegExp(`${created.length} case`))).toBeVisible();
    await dialog.getByRole("button", { name: "Done" }).click();
    await expect(dialog).toBeHidden();

    const ids: string[] = [...created.map((c: { id: string }) => c.id), ...deduplicated.map((d: { id: string }) => d.id)];
    for (const id of ids) {
      await page.goto(`/cases/${id}`);
      await expectPageHealthy(page, { notRedirectedFrom: `/cases/${id}` });
      // Correct source-row linkage: the case's own overview links back to
      // exactly the dataset the rows were selected from.
      await expect(page.getByRole("link", { name: dataset.name }).first()).toBeVisible();
    }
  });

  test("case detail CRUD: severity/description/due-date edits persist through a reload", async ({ page }) => {
    const tag = uniqueTag("cd");
    await loginAs(page, PERSONAS().admin);
    const { dataset, totalRows } = await findDatasetWithRows(page);
    const { id: caseId } = await createFixtureCase(page, dataset, offsetFromTag(tag, totalRows), {
      severity: "low",
      description: `seed ${tag}`,
    });

    await page.goto(`/cases/${caseId}`);
    await expectPageHealthy(page, { notRedirectedFrom: `/cases/${caseId}` });
    await page.getByRole("tab", { name: "details" }).click();

    const newDescription = `E2E edited description ${tag}`;
    const newDue = new Date(Date.now() + 21 * 86_400_000).toISOString().slice(0, 10);

    const panel = page.getByRole("tabpanel");
    await panel.locator("select").selectOption("HIGH");
    await panel.getByPlaceholder("Describe the case…").fill(newDescription);
    await panel.locator('input[type="date"]').fill(newDue);

    const [updateResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("UpdateCase") ?? false),
      ),
      panel.getByRole("button", { name: /save changes/i }).click(),
    ]);
    const updateBody = await updateResp.json();
    expect(updateBody.errors, `updateCase should not error: ${JSON.stringify(updateBody.errors)}`).toBeFalsy();
    expect(updateBody.data.updateCase.severity).toBe("HIGH");

    // Reload — read back from the real API, not the optimistic cache.
    await page.reload();
    await expectPageHealthy(page, { notRedirectedFrom: `/cases/${caseId}` });
    await page.getByRole("tab", { name: "details" }).click();
    const panelAfterReload = page.getByRole("tabpanel");
    await expect(panelAfterReload.locator("select")).toHaveValue("HIGH");
    await expect(panelAfterReload.getByPlaceholder("Describe the case…")).toHaveValue(newDescription);
    await expect(panelAfterReload.locator('input[type="date"]')).toHaveValue(newDue);
  });

  test("case field edit: purpose/fieldMeta update via the settings Edit control; name/dataType stay read-only", async ({
    page,
  }) => {
    const tag = uniqueTag("cf");
    const fieldName = `wr_e2e_${tag.replace(/[^a-zA-Z0-9]/g, "_")}`;
    await loginAs(page, PERSONAS().admin);

    await page.goto("/cases/settings");
    await expectPageHealthy(page, { notRedirectedFrom: "/cases/settings" });
    await page.getByRole("tab", { name: "Case fields" }).click();

    // Create the field to edit — fresh + tag-unique, so this run doesn't
    // depend on any pre-existing field.
    await page.getByLabel("Name").fill(fieldName);
    await page.getByLabel("Data type").selectOption("string");
    await page.getByLabel("Purpose").selectOption("both");
    const [createResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("CreateCaseField") ?? false),
      ),
      page.getByRole("button", { name: /create field/i }).click(),
    ]);
    const createBody = await createResp.json();
    expect(createBody.errors, `createCaseField should not error: ${JSON.stringify(createBody.errors)}`).toBeFalsy();

    const row = page.locator('[role="row"]').filter({ hasText: fieldName });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: "Edit" }).click();

    // The WS2-tail Edit control must keep name + dataType read-only.
    await expect(page.getByLabel("Name")).toHaveValue(fieldName);
    await expect(page.getByLabel("Name")).toBeDisabled();
    await expect(page.getByLabel("Data type")).toHaveValue("string");
    await expect(page.getByLabel("Data type")).toBeDisabled();

    await page.getByLabel("Purpose").selectOption("create");
    const meta = { note: `edited ${tag}` };
    await page.getByLabel("Field meta (JSON)").fill(JSON.stringify(meta));

    const [updateResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("UpdateCaseField") ?? false),
      ),
      page.getByRole("button", { name: "Save" }).click(),
    ]);
    const updateBody = await updateResp.json();
    expect(updateBody.errors, `updateCaseField should not error: ${JSON.stringify(updateBody.errors)}`).toBeFalsy();
    expect(updateBody.data.updateCaseField.purpose).toBe("create");
    expect(updateBody.data.updateCaseField.fieldMeta).toEqual(meta);

    // Reload and re-open Edit — confirm the persisted (not optimistic) value.
    await page.reload();
    await expectPageHealthy(page, { notRedirectedFrom: "/cases/settings" });
    await page.getByRole("tab", { name: "Case fields" }).click();
    const rowAfterReload = page.locator('[role="row"]').filter({ hasText: fieldName });
    await expect(rowAfterReload).toBeVisible({ timeout: 15_000 });
    await rowAfterReload.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByLabel("Name")).toHaveValue(fieldName);
    await expect(page.getByLabel("Name")).toBeDisabled();
    await expect(page.getByLabel("Data type")).toHaveValue("string");
    await expect(page.getByLabel("Data type")).toBeDisabled();
    await expect(page.getByLabel("Purpose")).toHaveValue("create");
    await expect(page.getByLabel("Field meta (JSON)")).toHaveValue(JSON.stringify(meta, null, 2));
  });

  test("assign, reassign and comment on a case; the activity timeline reflects both; worklist bulk-assign also works", async ({
    page,
  }) => {
    const tag = uniqueTag("bulk");
    await loginAs(page, PERSONAS().manager); // Case Manager holds case.case.assign
    const { dataset, totalRows } = await findDatasetWithRows(page);
    const { id: caseId } = await createFixtureCase(page, dataset, offsetFromTag(tag, totalRows), {
      severity: "medium",
      description: `seed ${tag}`,
    });

    const { users } = await graphql<{ users: { nodes: { id: string; email: string; fullName?: string }[] } }>(
      page,
      USERS_QUERY,
      { first: 10 },
    );
    expect(users.nodes.length, "live stack must have at least 2 users to exercise reassignment").toBeGreaterThanOrEqual(2);
    const [userA, userB] = users.nodes;

    await page.goto(`/cases/${caseId}`);
    await expectPageHealthy(page, { notRedirectedFrom: `/cases/${caseId}` });

    await test.step("assign", async () => {
      await page.getByRole("button", { name: /^assign$/i }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByLabel("Assign to").selectOption(userA.id);
      const [assignResp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("AssignCase") ?? false),
        ),
        dialog.getByRole("button", { name: "Assign" }).click(),
      ]);
      const body = await assignResp.json();
      expect(body.errors, `assignCase should not error: ${JSON.stringify(body.errors)}`).toBeFalsy();
      expect(body.data.assignCase.assignee?.id).toBe(userA.id);
      await expect(page.getByText(userA.fullName || userA.email)).toBeVisible();
    });

    await test.step("reassign", async () => {
      await page.getByRole("button", { name: /^reassign$/i }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByLabel("Assign to").selectOption(userB.id);
      const [reassignResp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("AssignCase") ?? false),
        ),
        dialog.getByRole("button", { name: "Assign" }).click(),
      ]);
      const body = await reassignResp.json();
      expect(body.errors, `reassign (assignCase) should not error: ${JSON.stringify(body.errors)}`).toBeFalsy();
      expect(body.data.assignCase.assignee?.id).toBe(userB.id);
      await expect(page.getByText(userB.fullName || userB.email)).toBeVisible();
    });

    const commentBody = `E2E comment ${tag}`;
    await test.step("add a comment", async () => {
      await page.getByRole("tab", { name: "activity" }).click();
      await page.getByLabel("Add a comment").fill(commentBody);
      const [addResp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("AddCaseComment") ?? false),
        ),
        page.getByRole("button", { name: /^comment$/i }).click(),
      ]);
      const body = await addResp.json();
      expect(body.errors, `addCaseComment should not error: ${JSON.stringify(body.errors)}`).toBeFalsy();
    });

    await test.step("the activity timeline reflects the assign, reassign and comment", async () => {
      const timeline = page.getByRole("list", { name: "Case activity" });
      await expect(timeline).toBeVisible();
      await expect(timeline).toContainText(commentBody);
      await expect(timeline.getByText(/assign/i).first()).toBeVisible();
    });

    await test.step("worklist bulk-assign: two fresh cases assigned together via the /cases bulk bar", async () => {
      const bulkTag = `${tag}-bulk`;
      const caseA = await createFixtureCase(page, dataset, offsetFromTag(`${bulkTag}-a`, totalRows), {
        severity: "low",
        description: `bulk ${bulkTag}`,
      });
      const caseB = await createFixtureCase(page, dataset, offsetFromTag(`${bulkTag}-b`, totalRows), {
        severity: "low",
        description: `bulk ${bulkTag}`,
      });

      await waitForSearchable(page, `bulk ${bulkTag}`, 2);
      await page.goto(`/cases?q=${encodeURIComponent(`bulk ${bulkTag}`)}`);
      await expectPageHealthy(page, { notRedirectedFrom: "/cases" });

      const grid = page.getByRole("grid", { name: "Cases" });
      await expect(grid.getByRole("checkbox", { name: `Select ${caseA.id}` })).toBeVisible({ timeout: 20_000 });
      await grid.getByRole("checkbox", { name: `Select ${caseA.id}` }).click();
      await grid.getByRole("checkbox", { name: `Select ${caseB.id}` }).click();

      await page.getByRole("button", { name: "Bulk assign" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Assign to").selectOption(userA.id);
      const [bulkResp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("BulkAssignCases") ?? false),
        ),
        dialog.getByRole("button", { name: "Apply" }).click(),
      ]);
      const bulkBody = await bulkResp.json();
      expect(bulkBody.errors, `bulkAssignCases should not error: ${JSON.stringify(bulkBody.errors)}`).toBeFalsy();
      const { succeededIds, failed } = bulkBody.data.bulkAssignCases;
      expect(failed, `some bulk-assigns failed: ${JSON.stringify(failed)}`).toEqual([]);
      expect([...succeededIds].sort()).toEqual([caseA.id, caseB.id].sort());
    });
  });

  test("RBAC: adjuster gets in-scope case read/write, but genuinely gated-out actions are hidden", async ({ page }) => {
    // Ground truth checked against source before writing this test (do not
    // guess): src/lib/authz/registry.ts — the "Case Analyst" role (adjuster
    // persona) holds case.case.read/list/update/disposition.* but NOT
    // case.case.create, case.case.assign, or case.case.admin. So:
    //   - editing severity/description (manageCase / FEATURE_GATES gate =
    //     case.case.update) MUST succeed for adjuster.
    //   - Assign/Reassign (assignCase = case.case.assign), creating cases
    //     from dataset rows (case.case.create), the worklist bulk-assign bar
    //     (bulkAssignCases = case.case.assign), and the SLA policy panel
    //     (manageSlaPolicy = case.case.admin) MUST all be hidden.
    // NOTE: manageCaseFields also resolves to case.case.update, so — unlike
    // what one might assume — Case Analyst is NOT gated out of case-field
    // management; that's a real, verified platform fact, not an oversight,
    // so this test does not (falsely) assert it's hidden.
    const tag = uniqueTag("rbac");
    await loginAs(page, PERSONAS().admin);
    const { dataset, totalRows } = await findDatasetWithRows(page);
    const { id: caseId } = await createFixtureCase(page, dataset, offsetFromTag(tag, totalRows), {
      severity: "low",
      description: `seed ${tag}`,
    });
    await logout(page);

    await loginAs(page, PERSONAS().adjuster);

    await test.step("adjuster can read the case list and an individual case", async () => {
      await page.goto("/cases");
      await expectPageHealthy(page, { notRedirectedFrom: "/cases" });
      await page.goto(`/cases/${caseId}`);
      await expectPageHealthy(page, { notRedirectedFrom: `/cases/${caseId}` });
    });

    await test.step("adjuster CAN edit case severity/description (case.case.update is held)", async () => {
      await page.getByRole("tab", { name: "details" }).click();
      const panel = page.getByRole("tabpanel");
      const newDescription = `adjuster-edited ${tag}`;
      await panel.locator("select").selectOption("HIGH");
      await panel.getByPlaceholder("Describe the case…").fill(newDescription);
      const [resp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/graphql") && (r.request().postData()?.includes("UpdateCase") ?? false),
        ),
        panel.getByRole("button", { name: /save changes/i }).click(),
      ]);
      const body = await resp.json();
      expect(body.errors, `adjuster's updateCase should succeed (holds case.case.update): ${JSON.stringify(body.errors)}`).toBeFalsy();
      expect(body.data.updateCase.severity).toBe("HIGH");
    });

    await test.step("adjuster CANNOT see Assign/Reassign (case.case.assign not granted)", async () => {
      await expect(page.getByRole("button", { name: /^(assign|reassign)$/i })).toHaveCount(0);
    });

    await test.step("adjuster CANNOT create cases from dataset rows (case.case.create not granted)", async () => {
      await page.goto(`/data/datasets/${dataset.id}`);
      await expectPageHealthy(page, { notRedirectedFrom: `/data/datasets/${dataset.id}` });
      await page.getByRole("tab", { name: "data", exact: true }).click();
      const grid = page.getByRole("grid", { name: "Dataset rows" });
      await expect(grid.getByRole("checkbox", { name: "Select 0" })).toBeVisible({ timeout: 20_000 });
      await grid.getByRole("checkbox", { name: "Select 0" }).click();
      await expect(page.getByRole("button", { name: /^Create \d+ cases?$/ })).toHaveCount(0);
    });

    await test.step("adjuster CANNOT see the worklist bulk-assign bar even with rows selected (case.case.assign gate)", async () => {
      await page.goto("/cases");
      await expectPageHealthy(page, { notRedirectedFrom: "/cases" });
      const grid = page.getByRole("grid", { name: "Cases" });
      await expect(grid).toBeVisible({ timeout: 20_000 });
      await grid.getByRole("checkbox").first().click();
      await expect(page.getByRole("button", { name: "Bulk assign" })).toHaveCount(0);
    });

    await test.step("adjuster CANNOT manage the SLA policy (case.case.admin not granted) — real gated-out fallback", async () => {
      await page.goto("/cases/settings");
      await expectPageHealthy(page, { notRedirectedFrom: "/cases/settings" });
      await page.getByRole("tab", { name: "SLA policy" }).click();
      await expect(page.getByText(/need the case admin capability/i)).toBeVisible();
    });
  });
});
