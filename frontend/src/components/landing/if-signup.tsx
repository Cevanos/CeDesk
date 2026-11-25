"use client";

import { useSignupConfig } from "@/hooks/use-signup";

/**
 * Shows `children` (a "Create a workspace" link) while sign-up is on, and `fallback` once the
 * server says it is off. A self-hosted server has sign-up off by default, so every sign-up
 * button would otherwise lead to "Sign-up isn't available". Until the answer arrives we show
 * the sign-up version, because that is what our cloud site wants.
 */
export function IfSignup({
  children,
  fallback = null,
}: {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { data, isPending } = useSignupConfig();
  return <>{isPending || data?.enabled ? children : fallback}</>;
}
