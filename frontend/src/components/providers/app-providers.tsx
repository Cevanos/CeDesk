"use client";

import { QueryCache, QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { useEffect, useState } from "react";
import { Toaster, toast } from "sonner";

import { WebVitals } from "@/components/providers/web-vitals";
import { ApiError, setUnauthorizedHandler } from "@/lib/api/client";
import { hardNavigate } from "@/lib/navigation";

function makeClient() {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        // Background refetch failures are quiet; initial load failures are
        // surfaced by the component that owns the query.
        if (query.state.data !== undefined && error instanceof ApiError && error.status !== 401) {
          toast.error(error.userMessage);
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _v, _c, mutation) => {
        // Mutations that handle their own errors set meta.silent.
        if (mutation.options.meta?.silent) return;
        if (error instanceof ApiError) {
          if (error.status === 401) return;
          // 422 is normally shown next to the field; actions without a form opt in.
          if (error.status === 422) {
            if (mutation.options.meta?.loud422) {
              toast.error(error.details.map((d) => d.message).join(" ") || error.userMessage);
            }
            return;
          }
          if (error.status === 503) {
            // "Retry" re-runs the same call with the same variables (and Idempotency-Key).
            toast.error(error.userMessage, {
              action: {
                label: "Retry",
                onClick: () => {
                  void client
                    .getMutationCache()
                    .build(client, mutation.options)
                    .execute(mutation.state.variables)
                    .catch(() => undefined);
                },
              },
            });
            return;
          }
          toast.error(error.userMessage);
          return;
        }
        // Thrown before a request went out (a bug or an unsupported browser API):
        // never leave the user with a click that does nothing.
        toast.error("Couldn't send. Please try again.");
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (count, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
          return count < 2;
        },
      },
    },
  });
  return client;
}

export function AppProviders({ children, nonce }: { children: React.ReactNode; nonce?: string }) {
  const [client] = useState(makeClient);

  // A 401 anywhere means the session is over, so go to sign-in.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      client.clear();
      const here = window.location.pathname + window.location.search;
      if (!window.location.pathname.startsWith("/login")) {
        hardNavigate(`/login?next=${encodeURIComponent(here)}`);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [client]);

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      nonce={nonce}
    >
      <WebVitals />
      <QueryClientProvider client={client}>
        {children}
        <Toaster
          position="bottom-right"
          richColors
          closeButton
          toastOptions={{ classNames: { toast: "!font-sans !rounded-xl" } }}
        />
        {process.env.NODE_ENV === "development" && <ReactQueryDevtools initialIsOpen={false} />}
      </QueryClientProvider>
    </ThemeProvider>
  );
}
