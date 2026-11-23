"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

/** Query keys for this file only (kept local so the shared key list stays untouched). */
export const accountKeys = {
  twoFactor: ["me", "2fa"] as const,
};

/** GET /me/2fa. Field names are inferred; every field is optional and read defensively. */
export interface TwoFactorStatus {
  enabled?: boolean;
  /** The workspace requires 2FA, so it cannot be turned off. */
  required?: boolean;
  recovery_codes_remaining?: number;
  [k: string]: unknown;
}

/** POST /me/2fa/setup: a new secret each time until it is confirmed. */
export interface TwoFactorSetup {
  otpauth_uri: string;
  secret?: string;
}

export interface RecoveryCodes {
  recovery_codes: string[];
}

export function useTwoFactorStatus() {
  return useQuery({
    queryKey: accountKeys.twoFactor,
    queryFn: () => api.get<TwoFactorStatus>("me/2fa"),
    retry: false,
  });
}

function useRefreshAccount() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: accountKeys.twoFactor });
    void qc.invalidateQueries({ queryKey: queryKeys.me });
  };
}

/** Pulls the secret out of an otpauth URI when the API does not send it separately. */
export function secretOf(setup: TwoFactorSetup | undefined): string | undefined {
  if (!setup) return undefined;
  if (setup.secret) return setup.secret;
  try {
    return new URL(setup.otpauth_uri).searchParams.get("secret") ?? undefined;
  } catch {
    return undefined;
  }
}

export function useStartTwoFactorSetup() {
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<TwoFactorSetup>("me/2fa/setup"),
  });
}

export function useConfirmTwoFactor() {
  const refresh = useRefreshAccount();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { code: string }) => api.post<RecoveryCodes>("me/2fa/confirm", v),
    onSuccess: refresh,
  });
}

/** 403 when the workspace requires 2FA. */
export function useDisableTwoFactor() {
  const refresh = useRefreshAccount();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { code: string }) => api.post<void>("me/2fa/disable", v),
    onSuccess: refresh,
  });
}

/** The old codes stop working. Needs a current code from the app. */
export function useNewRecoveryCodes() {
  const refresh = useRefreshAccount();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { code: string }) => api.post<RecoveryCodes>("me/2fa/recovery-codes", v),
    onSuccess: refresh,
  });
}

/* ---------- phones that get push notifications ---------- */

export interface Device {
  id: string;
  platform: "ios" | "android" | (string & {});
  /** The last 6 characters of the token. The full token is never returned. */
  token_hint?: string | null;
  created_at?: string;
  last_seen_at?: string | null;
}
export interface DeviceList {
  /** False: push isn't set up on this server. */
  push_on: boolean;
  devices: Device[];
}

export function useDevices() {
  return useQuery({
    queryKey: ["me", "devices"] as const,
    queryFn: async (): Promise<DeviceList> => {
      const d = await api.get<Partial<DeviceList> | null>("me/devices");
      return { push_on: d?.push_on === true, devices: d?.devices ?? [] };
    },
    retry: false,
  });
}

export function useRemoveDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`me/devices/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me", "devices"] }),
  });
}
