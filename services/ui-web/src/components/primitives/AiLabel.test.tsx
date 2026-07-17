import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiLabel, AiDisclosure } from "./AiLabel";
import { ProvenanceBadge } from "./ProvenanceBadge";

const SUPPRESSORS = ["hidden", "invisible", "opacity-0", "sr-only", "collapse", "!hidden", "md:hidden", "w-0"];

/** Every className token that would hide the element must have been stripped. */
function assertNotSuppressed(el: HTMLElement) {
  const classes = el.className.split(/\s+/);
  for (const bad of ["hidden", "invisible", "opacity-0", "sr-only", "collapse"]) {
    expect(classes).not.toContain(bad);
  }
}

describe("AiLabel — non-suppressible BY CONSTRUCTION (UI-FR-031, BR-2)", () => {
  it("always renders the AI disclosure label", () => {
    render(<AiLabel />);
    const label = screen.getByRole("note");
    expect(label).toHaveAttribute("data-ai-label", "true");
    expect(label).toHaveTextContent("AI");
  });

  it.each(SUPPRESSORS)("stays visible even when a caller passes className=%s", (cls) => {
    render(<AiLabel className={cls} />);
    const label = screen.getByRole("note");
    expect(label).toBeInTheDocument();
    expect(label).toHaveAttribute("data-ai-label", "true");
    // The hiding utility was stripped from the merged className by construction.
    assertNotSuppressed(label);
  });

  it("keeps legitimate caller classes while dropping only the suppressor", () => {
    render(<AiLabel className="ml-2 hidden opacity-0" />);
    const label = screen.getByRole("note");
    expect(label.className).toContain("ml-2");
    assertNotSuppressed(label);
  });

  it("disclosure banner states the user is interacting with an AI system", () => {
    render(<AiDisclosure />);
    expect(screen.getByText(/interacting with an AI system/i)).toBeInTheDocument();
  });

  it("disclosure banner cannot be hidden by a caller className either", () => {
    const { container } = render(<AiDisclosure className="hidden invisible" />);
    const banner = container.querySelector('[data-ai-disclosure="true"]') as HTMLElement;
    expect(banner).toBeInTheDocument();
    assertNotSuppressed(banner);
  });
});

describe("ProvenanceBadge (UI-FR-032, AC-4)", () => {
  it("renders an AI-generated badge whenever provenance is non-null", () => {
    render(<ProvenanceBadge provenance={{ agent: "triage", version: "v3", sourceRunId: "run-1" }} />);
    expect(screen.getByRole("button", { name: /AI-generated/i })).toBeInTheDocument();
  });

  it("renders nothing when provenance is null (not an AI artifact)", () => {
    const { container } = render(<ProvenanceBadge provenance={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("badge cannot be suppressed by a caller className", () => {
    render(
      <ProvenanceBadge
        provenance={{ agent: "triage", version: "v3", sourceRunId: "run-1" }}
        className="hidden opacity-0 sr-only"
      />,
    );
    const badge = screen.getByRole("button", { name: /AI-generated/i });
    assertNotSuppressed(badge);
  });
});
