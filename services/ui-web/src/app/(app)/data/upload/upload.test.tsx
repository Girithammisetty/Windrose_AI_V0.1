import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Session-lifecycle mutations (createIngestion/createUpload/completeUpload) go
 * through graphqlRequest, mocked the same way as every other page test in this
 * repo. The per-chunk PUT bypasses GraphQL entirely (binary body — see
 * src/lib/uploads/chunk.ts and the proxy route's doc comment), so it is
 * exercised here via a global fetch mock instead.
 */
let handler: (doc: string, vars: any) => any = () => ({});
const requests: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      requests.push({ doc, vars });
      return Promise.resolve(handler(doc, vars));
    },
  };
});

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import DataUploadPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const ingestionFields = {
  urn: "wr:t-42:ingestion:ingestion/ing-1", trigger: null, connectionId: null,
  datasetUrn: "wr:t-42:dataset:dataset/ds-9",
  statement: null, attempts: null, createdAt: null, updatedAt: null,
};

let originalFetch: typeof fetch;

beforeEach(() => {
  requests.length = 0;
  push.mockClear();
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("mutation CreateIngestion")) {
      return { createIngestion: { id: "ing-1", mode: "file_upload", status: "created", fileFormat: "csv", bytesReceived: null, bytesTotal: null, rowsAppended: null, ...ingestionFields } };
    }
    if (doc.includes("mutation CreateUpload")) {
      return {
        createUpload: {
          uploadId: "up-1", ingestionId: "ing-1", status: "created",
          partSize: 5, bytesTotal: 10, sha256: null, expiresAt: null, parts: [],
        },
      };
    }
    if (doc.includes("mutation CompleteUpload")) {
      return { completeUpload: { id: "ing-1", mode: "file_upload", status: "queued", fileFormat: "csv", bytesReceived: 10, bytesTotal: 10, rowsAppended: null, ...ingestionFields } };
    }
    if (doc.includes("query Ingestion")) {
      return { ingestion: { id: "ing-1", mode: "file_upload", status: "completed", fileFormat: "csv", bytesReceived: 10, bytesTotal: 10, rowsAppended: 3, ...ingestionFields } };
    }
    if (doc.includes("query DatasetSchema") || doc.includes("datasetSchema")) {
      return { datasetSchema: [
        { name: "id", type: "string", nullable: true, tags: [], inferred: true },
        { name: "amount", type: "string", nullable: true, tags: [], inferred: true },
      ] };
    }
    if (doc.includes("query Dataset")) {
      return { dataset: { id: "ds-9", urn: "wr:t-42:dataset:dataset/ds-9", name: "claims", description: null, status: "active", tags: [], rowCount: 3, createdAt: null, profile: null } };
    }
    return {};
  };

  originalFetch = global.fetch;
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/uploads/")) {
      const match = url.match(/parts\/(\d+)/);
      const n = match ? Number(match[1]) : 1;
      return new Response(JSON.stringify({ data: { n, etag: `etag-${n}`, size: 5 } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`unexpected fetch in upload test: ${url}`);
  }) as unknown as typeof fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("Upload wizard", () => {
  it("auto-detects the format, chunks per the server's partSize, PUTs each part, and completes then previews the dataset schema", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DataUploadPage />);

    const fileInput = await screen.findByLabelText("Upload file");
    const file = new File(["0123456789"], "claims.csv", { type: "text/csv" }); // 10 bytes
    await user.upload(fileInput, file);

    // .csv auto-selects the CSV format tile.
    await waitFor(() => expect(screen.getByRole("radio", { name: "CSV" })).toHaveAttribute("aria-checked", "true"));

    await user.click(await screen.findByRole("button", { name: /Start upload/ }));

    // Review step: the polled ingestion is completed and the schema previews.
    await waitFor(() => expect(screen.getByTestId("upload-schema-preview")).toBeInTheDocument());

    const createIngestionCall = requests.find((r) => r.doc.includes("mutation CreateIngestion"));
    expect(createIngestionCall?.vars.input).toMatchObject({
      mode: "file_upload", fileFormat: "csv", newDatasetName: "claims",
    });

    const createUploadCall = requests.find((r) => r.doc.includes("mutation CreateUpload"));
    expect(createUploadCall?.vars.input).toMatchObject({ ingestionId: "ing-1", bytesTotal: 10 });

    // Server-decided partSize=5 over a 10-byte file -> exactly 2 chunk PUTs.
    const putCalls = (global.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter(([url]) =>
      String(url).includes("/api/uploads/"),
    );
    expect(putCalls).toHaveLength(2);
    expect(String(putCalls[0][0])).toContain("/api/uploads/up-1/parts/1");
    expect(String(putCalls[1][0])).toContain("/api/uploads/up-1/parts/2");

    const completeCall = requests.find((r) => r.doc.includes("mutation CompleteUpload"));
    expect(completeCall?.vars).toMatchObject({ uploadId: "up-1" });
    expect(completeCall?.vars.input.parts).toEqual([
      { n: 1, etag: "etag-1", size: 5 },
      { n: 2, etag: "etag-2", size: 5 },
    ]);

    // Schema preview shows the real inferred columns + a link to the dataset.
    expect(screen.getByText("id")).toBeInTheDocument();
    expect(screen.getByText("amount")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open dataset/ })).toHaveAttribute("href", "/data/datasets/ds-9");
  });

  it("surfaces a chunk-PUT failure and offers a resumable retry", async () => {
    const user = userEvent.setup();
    let call = 0;
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/uploads/")) {
        call += 1;
        if (call === 2) {
          return new Response(JSON.stringify({ error: { message: "part too large" } }), { status: 413 });
        }
        const match = url.match(/parts\/(\d+)/);
        const n = match ? Number(match[1]) : 1;
        return new Response(JSON.stringify({ data: { n, etag: `etag-${n}`, size: 5 } }), { status: 200 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    renderWithProviders(<DataUploadPage />);
    const fileInput = await screen.findByLabelText("Upload file");
    await user.upload(fileInput, new File(["0123456789"], "claims.csv", { type: "text/csv" }));
    await user.click(await screen.findByRole("button", { name: /Start upload/ }));

    expect(await screen.findByText("part too large")).toBeInTheDocument();
    expect(await screen.findByText(/1\/2 parts already confirmed/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Resume upload" }));
    await waitFor(() => expect(screen.getByTestId("upload-schema-preview")).toBeInTheDocument());
  });
});
