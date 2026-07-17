"use client";
import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { AiDisclosure, AiLabel } from "@/components/primitives/AiLabel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/primitives";
import { useCopilotThread } from "@/components/copilot/useCopilotThread";
import { useMe } from "@/lib/graphql/hooks";
import { useEmbedFrame } from "@/lib/embed/useEmbedFrame";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Headless embedded copilot (embed surface "copilot"). Same real SSE thread
 * engine as the first-party copilot; the workspace context is derived from the
 * embed token via `useMe()` (no SessionProvider in embed). Persistent AI label
 * + disclosure are kept (governance is not optional inside an iframe).
 */
export default function EmbeddedCopilotPage() {
  useEmbedFrame();
  const { data: me } = useMe();
  const contextUrn = me
    ? `wr:${me.me.tenantId}:workspace:${me.me.workspaceId}`
    : null;
  const { messages, streaming, send } = useCopilotThread(contextUrn);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const onSend = () => {
    const text = input.trim();
    if (!text || streaming || !contextUrn) return;
    setInput("");
    void send(text);
  };

  return (
    <main id="main" className="flex h-screen flex-col bg-background">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="text-sm font-semibold">{t("copilot.title")}</span>
        <AiLabel />
      </div>
      <AiDisclosure />
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto p-4">
        {messages.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("copilot.placeholder")}</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              data-role={m.role}
              className={cn(
                "max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
                m.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-foreground",
              )}
            >
              {m.text}
            </div>
          </div>
        ))}
      </div>
      <div className="flex items-end gap-2 border-t p-3">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          rows={1}
          placeholder={t("copilot.placeholder")}
          aria-label={t("copilot.placeholder")}
          className="flex-1 resize-none"
        />
        <Button onClick={onSend} disabled={streaming || !input.trim()} size="icon" aria-label={t("copilot.send")}>
          <Send className="size-4" />
        </Button>
      </div>
    </main>
  );
}
