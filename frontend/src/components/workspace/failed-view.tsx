"use client";

import { CheckCircle2, RotateCw } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Badge, Button, Card, Skeleton } from "@/components/ui/primitives";
import { LoadError } from "@/components/workspace/list-shared";
import {
  useFailedEmails,
  useFailedEvents,
  useRetryEmail,
  useRetryEvent,
} from "@/hooks/use-workspace";
import { formatDateTime } from "@/lib/utils";

function None({ what }: { what: string }) {
  return (
    <Card className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
      <CheckCircle2 className="size-4 text-success" aria-hidden /> No failed {what}.
    </Card>
  );
}

/** Admin: anything that failed to leave the system, with a "Send again" button. */
export function FailedView() {
  return (
    <div className="space-y-8">
      <FailedEmails />
      <FailedEvents />
    </div>
  );
}

function FailedEmails() {
  const q = useFailedEmails();
  const retry = useRetryEmail();
  return (
    <section aria-labelledby="failed-emails">
      <h2 id="failed-emails" className="text-base font-semibold">
        Failed emails
      </h2>
      <p className="mt-1 mb-3 text-sm text-muted-foreground">
        Replies that could not be delivered after several tries.
      </p>
      {q.isPending ? (
        <Skeleton className="h-20" />
      ) : q.isError ? (
        <LoadError error={q.error} />
      ) : q.data.length === 0 ? (
        <None what="emails" />
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {q.data.map((m) => (
              <li key={m.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 basis-64">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{m.subject || "(no subject)"}</span>
                    {m.ticket_label && m.ticket_id && (
                      <Link
                        href={`/tickets/${m.ticket_id}`}
                        className="font-mono text-xs text-primary-ink hover:underline"
                      >
                        {m.ticket_label}
                      </Link>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    To {m.to.join(", ")} · {m.attempts} {m.attempts === 1 ? "try" : "tries"} ·{" "}
                    {formatDateTime(m.created_at)}
                  </div>
                  {m.last_error && <p className="mt-1 text-xs text-danger">{m.last_error}</p>}
                </div>
                {m.can_retry ? (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retry.isPending && retry.variables === m.id}
                    disabled={retry.isPending}
                    onClick={() =>
                      retry.mutate(m.id, { onSuccess: () => toast.success("Queued to send again") })
                    }
                  >
                    <RotateCw className="size-3.5" /> Send again
                  </Button>
                ) : (
                  <Badge>Can&apos;t retry</Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}

function FailedEvents() {
  const q = useFailedEvents();
  const retry = useRetryEvent();
  return (
    <section aria-labelledby="failed-events">
      <h2 id="failed-events" className="text-base font-semibold">
        Failed events
      </h2>
      <p className="mt-1 mb-3 text-sm text-muted-foreground">
        Messages to other systems that did not go through, for example a reply to a Cevanos chat
        while Cevanos could not be reached.
      </p>
      {q.isPending ? (
        <Skeleton className="h-20" />
      ) : q.isError ? (
        <LoadError error={q.error} />
      ) : q.data.length === 0 ? (
        <None what="events" />
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {q.data.map((ev) => (
              <li key={ev.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 basis-64">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{ev.event}</span>
                    {ev.ticket_id && (
                      <Link
                        href={`/tickets/${ev.ticket_id}`}
                        className="text-xs text-primary-ink hover:underline"
                      >
                        Open ticket
                      </Link>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Failed {formatDateTime(ev.failed_at)} · {ev.attempts}{" "}
                    {ev.attempts === 1 ? "try" : "tries"}
                  </div>
                  {ev.last_error && <p className="mt-1 text-xs text-danger">{ev.last_error}</p>}
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  loading={retry.isPending && retry.variables === ev.id}
                  disabled={retry.isPending}
                  onClick={() =>
                    retry.mutate(ev.id, { onSuccess: () => toast.success("Queued to send again") })
                  }
                >
                  <RotateCw className="size-3.5" /> Send again
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}
