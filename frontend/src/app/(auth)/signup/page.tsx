import type { Metadata } from "next";
import { headers } from "next/headers";
import { Suspense } from "react";

import { SignupForm } from "@/components/auth/signup-form";

export const metadata: Metadata = { title: "Create a workspace" };

export default async function SignupPage() {
  // The CSP nonce set by src/proxy.ts; the Turnstile loader needs it.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <Suspense>
      <SignupForm nonce={nonce} />
    </Suspense>
  );
}
