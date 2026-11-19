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
import { useConfirm } from "@/components/ui/confirm";
import { Badge, Button, Input, Textarea } from "@/components/ui/primitives";
import { useCreateJira, useJira, useLinkJira, useUnlinkJira } from "@/hooks/use-ticket-extras";

function JiraForm({
  ticketId,
  subject,
  close,
}: {
  ticketId: string;
  subject?: string;
  close: () => void;
}) {
  const create = useCreateJira(ticketId);
  const link = useLinkJira(ticketId);
  const [key, setKey] = useState("");
  const [creating, setCreating] = useState(false);
  const [summary, setSummary] = useState(subject ?? "");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const done = () => {
    toast.success("Jira updated");
    close();
  };

  if (creating)
    return (
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const s = summary.trim();
          const d = description.trim();
          create.mutate(
            { ...(s ? { summary: s } : {}), ...(d ? { description: d } : {}) },
            { onSuccess: done, onError: (err) => setError(errText(err)) },
          );
        }}
      >
        <Input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          placeholder="Summary"
          aria-label="Jira issue summary"
          maxLength={255}
          className="h-9"
        />
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          aria-label="Jira issue description"
          rows={4}
        />
        <p className="text-xs text-muted-foreground">
          The customer&apos;s messages aren&apos;t sent. Add what engineering needs.
        </p>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" type="submit" loading={create.isPending}>
            Create
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>
            Cancel
          </Button>
        </div>
      </form>
    );

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
          onClick={() => {
            setError(null);
            setCreating(true);
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

/** The "Jira KAN-5" chip for the ticket header, linking to the first issue. Renders nothing without one. */
export function JiraHeaderChip({ ticketId }: { ticketId: string }) {
  const q = useJira(ticketId);
  const issues = q.data ?? [];
  const first = issues[0];
  if (!first) return null;
  const label = `Jira ${first.key}${issues.length > 1 ? ` +${issues.length - 1}` : ""}`;
  const title = issues.map((j) => j.key).join(", ");
  return first.url && /^https?:\/\//i.test(first.url) ? (
    <a href={first.url} target="_blank" rel="noopener noreferrer" title={title}>
      <Badge className="hover:underline">
        {label} <ExternalLink className="size-3" aria-hidden />
      </Badge>
    </a>
  ) : (
    <Badge title={title}>{label}</Badge>
  );
}

export function JiraCard({ ticketId, canEdit, subject }: ExtraProps & { subject?: string }) {
  const q = useJira(ticketId);
  const unlink = useUnlinkJira(ticketId);
  const { ask, dialog } = useConfirm();
  return (
    <ExtraCard title="Jira">
      {dialog}
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
                      onClick={() =>
                        ask({
                          title: "Unlink this Jira issue?",
                          message: "It stays in Jira; it's only detached from this ticket.",
                          detail: j.key,
                          confirmLabel: "Unlink",
                          run: () => unlink.mutate(j.key, { onError: toastError }),
                        })
                      }
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
              {(close) => <JiraForm ticketId={ticketId} subject={subject} close={close} />}
            </AddToggle>
          </div>
        </>
      )}
    </ExtraCard>
  );
}
