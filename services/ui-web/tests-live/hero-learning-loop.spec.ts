import { test, expect, loginAs, logout, PERSONAS } from "./fixtures";
import type { Page, APIRequestContext } from "@playwright/test";

/**
 * HERO end-to-end journey — the human-correction → learning-loop (the product
 * differentiator), driven against the LIVE stack with NOTHING mocked.
 *
 * Thesis: an AI proposes a decision on a claim case; a HUMAN CORRECTS it (edits
 * the proposed value rather than blindly approving); that correction is captured
 * as the learning signal — a durable, field-level (input → corrected-output)
 * record that feeds the SLM distillation loop.
 *
 * Real chain exercised on a HEALTHY stack:
 *   real UI (:3000) → real bff-graphql (:4000) → agent-runtime (:8306,
 *   case-triage LangGraph → ai-gateway → Ollama → Temporal HITL workflow) →
 *   case-service / rbac / OPA / Postgres (RLS). The proposal is originated by a
 *   REAL case-triage run; the correction goes through the REAL
 *   decideProposal(EDIT_ARGS) mutation from the inbox UI; the persisted
 *   correction is read back through the REAL API.
 *
 * ── Origination note ──────────────────────────────────────────────────────
 * The copilot drawer CANNOT originate a triage proposal —
 * src/app/api/copilot/message/route.ts explicitly refuses `case-triage` (it
 * requires a case_id and "has its own triage surface"), and no GraphQL mutation
 * starts a triage run. So the proposal is originated the real way by invoking
 * the published `case-triage` agent directly on agent-runtime, authenticated
 * with the REAL RS256 session JWT minted for the logged-in persona (read from
 * the httpOnly `wr_session` cookie). The live stack runs AR_USE_TEMPORAL=true,
 * so the run returns a workflow handle and the proposal is created
 * asynchronously by the durable HITL workflow — we poll agent-runtime for it.
 *
 * ── Personas ──────────────────────────────────────────────────────────────
 * Originated on-behalf-of the ADJUSTER, corrected/decided by the MANAGER. Two
 * personas is required, not cosmetic: ProposalService's self-approval guard
 * (ART-FR-044) forbids the on-behalf-of user approving their own proposal.
 *
 * ── Where the learning signal lives (real product finding) ─────────────────
 * The human correction is asserted where the live (Temporal) stack ACTUALLY
 * persists it: the proposal's own decision record — `decision.action ==
 * "edit_args"`, `decision.diff` (field-level AI→human change), and
 * `decision.edited_args` (the corrected output). This is real and durable.
 *
 * The dedicated SLM transcript sink (agent_transcripts.corrected_output, the
 * curated SFT corpus) is verified as a secondary, best-effort step. PRODUCT GAP
 * (see report): TranscriptSink.capture is wired ONLY into the inline run path
 * (run_engine.execute); the Temporal workflow path the live stack runs
 * (activities.run_graph → create_proposal → …) never calls it, so in Temporal
 * mode the EDIT correction is never joined into agent_transcripts. The step
 * detects this and records it as an annotation rather than asserting a
 * fabricated pass.
 *
 * ── Known live-stack blocker on THIS environment (see report) ──────────────
 * If the live stack cannot produce a fresh, current-tenant, decidable proposal
 * — e.g. ai-gateway rejects the triage LLM call (observed: KEY_INVALID,
 * "virtual key is invalid or revoked", for agent-runtime's provisioned virtual
 * key) so the workflow never creates a proposal — the human-correction tail is
 * `test.fixme`-skipped with a precise reason rather than faked green. Pre-seeded
 * pending proposals are NOT a usable fallback: they are orphaned from a prior
 * tenant epoch (their case URNs carry a different tenant id and no workspace_id)
 * and decideProposal returns PERMISSION_DENIED ("approver lacks permission").
 */

const AGENT_RUNTIME_URL =
  process.env.E2E_LIVE_AGENT_RUNTIME_URL ?? process.env.AGENT_RUNTIME_URL ?? "http://localhost:8306";

const CASE_SEARCH = /* GraphQL */ `
  query HeroCaseSearch($first: Int) {
    caseSearch(first: $first) { nodes { id status } }
  }
`;

interface AgentProposal {
  id: string;
  run_id: string;
  status: string;
  args?: Record<string, unknown>;
}

/** Read the REAL RS256 user JWT out of the httpOnly session cookie set by login.
 * Playwright can see httpOnly cookies; page JS never can. */
async function sessionJwt(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const c = cookies.find((ck) => ck.name === "wr_session");
  expect(c, "a wr_session cookie must be set after login").toBeTruthy();
  expect(c!.value.length, "session JWT must be non-empty").toBeGreaterThan(20);
  return c!.value;
}

/** Poll agent-runtime for the PENDING proposal created by our own triage run
 * (matched by run_id, so it is guaranteed current-tenant + decidable). Returns
 * null if it never appears within the budget (degraded LLM path). */
