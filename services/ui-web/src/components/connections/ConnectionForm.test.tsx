import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import type { ConnectorType } from "@/lib/graphql/types";

/** Route graphqlRequest by operation name to a per-test handler; keep the real
 * GraphQLRequestError (ConnectionForm imports it from the same module). */
let handler: (doc: string, vars: any) => any = () => ({});
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return { ...actual, graphqlRequest: (doc: string, vars: any) => Promise.resolve(handler(doc, vars)) };
});

import { ConnectionForm } from "./ConnectionForm";

const POSTGRES: ConnectorType = {
  connectorType: "postgres",
  displayName: "PostgreSQL",
  category: "database",
  secretFields: ["password"],
  configSchema: {},
  fields: [
    { name: "host", type: "string", required: true, secret: false, default: null, enum: null, help: "Hostname" },
    { name: "port", type: "integer", required: false, secret: false, default: 5432, enum: null, help: null },
    { name: "database", type: "string", required: true, secret: false, default: null, enum: null, help: null },
    { name: "username", type: "string", required: true, secret: false, default: null, enum: null, help: null },
    { name: "ssl_mode", type: "enum", required: false, secret: false, default: "require", enum: ["disable", "require"], help: null },
    { name: "password", type: "string", required: false, secret: true, default: null, enum: null, help: "pw" },
  ],
};

const S3: ConnectorType = {
  connectorType: "s3",
  displayName: "Amazon S3",
  category: "object-store",
  secretFields: ["access_key_id", "secret_access_key"],
  configSchema: {},
  fields: [
    { name: "region", type: "string", required: false, secret: false, default: "us-east-1", enum: null, help: null },
    { name: "bucket", type: "string", required: true, secret: false, default: null, enum: null, help: null },
    { name: "root_prefix", type: "string", required: false, secret: false, default: "/", enum: null, help: null },
    { name: "file_format", type: "enum", required: false, secret: false, default: "csv", enum: ["csv", "parquet"], help: null },
    { name: "access_key_id", type: "string", required: false, secret: true, default: null, enum: null, help: null },
    { name: "secret_access_key", type: "string", required: false, secret: true, default: null, enum: null, help: null },
  ],
};

const SALESFORCE: ConnectorType = {
  connectorType: "salesforce",
  displayName: "Salesforce",
  category: "saas",
  secretFields: ["client_id", "client_secret", "password", "security_token"],
  configSchema: {},
  fields: [
    { name: "username", type: "string", required: true, secret: false, default: null, enum: null, help: null },
    { name: "domain", type: "enum", required: false, secret: false, default: "login", enum: ["login", "test"], help: null },
    { name: "api_version", type: "string", required: false, secret: false, default: "59.0", enum: null, help: null },
    { name: "client_id", type: "string", required: false, secret: true, default: null, enum: null, help: null },
    { name: "client_secret", type: "string", required: false, secret: true, default: null, enum: null, help: null },
    { name: "password", type: "string", required: false, secret: true, default: null, enum: null, help: null },
    { name: "security_token", type: "string", required: false, secret: true, default: null, enum: null, help: null },
  ],
};

const pw = (name: string) => document.getElementById(`field-${name}`) as HTMLInputElement;

beforeEach(() => {
  handler = () => ({});
});

describe("ConnectionForm renders the right widgets per connector type", () => {
  it("postgres: host/port/database/username + a password secret input", () => {
    renderWithProviders(<ConnectionForm type={POSTGRES} onSaved={() => {}} />);
    expect(pw("host")).toBeInTheDocument();
    expect(pw("port").type).toBe("number");
    expect(pw("port").value).toBe("5432"); // schema default seeded
    expect(pw("database")).toBeInTheDocument();
    expect(pw("username")).toBeInTheDocument();
    // ssl_mode is a select seeded to its default
    expect((document.getElementById("field-ssl_mode") as HTMLSelectElement).value).toBe("require");
    // password is a masked (write-only) input
    expect(pw("password").type).toBe("password");
  });

  it("s3: bucket/prefix/format + access key & secret key as password inputs", () => {
    renderWithProviders(<ConnectionForm type={S3} onSaved={() => {}} />);
    expect(pw("bucket")).toBeInTheDocument();
    expect(pw("root_prefix").value).toBe("/");
    expect((document.getElementById("field-file_format") as HTMLSelectElement).value).toBe("csv");
    expect(pw("access_key_id").type).toBe("password");
    expect(pw("secret_access_key").type).toBe("password");
  });

  it("salesforce: username/domain/api_version + four secret inputs", () => {
    renderWithProviders(<ConnectionForm type={SALESFORCE} onSaved={() => {}} />);
    expect(pw("username")).toBeInTheDocument();
    expect((document.getElementById("field-domain") as HTMLSelectElement).value).toBe("login");
    expect(pw("api_version").value).toBe("59.0");
    for (const s of SALESFORCE.secretFields) expect(pw(s).type).toBe("password");
  });
});

