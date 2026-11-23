"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

export type SessionLength = "1h_idle" | "8h" | "12h" | "7d";

/** GET/PUT /security-settings. A PUT sends only what changed; omitted fields keep their value. */
export interface SecuritySettings {
  password_min: number;
  /** null = never expires. */
  password_expiry_days: number | null;
  session_length: SessionLength;
  require_2fa: boolean;
  attachments_need_sign_in: boolean;
  admin_alerts: boolean;
  /** Empty = everyone may sign in. */
  ip_allowlist: string[];
  updated_by_name?: string | null;
  updated_at?: string | null;
}

export type SecuritySettingsInput = Partial<
  Omit<SecuritySettings, "updated_by_name" | "updated_at">
>;

export type ScoreLevel = "healthy" | "recommended" | "critical";

export interface SecurityScore {
  score: number;
  level: ScoreLevel;
  items: { key: string; points: number; on: boolean; status?: string }[];
}

export interface TestIpResult {
  ip: string;
  allowed: boolean;
}

/** GET/PUT /sso-settings (SAML). `sp_entity_id`, `acs_url` and `metadata_url` are read-only. */
export interface SsoSettings {
  enabled: boolean;
  enforced: boolean;
  idp_entity_id: string | null;
  idp_sso_url: string | null;
  idp_cert: string | null;
  sp_entity_id?: string;
  acs_url?: string;
  metadata_url?: string;
}

export type SsoSettingsInput = Pick<
  SsoSettings,
  "enabled" | "enforced" | "idp_entity_id" | "idp_sso_url" | "idp_cert"
>;

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
    mutationFn: (body: SecuritySettingsInput) =>
      api.put<SecuritySettings>("security-settings", body),
    // The score depends on these settings, so reload it after every save.
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
    mutationFn: (body: SsoSettingsInput) => api.put<SsoSettings>("sso-settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security"] }),
  });
}