async function waitForOwnProposal(
  req: APIRequestContext,
  jwt: string,
  runId: string,
  budgetMs: number,
): Promise<AgentProposal | null> {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    const r = await req.get(
      `${AGENT_RUNTIME_URL}/api/v1/proposals?filter[status]=pending&limit=200`,
      { headers: { authorization: `Bearer ${jwt}` } },
    );
    if (r.ok()) {
      const body = await r.json();
      const rows = (body?.data ?? []) as AgentProposal[];
      const mine = rows.find((p) => p.run_id === runId);
      if (mine) return mine;
    }
    await new Promise((res) => setTimeout(res, 3000));
  }
  return null;
}

test.describe("HERO: human-correction → learning loop", () => {
  // Real LLM + durable HITL workflow are slower than a normal live route.
  test.setTimeout(240_000);

  test("an AI triage proposal is corrected by a human and the correction is captured", async ({
    page,
  }) => {
    const tag = Date.now(); // unique per run → self-contained, idempotent fixtures

    // ── 1. Login as the persona that originates the case-triage proposal ──────
    await loginAs(page, PERSONAS().adjuster);
    const adjusterJwt = await sessionJwt(page);

    const caseId = await test.step("discover a real case to triage", async () => {
      const caseResp = await page.request.post("/api/graphql", {
        data: { query: CASE_SEARCH, variables: { first: 25 } },
      });
      expect(caseResp.ok(), `caseSearch failed: ${caseResp.status()}`).toBeTruthy();
      const caseJson = await caseResp.json();
      const cases: Array<{ id: string; status: string }> = caseJson?.data?.caseSearch?.nodes ?? [];
      const target = cases.find((c) => c.status !== "CLOSED") ?? cases[0];
      expect(target, "the live stack must have at least one case to triage").toBeTruthy();
      return target!.id;
    });

    // ── 2. Originate an AI proposal via a REAL case-triage run ────────────────
    // Real model call via ai-gateway → Ollama; Temporal mode returns a workflow
    // handle and creates the proposal asynchronously. This step passes as long
    // as agent-runtime accepts the run — a genuine UI-auth → agent-runtime →
    // Temporal assertion — even if the downstream workflow later degrades.
    const runId = await test.step("start a real case-triage run (returns a workflow/run handle)", async () => {
      const triageResp = await page.request.post(
        `${AGENT_RUNTIME_URL}/api/v1/agents/case-triage/chat/completions`,
        {
          headers: { authorization: `Bearer ${adjusterJwt}`, "content-type": "application/json" },
          data: {
            messages: [{ role: "user", content: `Triage this claim (hero-loop ${tag}).` }],
            metadata: { case_id: caseId },
          },
          timeout: 90_000,
        },
      );
      expect(
        triageResp.ok(),
        `case-triage run was not accepted (${triageResp.status()}): ${await triageResp.text()}`,
      ).toBeTruthy();
      const triageJson = await triageResp.json();
      const rid = triageJson?.data?.run_id ?? "";
      expect(rid, `triage run must return a run_id: ${JSON.stringify(triageJson)}`).toBeTruthy();
      return rid as string;
    });

    // The durable HITL workflow creates the proposal a beat later. On a healthy
    // stack it appears within the budget; if the LLM path is degraded it never
    // does and we cannot exercise the correction without faking it.
    const proposal = await waitForOwnProposal(page.request, adjusterJwt, runId, 90_000);

    test.fixme(
      proposal === null,
      "BLOCKED: the live stack did not produce a decidable pending proposal from a fresh " +
        "case-triage run within 90s. Root cause on this environment: ai-gateway rejects " +
        "agent-runtime's LLM call with KEY_INVALID ('virtual key is invalid or revoked') for " +
        "the provisioned virtual key, so the Temporal workflow's run_graph activity fails and " +
        "no proposal is created. Pre-seeded pending proposals are NOT a usable fallback — they " +
        "are orphaned from a prior tenant epoch (case URN carries a different tenant id and no " +
        "workspace_id) and decideProposal returns PERMISSION_DENIED. The correction + capture " +
        "assertions below run and pass as soon as the stack can mint a valid virtual key.",
    );

    // Everything past here requires a real, decidable, current-tenant proposal.
    const p = proposal!;
    const proposalId = p.id;
    const aiSeverity = String((p.args ?? {}).severity ?? "medium");
    expect(aiSeverity, "the AI proposal must carry a severity to correct").toBeTruthy();

    await test.step("the proposal is surfaced on the case in the UI", async () => {
      await page.goto(`/cases/${caseId}`);
      await page.getByRole("tab", { name: /proposals/i }).click();
      await expect(page.locator(`a[href="/inbox?p=${proposalId}"]`)).toBeVisible({
        timeout: 20_000,
      });
    });

    // ── 3. HUMAN CORRECTION — as the MANAGER (a different human than the ──────
    // on-behalf-of adjuster; self-approval is forbidden). The manager edits the
    // AI's proposed value before deciding: the edited args ARE the correction.
    let correctedSeverity = "";
    const decision = await test.step("a human corrects the AI's value and decides", async () => {
      await logout(page);
      await loginAs(page, PERSONAS().manager);

      await page.goto(`/inbox?p=${proposalId}`);
      const detail = page.locator(`[data-proposal-detail="${proposalId}"]`);
      await expect(detail).toBeVisible({ timeout: 20_000 });
      // Non-suppressible AI provenance on the item the human is about to correct.
      await expect(detail.locator('[data-ai-label="true"]').first()).toBeVisible();

      // Enter edit-args mode (the correction affordance) and read the AI's value.
      await detail.getByRole("button", { name: /edit args/i }).click();
      const editor = detail.getByLabel("Edited args");
      await expect(editor).toBeVisible();
      const proposedArgs = JSON.parse(await editor.inputValue()) as Record<string, unknown>;
      // Correct to a deterministically-different, valid severity (stay within the
      // tool's schema so the approved write still executes cleanly).
      correctedSeverity = aiSeverity === "critical" ? "low" : "critical";
      proposedArgs.severity = correctedSeverity;

      // Submit via the REAL decideProposal(EDIT_ARGS) mutation.
      await editor.fill(JSON.stringify(proposedArgs, null, 2));
      const [decideResp] = await Promise.all([
        page.waitForResponse(
          (r) =>
            r.url().includes("/api/graphql") &&
            (r.request().postData()?.includes("DecideProposal") ?? false),
          { timeout: 30_000 },
        ),
        detail.getByRole("button", { name: /approve with edits/i }).click(),
      ]);
      const decideBody = await decideResp.json();
      const dp = decideBody?.data?.decideProposal;
      expect(dp, `decideProposal response: ${JSON.stringify(decideBody)}`).toBeTruthy();
      // A correction, not a blind approve.
      expect(dp.status).toBe("EDITED_APPROVED");
      // The decided proposal drops out of the pending inbox (no manual refresh).
      await expect(detail).toBeHidden({ timeout: 10_000 });
      return dp.decision as Record<string, any>;
    });

    await test.step("the human correction is captured on the decided proposal", async () => {
      // The proposal's own decision record IS the durable, authoritative
      // human-correction signal in the live (Temporal) stack.
      expect(decision, "the decided proposal must carry a decision record").toBeTruthy();
      expect(decision.action).toBe("edit_args");
      expect(decision.actor, "the decision is attributed to the human corrector").toMatch(/^user:/);
      // The corrected output (the value a distilled model should learn to emit).
      expect(decision.edited_args?.severity).toBe(correctedSeverity);
      // A field-level diff proving the human OVERRODE the AI (input → target).
      const diff: Array<{ field: string; from: unknown; to: unknown }> = decision.diff ?? [];
      const sevChange = diff.find((d) => d.field === "severity");
      expect(
        sevChange,
        `decision.diff must record the severity correction: ${JSON.stringify(diff)}`,
      ).toBeTruthy();
      expect(String(sevChange!.from)).toBe(aiSeverity);
      expect(String(sevChange!.to)).toBe(correctedSeverity);
      expect(String(sevChange!.to)).not.toBe(String(sevChange!.from));
    });

    await test.step("SLM transcript sink (best-effort; documents a real capture gap)", async () => {
      // The differentiator's ultimate sink is the governed SFT corpus:
      // agent_transcripts with decision="edit" + corrected_output. Verify it the
      // real way if the sink captured this run. In the live Temporal-mode stack
      // it does NOT (capture is only wired into the inline run path), so record
      // the gap as an annotation instead of asserting a fabricated pass.
      const managerJwt = await sessionJwt(page);
      let matched: { decision?: string; corrected_output?: { severity?: string } | null } | null =
        null;
      const deadline = Date.now() + 12_000;
      while (Date.now() < deadline) {
        const r = await page.request.get(
          `${AGENT_RUNTIME_URL}/api/v1/transcripts?filter[decided]=true&limit=200`,
          { headers: { authorization: `Bearer ${managerJwt}` } },
        );
        if (r.ok()) {
          const body = await r.json();
          const rows: Array<Record<string, any>> = body?.data ?? [];
          matched = rows.find((t) => t.proposal_id === proposalId) ?? null;
          if (matched) break;
        }
        await new Promise((res) => setTimeout(res, 2000));
      }

      if (matched && (matched as any).decision === "edit") {
        // If the sink IS wired (e.g. inline mode), assert the SFT gold target.
        expect((matched as any).corrected_output?.severity).toBe(correctedSeverity);
      } else {
        const note =
          `GAP: SLM transcript sink did not capture the correction for proposal ${proposalId}. ` +
          `TranscriptSink.capture is wired only into the inline run path (run_engine.execute); ` +
          `the live stack's Temporal workflow path (activities.run_graph → create_proposal) ` +
          `never calls it, so the human EDIT is not joined into ` +
          `agent_transcripts.corrected_output (the SFT corpus). The correction IS durably ` +
          `captured on the proposal decision record (asserted above).`;
        test.info().annotations.push({ type: "product-gap", description: note });
        // eslint-disable-next-line no-console
        console.warn(`[hero-learning-loop] ${note}`);
      }
    });
  });
});
