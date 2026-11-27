"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

/** An AI app (Claude, ChatGPT, Codex, ...) as the backend describes it. */
export interface ConnectedApp {
  client_id: string;
  name: string;
  client_uri: string | null;
  logo_uri: string | null;
  registration: "dynamic" | "metadata_document" | "manual";
  domain: string | null;
}

/** GET /oauth/consent: what the app asks for, and who it will act as. */
export interface ConsentRequest {
  app: ConnectedApp;
  redirect_host: string;
  scopes: string[];
  resource: string;
  workspace: { id: string; name: string; slug: string | null };
  member: { id: string; name: string; email: string };
}

export interface ConnectedAppGrant {
  id: string;
  app: ConnectedApp;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
}

export const connectedAppsKey = ["oauth", "grants"] as const;

export function useConsentRequest(handle: string | null) {
  return useQuery({
    queryKey: ["oauth", "consent", handle],
    queryFn: () => api.get<ConsentRequest>("oauth/consent", { request: handle }),
    enabled: !!handle,
    retry: false,
  });
}

export function useDecideConsent() {
  return useMutation({
    mutationFn: (body: { request: string; decision: "approve" | "deny" }) =>
      api.post<{ redirect_to: string }>("oauth/consent", body),
  });
}

export function useConnectedApps() {
  return useQuery({
    queryKey: connectedAppsKey,
    queryFn: () => api.get<ConnectedAppGrant[]>("oauth/grants"),
  });
}

export function useDisconnectApp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`oauth/grants/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: connectedAppsKey }),
  });
}

/** The scopes in plain words, for the consent screen. */
export const SCOPE_WORDS: Record<string, string> = {
  read: "Read the tickets, reports and settings you can see",
  write: "Reply to tickets, and change the tickets and settings you can change",
};
