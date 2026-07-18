"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Send } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AiDisclosure, AiLabel } from "@/components/primitives/AiLabel";
import { UrnLink } from "@/components/primitives/UrnLink";
import { Card } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/primitives";
import { useCopilotThread } from "@/components/copilot/useCopilotThread";
import { useSession } from "@/lib/session/SessionContext";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/** Full-page copilot (UI-FR / §5). Same thread engine as the drawer; persistent AI label. */
const COPILOT_AGENTS = [
  { key: "analytics", label: "Analytics (default)" },
  { key: "ml-engineer", label: "ML Engineer" },
  { key: "model-training", label: "Model Training" },
  { key: "inference", label: "Inference" },
  { key: "dashboard-designer", label: "Dashboard Designer" },
  { key: "governance", label: "Governance" },
];

export default function CopilotPage() {
  const session = useSession();
  const contextUrn = `wr:${session.tenantId}:workspace:${session.workspaceId}`;
  const [agentKey, setAgentKey] = useState<string>("analytics");
  const { messages, streaming, send } = useCopilotThread(contextUrn, agentKey);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col">
      <PageHeader
        title={t("copilot.title")}
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="copilot-agent" className="text-xs text-muted-foreground">
              Agent
            </label>
            <select
              id="copilot-agent"
              aria-label="Copilot agent"
              value={agentKey}
              onChange={(e) => setAgentKey(e.target.value)}
              className="h-8 rounded-md border bg-background px-2 text-sm"
            >
              {COPILOT_AGENTS.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.label}
                </option>
              ))}
            </select>
            <AiLabel />
          </div>
        }
      />
      <Card className="flex flex-1 flex-col overflow-hidden">
        <AiDisclosure />
        <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto p-4">
          {messages.length === 0 && <p className="text-sm text-muted-foreground">{t("copilot.placeholder")}</p>}
          {messages.map((m) => (
            <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div
                data-role={m.role}
                className={cn(
                  "max-w-[80%] rounded-lg px-3 py-2 text-sm",
                  m.role === "user" ? "bg-primary text-primary-foreground" : "border border-ai/30 bg-ai/5",
                )}
              >
                {m.role === "assistant" && <AiLabel className="mb-1" />}
                <p className={cn("whitespace-pre-wrap", m.streaming && "streaming-caret")}>{m.text}</p>
                {m.citations?.map((c, i) => <UrnLink key={i} urn={c.urn} label={c.label} className="mt-1 text-xs" />)}
                {m.actions?.map((a, i) => (
                  <Button key={i} asChild size="sm" variant="ai" className="mr-1 mt-2 h-7">
                    <Link href={a.href ?? "/inbox"}>{a.label}</Link>
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <form
          className="border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            const text = input.trim();
            if (!text || streaming) return;
            setInput("");
            void send(text);
          }}
        >
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t("copilot.placeholder")}
              aria-label={t("copilot.placeholder")}
              className="min-h-[44px] resize-none"
            />
            <Button type="submit" variant="ai" disabled={streaming || !input.trim()}>
              <Send className="size-4" />
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
