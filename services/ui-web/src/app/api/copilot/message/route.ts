/**
 * Copilot send → agent-runtime chat. Forwards the user's message + context URN
 * to agent-runtime (real service) with the Bearer JWT and returns the stream
 * descriptor (hubUrl + topics) the browser subscribes to for token streaming.
 *
 * The DEFAULT copilot runs the read-only `analytics` agent (conversational
 * analytics over the governed semantic layer). Module copilots select a
 * different published agent via `agentKey` (allowlisted below — the module
 * pages send the agent matching their context: /data → onboarding,
 * /ml → model-training or inference, /dashboards → dashboard-designer).
 * agent-runtime's chat surface is
 * `POST /api/v1/agents/{agent_key}/chat/completions` (OpenAI-shaped: a `messages`
 * array + `metadata`); it returns `{data:{run_id, session_id}}` and an
 * `x-windrose-stream-topic: agent_run:{run_id}` header — the realtime-hub topic
 * the browser subscribes to for token streaming.
 *
 * The copilot has NO mutation capability except decideProposal/submitFeedback
 * (BR-13): a suggested write always materializes as a proposal, never a direct
 * mutation from chat — write-mode agents emit proposals by construction, and
 * agent-runtime enforces authz per run (OBO token), so exposing the agent key
 * grants nothing the caller's grants don't already allow.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The general copilot agent (read-only conversational analytics). case-triage is
// NOT selectable here — it requires a case_id and has its own triage surface.
const COPILOT_AGENT_KEY = process.env.COPILOT_AGENT_KEY ?? "analytics";
// Published agents a module copilot may target. Kept in lockstep with
// agent-runtime's catalog (app/agents/catalog.py).
const ALLOWED_AGENT_KEYS = new Set([
  "analytics",
  "onboarding",
  "dashboard-designer",
  "model-training",
  "ml-engineer",
  "inference",
  "governance",
  "meta-router",
]);
const AGENT_RUNTIME_URL = process.env.AGENT_RUNTIME_URL ?? "http://localhost:8306";
const HUB_URL =
  process.env.NEXT_PUBLIC_REALTIME_HUB_URL ?? process.env.REALTIME_HUB_URL ?? "http://localhost:8305";

export async function POST(req: NextRequest) {
  const token = await getSessionToken();
  if (!token) return NextResponse.json({ error: "unauthenticated" }, { status: 401 });

  const body = (await req.json().catch(() => ({}))) as {
    threadId?: string;
    text?: string;
    contextUrn?: string | null;
    sessionId?: string | null;
    agentKey?: string | null;
  };
  if (!body.text) return NextResponse.json({ error: "text required" }, { status: 400 });
  const agentKey =
    body.agentKey && ALLOWED_AGENT_KEYS.has(body.agentKey) ? body.agentKey : COPILOT_AGENT_KEY;

  const threadId = body.threadId || crypto.randomUUID();
  try {
    const res = await fetch(
      `${AGENT_RUNTIME_URL}/api/v1/agents/${agentKey}/chat/completions`,
      {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
        body: JSON.stringify({
          messages: [{ role: "user", content: body.text }],
          metadata: {
            context_urn: body.contextUrn ?? null,
            // Thread continuity: reuse the agent-runtime session across turns when
            // the client has one (returned below as `sessionId`).
            ...(body.sessionId ? { session_id: body.sessionId } : {}),
          },
        }),
      },
    );
    if (!res.ok) {
      return NextResponse.json({ error: "agent unavailable", status: res.status }, { status: 502 });
    }
    // agent-runtime publishes streamed tokens to this realtime-hub topic.
    const streamTopic = res.headers.get("x-windrose-stream-topic");
    const json = (await res.json().catch(() => ({}))) as {
      data?: { run_id?: string; session_id?: string };
      run_id?: string;
      session_id?: string;
    };
    const data = json.data ?? json;
    const runId = data.run_id ?? null;
    const topics = streamTopic ? [streamTopic] : runId ? [`agent_run:${runId}`] : [];
    return NextResponse.json({
      threadId,
      runId,
      sessionId: data.session_id ?? null,
      hubUrl: HUB_URL,
      topics,
    });
  } catch {
    return NextResponse.json({ error: "agent unreachable" }, { status: 502 });
  }
}
