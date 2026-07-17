"use client";
import { createContext, useContext } from "react";

export interface SessionInfo {
  userId: string;
  tenantId: string;
  workspaceId: string;
  scopes: string[];
}

const Ctx = createContext<SessionInfo | null>(null);

export function SessionProvider({ value, children }: { value: SessionInfo; children: React.ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionInfo {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession must be used within SessionProvider");
  return s;
}
