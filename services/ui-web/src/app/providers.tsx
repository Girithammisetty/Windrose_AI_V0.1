"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { GraphQLRequestError } from "@/lib/graphql/client";

/**
 * App-wide providers. TanStack Query owns all server state (UI-FR-040):
 * staleTime 30s for lists; retries skip auth/permission errors (retrying a
 * PERMISSION_DENIED is pointless and leaks nothing).
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: false,
            retry: (count, error) => {
              if (error instanceof GraphQLRequestError) {
                if (["UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND", "VALIDATION_FAILED"].includes(error.code))
                  return false;
              }
              return count < 2;
            },
          },
          mutations: { retry: false }, // BR-6: no auto-retry of mutations.
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
