"use client";

import { ExternalLink, Unlink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AddToggle,
  ExtraCard,
  Muted,
  QueryState,
  errText,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { Badge, Button, Input } from "@/components/ui/primitives";
import { useCreateJira, useJira, useLinkJira, useUnlinkJira } from "@/hooks/use-ticket-extras";

function JiraForm({ ticketId, close }: { ticketId: string; close: () => void }) {
  const create = useCreateJira(ticketId);
  const link = useLinkJira(ticketId);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const done = () => {
    toast.success("Jira updated");
    close();
  };
  return (
    <div className="space-y-2">
      <Input
        value={key}
        onChange={(e) => setKey(e.target.value.toUpperCase())}
        placeholder="Issue key, e.g. ABC-123"
        aria-label="Jira issue key"
        className="h-9"
      />
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={!key.trim()}
          loading={link.isPending}
          onClick={() => {
            setError(null);
            link.mutate(
              { key: key.trim() },
              { onSuccess: done, onError: (e) => setError(errText(e)) },
            );
          }}
        >
          Link existing
        </Button>
        <Button
          size="sm"
          variant="outline"
          loading={create.isPending}
          onClick={() => {
            setError(null);
            create.mutate({}, { onSuccess: done, onError: (e) => setError(errText(e)) });
          }}
        >
          Create new issue
        </Button>
        <Button size="sm" variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function JiraCard({ ticketId, canEdit }: ExtraProps) {
  const q = useJira(ticketId);
  const unlink = useUnlinkJira(ticketId);
  return (
    <ExtraCard title="Jira">
      <QueryState q={q} />
      {q.data && (
        <>
          {q.data.length ? (
            <ul className="mb-3 space-y-2">
              {q.data.map((j) => (
                <li key={j.key} className="flex items-start gap-2 text-sm">
                  <div className="min-w-0 flex-1">
                    {j.url && /^https?:\/\//i.test(j.url) ? (
                      <a
                        href={j.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-primary-ink hover:underline"
                      >
                        {j.key} <ExternalLink className="size-3" aria-hidden />
                      </a>
                    ) : (
                      <span className="font-mono text-xs font-semibold">{j.key}</span>
                    )}
                    {j.status && <Badge className="ml-2">{j.status}</Badge>}
                    {j.summary && <div className="wrap-break-word">{j.summary}</div>}
                  </div>
                  {canEdit && (
                    <button
                      aria-label={`Unlink ${j.key}`}
                      className="grid size-7 flex-none place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
                      onClick={() => unlink.mutate(j.key, { onError: toastError })}
                    >
                      <Unlink className="size-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Muted className="mb-3">No Jira issues.</Muted>
          )}
          <div className="flex">
            <AddToggle label="Add Jira issue" disabled={!canEdit}>
              {(close) => <JiraForm ticketId={ticketId} close={close} />}
            </AddToggle>
          </div>
        </>
      )}
    </ExtraCard>
  );
}
