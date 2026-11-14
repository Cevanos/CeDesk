"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";

export interface AppField {
  key: string;
  label?: string;
  type?: string;
  required?: boolean;
  hint?: string;
  options?: string[];
}

export interface AppCredential {
  key?: string;
  label?: string;
  hint?: string;
}

export interface WorkspaceApp {
  /** Slug used in /apps/{app}. */
  app: string;
  name?: string;
  description?: string;
  installed?: boolean;
  settings_fields?: AppField[];
  settings?: Record<string, unknown>;
  /** null: the app needs no credential (e.g. Calendly). */
  credential?: AppCredential | string | null;
  /** e.g. `signing_secret` for Slack side conversations. */
  second_credential?: AppCredential | string | null;
  secret_hint?: string | null;
  blocked?: string | null;
  /** Where the app shows up, e.g. `side` (Slack) or `book` (Calendly). */
  card?: string | null;
}

/** The answer to installing or saving an app. `note` is a warning to show, when set. */
export interface AppSaveResult {
  note?: string | null;
}

export const appsKey = ["apps"] as const;

export function useApps() {
  return useQuery({
    queryKey: appsKey,
    queryFn: () => api.get<unknown>("apps"),
    select: listOf<WorkspaceApp>,
    staleTime: 30_000,
  });
}

/** The key a second credential is sent under, e.g. `signing_secret`. */
export function secondKey(c: AppCredential | string | null | undefined): string {
  return (typeof c === "string" ? c : c?.key) || "signing_secret";
}

export interface AppInstallInput {
  app: string;
  settings?: Record<string, unknown>;
  /** Sent as `secret`. */
  secret?: string;
  /** Extra body fields, e.g. `{ signing_secret }`. */
  extra?: Record<string, string>;
}

export function useSaveApp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ app, extra, ...body }: AppInstallInput) =>
      api.put<AppSaveResult | null>(`apps/${app}`, { ...body, ...extra }),
    onSuccess: () => qc.invalidateQueries({ queryKey: appsKey }),
  });
}

export function useUninstallApp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (app: string) => api.delete<void>(`apps/${app}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: appsKey }),
  });
}
