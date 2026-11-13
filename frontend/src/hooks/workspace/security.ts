"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

/** Field names are inferred from the handoff doc; the server may send more, which we keep. */
export interface SecuritySettings {
  min_password_length?: number | null;
  password_expiry_days?: number | null;
  session_hours?: number | null;
  require_2fa?: boolean;
  ip_allowlist?: string[];
  files_require_signin?: boolean;
  admin_alerts?: boolean;
  [key: string]: unknown;
}

export interface SecurityScore {
  score?: number;
  max?: number;
  checks?: { key?: string; label?: string; ok?: boolean; passed?: boolean; hint?: string }[];
  [key: string]: unknown;
}

export interface TestIpResult {
  ip?: string;
  allowed?: boolean;
  [key: string]: unknown;
}

export interface SsoSettings {
  enabled?: boolean;
  provider?: string;
  issuer?: string;
  client_id?: string;
  client_secret?: string;
  secret_hint?: string;
  required?: boolean;
  [key: string]: unknown;
}

export const securityKeys = {
  settings: ["security", "settings"] as const,
  score: ["security", "score"] as const,
  sso: ["security", "sso"] as const,
};

export function useSecuritySettings() {
  return useQuery({
    queryKey: securityKeys.settings,
    queryFn: ({ signal }) => api.get<SecuritySettings>("security-settings", undefined, signal),
  });
}

export function useSaveSecuritySettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SecuritySettings) => api.put<SecuritySettings>("security-settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security"] }),
  });
}

export function useSecurityScore() {
  return useQuery({
    queryKey: securityKeys.score,
    queryFn: ({ signal }) => api.get<SecurityScore>("security/score", undefined, signal),
  });
}

/** `ips` tests an unsaved list; omit it to test the saved one. */
export function useTestMyIp() {
  return useMutation({
    mutationFn: (ips?: string[]) =>
      api.post<TestIpResult>("security/test-my-ip", ips ? { ip_allowlist: ips } : {}),
  });
}

export function useSsoSettings() {
  return useQuery({
    queryKey: securityKeys.sso,
    queryFn: ({ signal }) => api.get<SsoSettings>("sso-settings", undefined, signal),
  });
}

export function useSaveSsoSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SsoSettings) => api.put<SsoSettings>("sso-settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: securityKeys.sso }),
  });
}
