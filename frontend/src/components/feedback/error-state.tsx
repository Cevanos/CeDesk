"use client";

import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect } from "react";

import { Button } from "@/components/ui/primitives";
import { reportError } from "@/lib/monitoring";

/** Shared body for every `error.tsx` boundary. */
export function ErrorState({
  error,
  reset,
  scope,
  showHome,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  scope: string;
  showHome?: boolean;
}) {
  const t = useTranslations();

  useEffect(() => {
    reportError(error, { scope, digest: error.digest });
  }, [error, scope]);

  return (
    <div role="alert" className="mx-auto grid min-h-[50svh] max-w-md place-items-center px-6 py-16">
      <div className="text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-danger-soft text-danger">
          <AlertTriangle className="size-6" aria-hidden />
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight">{t("errors.title")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("errors.description")}</p>
        {error.digest && (
          <p className="mt-3 font-mono text-xs text-subtle-foreground">
            {t("errors.reference")}: {error.digest}
          </p>
        )}
        <div className="mt-6 flex justify-center gap-2">
          <Button onClick={reset}>{t("common.retry")}</Button>
          {showHome && (
            <Link
              href="/tickets"
              className="inline-flex h-10 items-center rounded-lg border border-border-strong bg-surface px-4 text-sm font-medium hover:bg-surface-2"
            >
              {t("common.backToTickets")}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
