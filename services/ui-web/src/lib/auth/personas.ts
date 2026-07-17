/**
 * Dev-login persona resolution (pure, unit-testable).
 *
 * `make up` injects WINDROSE_PERSONAS: a JSON map of persona email ->
 * {sub, tenantId, workspaceId, scopes} bound to the REAL provisioned tenant +
 * workspace and the projection grants seeded for that persona.
 *
 * Fail-closed contract: when a personas map is present (non-empty), it is
 * AUTHORITATIVE — an email that does not resolve is rejected (unknown user)
 * rather than silently minted into the self-contained dev defaults (which
 * would land the user in a ghost tenant with an empty nav and no error).
 * The dev defaults apply ONLY when no personas map is configured at all
 * (self-contained `pnpm dev` without a booted stack).
 */

export interface Persona {
  sub?: string;
  tenantId?: string;
  workspaceId?: string;
  scopes?: string[];
}

export type LoginResolution =
  /** Email found in the configured personas map. */
  | { kind: "persona"; persona: Persona }
  /** No personas map configured — self-contained dev defaults apply. */
  | { kind: "dev-default" }
  /** Personas map configured but the email is not in it — reject (403). */
  | { kind: "unknown-user" };

function parsePersonas(json: string | undefined): Record<string, Persona> {
  if (!json) return {};
  try {
    const parsed = JSON.parse(json) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, Persona>;
    }
  } catch {
    // Malformed env — treat as absent (self-contained dev), never crash login.
  }
  return {};
}

/** Resolve a login email against WINDROSE_PERSONAS (see module docs). */
export function resolveLogin(email: string, personasJson: string | undefined): LoginResolution {
  const personas = parsePersonas(personasJson);
  const persona = personas[email.toLowerCase()];
  if (persona) return { kind: "persona", persona };
  // A non-empty map is authoritative: unknown emails are rejected, not defaulted.
  if (Object.keys(personas).length > 0) return { kind: "unknown-user" };
  return { kind: "dev-default" };
}
