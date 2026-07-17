import { describe, it, expect } from "vitest";
import { agentKeyForPath } from "./agentKeys";

/** Route → module-specialist mapping (Tier 2b). Keys must stay within the
 * allowlist in src/app/api/copilot/message/route.ts. */
describe("agentKeyForPath", () => {
  it("maps /data pages to the onboarding specialist", () => {
    expect(agentKeyForPath("/data")).toBe("onboarding");
    expect(agentKeyForPath("/data/connections")).toBe("onboarding");
    expect(agentKeyForPath("/data/pipelines/runs")).toBe("onboarding");
  });

  it("maps /ml pages to model-training, except the inference pages", () => {
    expect(agentKeyForPath("/ml")).toBe("model-training");
    expect(agentKeyForPath("/ml/experiments")).toBe("model-training");
    expect(agentKeyForPath("/ml/models/m-1")).toBe("model-training");
    expect(agentKeyForPath("/ml/inference")).toBe("inference");
    expect(agentKeyForPath("/ml/inference/job-1")).toBe("inference");
  });

  it("maps /dashboards pages to dashboard-designer", () => {
    expect(agentKeyForPath("/dashboards")).toBe("dashboard-designer");
    expect(agentKeyForPath("/dashboards/d-1")).toBe("dashboard-designer");
  });

  it("returns null (default copilot agent) everywhere else", () => {
    expect(agentKeyForPath("/")).toBeNull();
    expect(agentKeyForPath("/cases/c-1")).toBeNull();
    expect(agentKeyForPath("/copilot")).toBeNull();
    expect(agentKeyForPath("/admin/agents")).toBeNull();
    // Prefix must match a path segment, not a substring.
    expect(agentKeyForPath("/mlx")).toBeNull();
    expect(agentKeyForPath("/database")).toBeNull();
  });
});
