import { describe, it, expect } from "vitest";
import { collect, defaultValues } from "./form";
import type { ConnectorField } from "@/lib/graphql/types";

const PG_FIELDS: ConnectorField[] = [
  { name: "host", type: "string", required: true, secret: false, default: null, enum: null, help: "Hostname" },
  { name: "port", type: "integer", required: false, secret: false, default: 5432, enum: null, help: null },
  { name: "database", type: "string", required: true, secret: false, default: null, enum: null, help: null },
  { name: "username", type: "string", required: true, secret: false, default: null, enum: null, help: null },
  { name: "ssl_mode", type: "enum", required: false, secret: false, default: "require", enum: ["disable", "require", "verify-full"], help: null },
  { name: "password", type: "string", required: false, secret: true, default: null, enum: null, help: "pw" },
];

describe("connection form helpers", () => {
  it("seeds defaults from the schema (secrets blank, defaults applied)", () => {
    const v = defaultValues(PG_FIELDS);
    expect(v.host).toBe("");
    expect(v.port).toBe("5432");
    expect(v.ssl_mode).toBe("require");
    expect(v.password).toBe("");
  });

  it("coerces + collects a valid config and secrets (types honoured, secret separated)", () => {
    const r = collect(PG_FIELDS, {
      host: "db.internal",
      port: "5432",
      database: "sales",
      username: "ro",
      ssl_mode: "require",
      password: "s3cr3t",
    });
    expect(r.ok).toBe(true);
    expect(r.config).toEqual({ host: "db.internal", port: 5432, database: "sales", username: "ro", ssl_mode: "require" });
    expect(r.secrets).toEqual({ password: "s3cr3t" }); // never in config
    expect(r.config).not.toHaveProperty("password");
  });

  it("errors on missing required fields, omits empty optionals + blank secrets", () => {
    const r = collect(PG_FIELDS, { host: "", port: "", database: "sales", username: "ro", ssl_mode: "require", password: "" });
    expect(r.ok).toBe(false);
    expect(r.errors.host).toBeTruthy();
    expect(r.config).not.toHaveProperty("port"); // empty optional omitted
    expect(r.secrets).toEqual({}); // blank secret omitted
  });

  it("rejects a non-integer port + an out-of-enum value", () => {
    const bad = collect(PG_FIELDS, { host: "h", port: "12.5", database: "d", username: "u", ssl_mode: "bogus", password: "" });
    expect(bad.errors.port).toBeTruthy();
    expect(bad.errors.ssl_mode).toBeTruthy();
  });

  it("parses object fields from JSON text", () => {
    const fields: ConnectorField[] = [
      { name: "headers", type: "object", required: false, secret: false, default: null, enum: null, help: null },
    ];
    expect(collect(fields, { headers: '{"X-Api":"1"}' }).config).toEqual({ headers: { "X-Api": "1" } });
    expect(collect(fields, { headers: "{not json}" }).errors.headers).toBeTruthy();
  });
});
