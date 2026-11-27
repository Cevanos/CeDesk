"use client";

import { Check, Plug, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { SCOPE_WORDS, useConsentRequest, useDecideConsent } from "@/hooks/workspace/connected-apps";
import { ApiError } from "@/lib/api/client";
import { hardNavigate } from "@/lib/navigation";

/**
 * An AI app asks to act as the signed-in member. Everyone else is sent to /login first
 * by the route guard and comes back here (2FA and SSO included). One member belongs to
 * one workspace, so there is nothing to pick: approve or deny.
 */
export function ConnectConsent() {
  const params = useSearchParams();
  const handle = params.get("request");
  const consent = useConsentRequest(handle);
  const decide = useDecideConsent();
  const [error, setError] = useState<string | null>(null);

  const answer = (decision: "approve" | "deny") => {
    if (!handle) return;
    setError(null);
    decide.mutate(
      { request: handle, decision },
      {
        // Back to the app: the API built it from a redirect URI the app registered.
        onSuccess: (r) => hardNavigate(r.redirect_to),
        onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Something went wrong."),
      },
    );
  };

  if (!handle) {
    return <Problem>This link is missing its request. Start again from the app.</Problem>;
  }
  if (consent.isPending) return <Skeleton className="h-72" />;
  if (consent.isError) {
    const e = consent.error;
    if (e instanceof ApiError && e.code === "TwoFactorSetupRequired") {
      return (
        <Problem>
          Your workspace requires two-step sign-in.{" "}
          <Link className="font-medium underline" href="/account?setup=2fa">
            Set it up
          </Link>
          , then start again from the app.
        </Problem>
      );
    }
    return <Problem>{e instanceof ApiError ? e.userMessage : "Something went wrong."}</Problem>;
  }

  const { app, member, workspace, scopes, redirect_host } = consent.data;
  return (
    <div>
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className="grid size-12 flex-none place-items-center rounded-xl bg-primary-soft text-lg font-semibold text-primary-ink"
        >
          {app.name.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Connect {app.name}?</h1>
          {app.domain && <p className="text-sm text-muted-foreground">{app.domain}</p>}
        </div>
      </div>

      <Card className="mt-6 space-y-4 p-4">
        <p className="text-sm">
          <strong>{app.name}</strong> wants to act as <strong>{member.name}</strong> ({member.email}
          ) in <strong>{workspace.name}</strong>, with your role: it can&apos;t do anything you
          can&apos;t.
        </p>
        <ul className="space-y-2">
          {scopes.map((s) => (
            <li key={s} className="flex items-start gap-2 text-sm">
              <Check className="mt-0.5 size-4 flex-none text-success" />
              {SCOPE_WORDS[s] ?? s}
            </li>
          ))}
        </ul>
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-3.5 flex-none" />
          <span>
            After you answer you go back to <strong>{redirect_host}</strong>. Approve only if that
            is the app you are setting up. You can disconnect it any time in My account.
          </span>
        </p>
      </Card>

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" disabled={decide.isPending} onClick={() => answer("deny")}>
          Deny
        </Button>
        <Button loading={decide.isPending} onClick={() => answer("approve")}>
          <Plug className="size-4" /> Approve
        </Button>
      </div>
    </div>
  );
}

function Problem({ children }: { children: React.ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-border bg-surface-2 p-4 text-sm">
      {children}
    </div>
  );
}