describe("Test Connection surfaces the real probe result", () => {
  it("shows OK when the probe succeeds", async () => {
    const user = userEvent.setup();
    handler = (doc, vars) => {
      if (doc.includes("TestConnection")) {
        expect(vars.type).toBe("postgres");
        expect(vars.secrets.password).toBe("s3cr3t");
        return { testConnection: { status: "OK", latencyMs: 12, errorCategory: null, errorDetail: null } };
      }
      return {};
    };
    renderWithProviders(<ConnectionForm type={POSTGRES} onSaved={() => {}} />);
    await user.type(pw("host"), "db.internal");
    await user.type(pw("database"), "sales");
    await user.type(pw("username"), "ro");
    await user.type(pw("password"), "s3cr3t");
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => expect(screen.getByTestId("test-result")).toHaveAttribute("data-status", "OK"));
    expect(screen.getByTestId("test-result").textContent).toMatch(/succeeded/i);
  });

  it("shows AUTH_FAILED when the probe fails with bad credentials", async () => {
    const user = userEvent.setup();
    handler = (doc) => {
      if (doc.includes("TestConnection")) {
        return { testConnection: { status: "FAILED", latencyMs: 5, errorCategory: "AUTH_FAILED", errorDetail: "authentication failed (scrubbed)" } };
      }
      return {};
    };
    renderWithProviders(<ConnectionForm type={POSTGRES} onSaved={() => {}} />);
    await user.type(pw("host"), "db.internal");
    await user.type(pw("database"), "sales");
    await user.type(pw("username"), "ro");
    await user.type(pw("password"), "wrong");
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    const banner = await screen.findByTestId("test-result");
    expect(banner).toHaveAttribute("data-status", "FAILED");
    expect(banner.textContent).toMatch(/AUTH_FAILED/);
  });

  it("blocks Test when a required field is empty (validation from the schema)", async () => {
    const user = userEvent.setup();
    let called = false;
    handler = (doc) => {
      if (doc.includes("TestConnection")) called = true;
      return { testConnection: { status: "OK" } };
    };
    renderWithProviders(<ConnectionForm type={POSTGRES} onSaved={() => {}} />);
    // host/database/username left empty
    await user.click(screen.getByRole("button", { name: /test connection/i }));
    // host + database + username are all required and empty → one error each.
    await waitFor(() => expect(screen.getAllByText("Required").length).toBeGreaterThanOrEqual(3));
    expect(called).toBe(false);
  });

  it("edit mode: prefills the SAVED config, keeps secrets blank, and PATCHes only re-entered secrets", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    handler = (doc, vars) => {
      if (doc.includes("UpdateConnection")) {
        expect(vars.id).toBe("conn-1");
        expect(vars.input.name).toBe("Prod DB v2");
        expect(vars.input.config.host).toBe("db2.internal");
        // Blank password stayed blank → secrets omitted entirely, so the
        // service preserves the stored Vault value (write-only merge).
        expect(vars.input.secrets).toBeUndefined();
        return { updateConnection: { id: "conn-1", name: "Prod DB v2", connectorType: "postgres",
          config: { host: "db2.internal" }, secretFields: ["password"], secretSet: true, tags: [] } };
      }
      return {};
    };
    const editing = {
      id: "conn-1", urn: "wr:t:ingestion:connection/conn-1", name: "Prod DB", connectorType: "postgres",
      config: { host: "db.internal", port: 5433, database: "sales", username: "ro", ssl_mode: "require" },
      secretFields: ["password"], secretSet: true, trafficDirection: null, tags: [],
      workspaceId: null, lastTestStatus: "ok", lastTestedAt: null, createdAt: null, updatedAt: null,
    };
    renderWithProviders(<ConnectionForm type={POSTGRES} editing={editing} onSaved={onSaved} />);

    // Saved config seeded; secret stays blank (write-only, masked on read).
    expect(pw("host").value).toBe("db.internal");
    expect(pw("port").value).toBe("5433");
    expect(pw("password").value).toBe("");
    expect(screen.getByTestId("secret-keep-hint")).toBeInTheDocument();
    // No adhoc Test button in edit mode — stored secrets never reach the browser.
    expect(screen.queryByRole("button", { name: /test connection/i })).not.toBeInTheDocument();

    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Prod DB v2");
    const host = pw("host");
    await user.clear(host);
    await user.type(host, "db2.internal");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "conn-1", name: "Prod DB v2" })));
  });

  it("saves via createConnection with the collected config + secrets", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    handler = (doc, vars) => {
      if (doc.includes("CreateConnection")) {
        expect(vars.input.type).toBe("postgres");
        expect(vars.input.name).toBe("Prod DB");
        expect(vars.input.config.host).toBe("db.internal");
        expect(vars.input.secrets.password).toBe("s3cr3t");
        return { createConnection: { id: "conn-1", name: "Prod DB", connectorType: "postgres", config: {}, secretFields: ["password"], secretSet: true, tags: [] } };
      }
      return {};
    };
    renderWithProviders(<ConnectionForm type={POSTGRES} onSaved={onSaved} />);
    await user.type(screen.getByLabelText("Name"), "Prod DB");
    await user.type(pw("host"), "db.internal");
    await user.type(pw("database"), "sales");
    await user.type(pw("username"), "ro");
    await user.type(pw("password"), "s3cr3t");
    await user.click(screen.getByRole("button", { name: /save connection/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "conn-1" })));
  });
});
