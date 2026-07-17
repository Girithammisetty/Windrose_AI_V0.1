import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { SessionProvider } from "@/lib/session/SessionContext";

export function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SessionProvider value={{ userId: "u", tenantId: "t-acme", workspaceId: "ws", scopes: [] }}>
        {ui}
      </SessionProvider>
    </QueryClientProvider>,
  );
}
