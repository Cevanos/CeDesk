"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { request } from "@/lib/api/client";
import { hardNavigate } from "@/lib/navigation";
import { safeNext } from "@/lib/safe-next";

/** An error whose message is safe to show as is. */
class SsoShown extends Error {}

const GENERIC = "That sign-in is not valid. Start again.";

/** Reads the hand-off from the address fragment. Returns it, and nothing else keeps it. */
export function readFragment(hash: string) {
  const p = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  return {
    token: p.get("token"),
    expiresAt: p.get("expires_at"),
    next: p.get("next"),
    error: p.get("error"),
  };
}

/**
 * Where the API sends the browser after single sign-on (`/sso/callback#token=…`). The token is
 * a live session: read it, wipe it from the address bar at once, hand it to the proxy (which
 * checks it and sets httpOnly cookies), then go on.
 */
export function SsoCallback() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  // StrictMode runs effects twice in development; the fragment is gone after the first read.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const { token, expiresAt, next, error: apiError } = readFragment(window.location.hash);
    // Nothing may stay in the address bar or the history entry.
    window.history.replaceState(null, "", window.location.pathname);

    // Errors are set from the promise callback, not synchronously in the effect body.
    const job: Promise<unknown> = apiError
      ? Promise.reject(new SsoShown(apiError))
      : !token
        ? Promise.reject(new SsoShown(GENERIC))
        : request("auth/sso-session", {
            method: "POST",
            body: { token, expires_at: expiresAt ?? undefined },
          }).then(() => {
            qc.clear();
            hardNavigate(safeNext(next));
          });
    job.catch((e) => setError(e instanceof SsoShown ? e.message : GENERIC));
  }, [qc]);

  if (error) {
    return (
      <div className="animate-rise">
        <h1 className="text-[1.75rem] font-semibold tracking-tight">Sign-in didn&apos;t finish</h1>
        <div
          role="alert"
          className="mt-6 flex items-start gap-2.5 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-3 text-sm text-danger"
        >
          <AlertCircle className="mt-0.5 size-4 flex-none" />
          <div>{error}</div>
        </div>
        <Link
          href="/login"
          className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-lg border border-border bg-surface text-sm font-medium hover:bg-surface-2"
        >
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div
      className="flex animate-rise items-center gap-3 text-sm text-muted-foreground"
      role="status"
    >
      <Loader2 className="size-4 animate-spin" /> Signing you in…
    </div>
  );
}
