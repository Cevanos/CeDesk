"use client";

import { CheckCircle2, MailX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { usePublicUnsubscribe, usePublicUnsubscribeSubmit } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

/** Never show a raw error: a busy IP gets one sentence, everything else another. */
function failText(e: unknown): string {
  return e instanceof ApiError && e.status === 429
    ? "Too many tries. Please wait a few minutes."
    : "Something went wrong. Please try again.";
}

/**
 * The page behind the footer link of every campaign email: `/unsubscribe#token=...`.
 * The token is in the hash so it never reaches a server log. Opening the page changes
 * nothing (mail scanners open links on their own); only the button unsubscribes.
 */
export function UnsubscribePage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const found = useRef<string | null>(null);

  useEffect(() => {
    // StrictMode runs this twice in development: the second run must not see an empty address.
    if (found.current === null) {
      const raw = /(?:^|[#&])token=([^&]+)/.exec(window.location.hash)?.[1] ?? "";
      let t = raw;
      try {
        t = decodeURIComponent(raw);
      } catch {
        /* keep raw */
      }
      found.current = t;
      if (t) window.history.replaceState(null, "", window.location.pathname);
    }
    setToken(found.current || null);
  }, []);

  return (
    <div className="mx-auto w-full max-w-xl animate-rise">
      {token === undefined ? (
        <Skeleton className="h-60 rounded-2xl" />
      ) : token === null ? (
        <Shell title="This link is not valid any more.">
          <p className="mt-2 text-sm text-muted-foreground">
            Please open the unsubscribe link from your email again.
          </p>
        </Shell>
      ) : (
        <Unsubscribe token={token} />
      )}
    </div>
  );
}

function Shell({
  title,
  children,
  done,
}: {
  title: string;
  children?: React.ReactNode;
  done?: boolean;
}) {
  return (
    <Card className="rounded-2xl p-8 text-center shadow-pop">
      <div
        className={
          done
            ? "mx-auto grid size-14 place-items-center rounded-2xl bg-success-soft text-success"
            : "mx-auto grid size-14 place-items-center rounded-2xl bg-primary-soft text-primary-ink"
        }
      >
        {done ? <CheckCircle2 className="size-7" /> : <MailX className="size-7" />}
      </div>
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">{title}</h1>
      {children}
    </Card>
  );
}

function Unsubscribe({ token }: { token: string }) {
  const info = usePublicUnsubscribe(token);
  const submit = usePublicUnsubscribeSubmit(token);

  if (info.isPending) return <Skeleton className="h-60 rounded-2xl" />;

  if (submit.isSuccess) {
    return (
      <Shell title="You're unsubscribed." done>
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          {submit.data.message || "You won't get these emails any more."}
        </p>
        <Replies />
      </Shell>
    );
  }
  if (info.isSuccess && info.data.unsubscribed) {
    return (
      <Shell title="You're unsubscribed." done>
        <p className="mt-2 text-sm text-muted-foreground">
          You won&apos;t get these emails any more.
        </p>
        <Replies />
      </Shell>
    );
  }

  // An unknown link still gets the button: it is harmless, and the answer is the same.
  const name = info.isSuccess ? info.data.workspace_name : null;
  const error = info.isError ? failText(info.error) : submit.isError ? failText(submit.error) : null;

  return (
    <Shell title={name ? `Unsubscribe from ${name} emails?` : "This link is not valid any more."}>
      {error && (
        <p role="alert" className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      <Button
        size="lg"
        className="mt-6 w-full"
        loading={submit.isPending}
        onClick={() => submit.mutate()}
      >
        Unsubscribe
      </Button>
      <Replies />
    </Shell>
  );
}

function Replies() {
  return (
    <p className="mt-4 text-xs text-subtle-foreground">
      You will still get replies to your support requests.
    </p>
  );
}
