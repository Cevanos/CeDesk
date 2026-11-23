"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

export interface SignupConfig {
  enabled: boolean;
  captchaSiteKey: string | null;
}

export interface SignupStarted {
  signup_id: string;
  code_expires_at: string;
  resend_after: string;
}

export interface SignupInput {
  workspace: string;
  workspace_name: string;
  name: string;
  email: string;
  password: string;
  captcha_token?: string | null;
}

/** Is self-service sign-up on? Any error (an older backend answers 404) counts as off. */
export function useSignupConfig() {
  return useQuery({
    queryKey: ["signup-config"] as const,
    meta: { silent: true },
    retry: false,
    staleTime: 5 * 60_000,
    queryFn: async (): Promise<SignupConfig> => {
      const d = await api.get<{ enabled?: unknown; captcha_site_key?: unknown } | null>(
        "auth/signup-config",
      );
      const key =
        typeof d?.captcha_site_key === "string" && d.captcha_site_key ? d.captcha_site_key : null;
      return { enabled: d?.enabled === true, captchaSiteKey: key };
    },
  });
}

// The password and the code are only ever sent; they are never cached, logged or stored.
export function useStartSignup() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: SignupInput) => api.post<SignupStarted>("auth/signup", v),
  });
}

export function useVerifySignup() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { signup_id: string; code: string; workspace?: string }) =>
      api.post<{ workspace: string }>("auth/signup/verify", v),
  });
}

export function useResendSignupCode() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { signup_id: string }) => api.post<SignupStarted>("auth/signup/resend", v),
  });
}
