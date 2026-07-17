import { Sparkles } from "lucide-react";
import { nonSuppressibleClassName } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * EU AI Act Article 50 disclosure (UI-FR-031, BR-2). This label is
 * NON-SUPPRESSIBLE by construction: there is no prop, tenant setting, or theme
 * that hides it. It renders before, during, and after streaming. Copy is
 * centrally managed here for legal review — do not inline AI-disclosure text
 * anywhere else.
 */
export function AiLabel({ className }: { className?: string }) {
  return (
    <span
      role="note"
      aria-label={t("ai.disclosure")}
      data-ai-label="true"
      className={nonSuppressibleClassName(
        "inline-flex select-none items-center gap-1 rounded-full bg-ai px-2 py-0.5 text-xs font-semibold text-ai-foreground",
        className,
      )}
    >
      <Sparkles className="size-3" aria-hidden />
      {t("ai.label")}
    </span>
  );
}

/** The always-visible disclosure banner shown on every chat/generation surface. */
export function AiDisclosure({ className }: { className?: string }) {
  return (
    <div
      role="note"
      data-ai-disclosure="true"
      className={nonSuppressibleClassName(
        "flex items-center gap-2 border-b border-ai/30 bg-ai/10 px-3 py-2 text-xs text-foreground",
        className,
      )}
    >
      <AiLabel />
      <span>{t("ai.disclosure")}</span>
    </div>
  );
}
