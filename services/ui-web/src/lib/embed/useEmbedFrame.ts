"use client";
import { useEffect } from "react";

/**
 * Shared embed-surface behavior for every `/embed/*` page:
 *  - applies the host-requested theme (`?theme=light|dark`) to <html>;
 *  - strips the one-time `?t=` token from the visible URL once the middleware
 *    has moved it into the httpOnly `wr_embed` cookie;
 *  - posts `windrose:ready` and continuous `windrose:resize` (content height)
 *    messages to the host window so the embed SDK can auto-size the iframe.
 *
 * postMessage targets `"*"` for the outbound height/ready signals (they carry
 * no sensitive data — just a type + a number); the SDK validates that inbound
 * messages come from the Windrose iframe origin. Inbound host→embed messages
 * (theme changes) are accepted only from the document referrer's origin.
 */
export function useEmbedFrame(): void {
  useEffect(() => {
    if (typeof window === "undefined") return;

    // 1) theme
    const params = new URLSearchParams(window.location.search);
    const theme = params.get("theme");
    if (theme === "dark") document.documentElement.classList.add("dark");
    else if (theme === "light") document.documentElement.classList.remove("dark");

    // 2) strip the one-time token from the URL
    if (params.has("t")) {
      const url = new URL(window.location.href);
      url.searchParams.delete("t");
      window.history.replaceState({}, "", url.toString());
    }

    // 3) ready + resize signalling to the host
    const post = (type: string, extra: Record<string, unknown> = {}) => {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ source: "windrose-embed", type, ...extra }, "*");
      }
    };
    post("windrose:ready");
    const emitHeight = () =>
      post("windrose:resize", { height: document.documentElement.scrollHeight });
    emitHeight();
    const ro = new ResizeObserver(emitHeight);
    ro.observe(document.documentElement);

    // 4) accept theme changes from the host (referrer origin only)
    let hostOrigin = "";
    try {
      hostOrigin = document.referrer ? new URL(document.referrer).origin : "";
    } catch {
      hostOrigin = "";
    }
    const onMessage = (e: MessageEvent) => {
      if (hostOrigin && e.origin !== hostOrigin) return;
      const data = e.data as { source?: string; type?: string; theme?: string } | null;
      if (data?.source !== "windrose-host") return;
      if (data.type === "windrose:set-theme") {
        document.documentElement.classList.toggle("dark", data.theme === "dark");
        emitHeight();
      }
    };
    window.addEventListener("message", onMessage);
    return () => {
      ro.disconnect();
      window.removeEventListener("message", onMessage);
    };
  }, []);
}
