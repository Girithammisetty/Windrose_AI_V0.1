"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { X, Send, Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/primitives";
import { AiDisclosure, AiLabel } from "@/components/primitives/AiLabel";
import { UrnLink } from "@/components/primitives/UrnLink";
import { useCopilot } from "@/stores/ui";
import { useCopilotThread } from "./useCopilotThread";
import { agentKeyForPath } from "@/lib/agentic/agentKeys";
import { routeUrnFor } from "@/lib/urn";
import { t } from "@/lib/i18n/messages";
import { useSession } from "@/lib/session/SessionContext";
import { cn } from "@/lib/utils";

/**
 * Copilot drawer (UI-FR-030) available on every page. Right-side, resizable
 * 360–640px. Opens with context = current resource URN. Streams assistant tokens
 * from realtime-hub. Carries a NON-SUPPRESSIBLE AI disclosure (UI-FR-031/BR-2)
 * before, during, and after streaming. Suggested write actions route to the
 * proposal flow only (BR-13). Shows the budget-exhausted banner when applicable.
 */
export function CopilotDrawer({
  budgetExhausted = false,
  agentKey: agentKeyProp,
}: {
  budgetExhausted?: boolean;
  /** Explicit specialist override (Tier 2b). Defaults to the current route's
   * module specialist (agentKeyForPath): /data → onboarding, /ml →
   * model-training (/ml/inference → inference), /dashboards →
   * dashboard-designer; elsewhere the default copilot agent. */
  agentKey?: string | null;
}) {
  const { open, width, setOpen, setWidth } = useCopilot();
  const pathname = usePathname();
  const session = useSession();
  const [input, setInput] = useState("");
  // Context URN derived from the current route, defaulting to the workspace.
  const contextUrn = routeUrnFor(pathname, session.tenantId) ?? `wr:${session.tenantId}:workspace:${session.workspaceId}`;
  // Module specialist for the current route (real agent-runtime agent key).
  const agentKey = agentKeyProp !== undefined ? agentKeyProp : agentKeyForPath(pathname);
  const { messages, streaming, send } = useCopilotThread(contextUrn, agentKey);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  if (!open) return null;

  function submit() {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    void send(text);
  }

  return (
    <aside
      role="complementary"
      aria-label={t("copilot.title")}
      data-copilot-drawer="open"
      className="fixed inset-y-0 right-0 z-40 flex flex-col border-l bg-card shadow-2xl"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize copilot"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setWidth(width + 20);
          if (e.key === "ArrowRight") setWidth(width - 20);
        }}
        onMouseDown={(e) => {
          const startX = e.clientX;
          const startW = width;
          const move = (ev: MouseEvent) => setWidth(startW + (startX - ev.clientX));
          const up = () => {
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
          };
          window.addEventListener("mousemove", move);
          window.addEventListener("mouseup", up);
        }}
        className="absolute left-0 top-0 h-full w-1 cursor-col-resize hover:bg-ai/40"
      />

      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Bot className="size-4 text-ai" aria-hidden />
        <span className="font-semibold">{t("copilot.title")}</span>
        <AiLabel className="ml-1" />
        <Button variant="ghost" size="icon" className="ml-auto" onClick={() => setOpen(false)} aria-label={t("action.cancel")}>
          <X className="size-4" />
        </Button>
      </div>

      {/* Non-suppressible disclosure (renders before any content) */}
      <AiDisclosure />

      <div
        className="border-b px-3 py-1.5 text-xs text-muted-foreground"
        data-copilot-context={contextUrn}
        data-copilot-agent={agentKey ?? "default"}
      >
        {t("copilot.context", { urn: "" })}
        <span className="ml-1">
          <UrnLink urn={contextUrn} />
        </span>
        {agentKey && (
          <span className="ml-2 rounded bg-ai/10 px-1.5 py-0.5 font-mono text-[10px] text-ai" title="Module specialist agent">
            {agentKey}
          </span>
        )}
      </div>

      {budgetExhausted && (
        <div role="alert" className="border-b border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t("copilot.budgetExhausted")}
        </div>
      )}

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto p-3">
        {messages.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("copilot.placeholder")}</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              data-role={m.role}
              className={cn(
                "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                m.role === "user" ? "bg-primary text-primary-foreground" : "border border-ai/30 bg-ai/5",
              )}
            >
              {m.role === "assistant" && (
                <div className="mb-1 flex items-center gap-1">
                  <AiLabel />
                </div>
              )}
              <p className={cn("whitespace-pre-wrap", m.streaming && "streaming-caret")}>{m.text}</p>
              {m.citations && m.citations.length > 0 && (
                <div className="mt-2 space-y-1 border-t pt-2">
                  <p className="text-xs font-medium text-muted-foreground">Citations</p>
                  {m.citations.map((c, i) => (
                    <UrnLink key={i} urn={c.urn} label={c.label} className="text-xs" />
                  ))}
                </div>
              )}
              {m.actions && m.actions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1 border-t pt-2">
                  {m.actions.map((a, i) => (
                    <Button key={i} asChild size="sm" variant="ai" className="h-7">
                      <Link href={a.href ?? (a.proposalId ? `/inbox?p=${a.proposalId}` : "/inbox")}>{a.label}</Link>
                    </Button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <form
        className="border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={t("copilot.placeholder")}
          aria-label={t("copilot.placeholder")}
          disabled={budgetExhausted}
          className="min-h-[60px] resize-none"
        />
        <div className="mt-2 flex justify-end">
          <Button type="submit" size="sm" variant="ai" disabled={streaming || budgetExhausted || !input.trim()}>
            <Send className="size-3.5" /> {t("copilot.send")}
          </Button>
        </div>
      </form>
    </aside>
  );
}
