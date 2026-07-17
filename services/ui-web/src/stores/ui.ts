"use client";
/**
 * UI-only Zustand stores (UI-FR-041). NO server data lives here — that is
 * TanStack Query's exclusive domain. Each store is small and single-purpose.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

/* ---- Copilot drawer (open state, width, active context URN) ---- */
interface CopilotState {
  open: boolean;
  width: number; // 360–640 (UI-FR-030)
  contextUrn: string | null;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  setWidth: (w: number) => void;
  setContext: (urn: string | null) => void;
}
export const useCopilot = create<CopilotState>()(
  persist(
    (set) => ({
      open: false,
      width: 420,
      contextUrn: null,
      setOpen: (open) => set({ open }),
      toggle: () => set((s) => ({ open: !s.open })),
      setWidth: (w) => set({ width: Math.min(640, Math.max(360, w)) }),
      setContext: (contextUrn) => set({ contextUrn }),
    }),
    { name: "wr-copilot", partialize: (s) => ({ width: s.width }) },
  ),
);

/* ---- Selection sets for bulk ops, keyed by filter signature (UI-FR-045) ---- */
interface SelectionState {
  signature: string;
  ids: Set<string>;
  setSignature: (sig: string) => void; // changing the filter clears selection
  toggle: (id: string) => void;
  clear: () => void;
  selectMany: (ids: string[]) => void;
}
export const useSelection = create<SelectionState>((set) => ({
  signature: "",
  ids: new Set(),
  setSignature: (signature) =>
    set((s) => (s.signature === signature ? s : { signature, ids: new Set() })),
  toggle: (id) =>
    set((s) => {
      const ids = new Set(s.ids);
      if (ids.has(id)) ids.delete(id);
      else if (ids.size < 1000) ids.add(id); // BR-8 cap
      return { ids };
    }),
  clear: () => set({ ids: new Set() }),
  selectMany: (list) => set(() => ({ ids: new Set(list.slice(0, 1000)) })),
}));

/* ---- Realtime connection health (drives "live updates paused", BR-5) ---- */
interface RealtimeState {
  degraded: boolean;
  setDegraded: (d: boolean) => void;
}
export const useRealtimeHealth = create<RealtimeState>((set) => ({
  degraded: false,
  setDegraded: (degraded) => set({ degraded }),
}));

/* ---- Toasts (optimistic-rollback notices, BR-6) ---- */
export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant?: "default" | "error" | "success";
  traceId?: string;
}
interface ToastState {
  toasts: Toast[];
  push: (t: Omit<Toast, "id">) => void;
  dismiss: (id: string) => void;
}
export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (t) =>
    set((s) => ({ toasts: [...s.toasts, { ...t, id: Math.random().toString(36).slice(2) }] })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));
