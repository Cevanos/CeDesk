"use client";

import { ListChecks, MailPlus, MoreHorizontal } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Input } from "@/components/ui/primitives";
import { useSendTranscript } from "@/hooks/use-inbox-tools";
import { useAutomationAccess } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import { apiMessage } from "@/lib/api/messages";
import type { Ticket } from "@/lib/api/types";

/**
 * The ticket page "…" menu. The transcript item needs the right to send transcripts and change
 * the ticket (`canTranscript`); "Automation runs" needs the Settings right.
 */
export function TicketMoreMenu({
  ticket,
  canTranscript = true,
}: {
  ticket: Pick<Ticket, "id" | "contact">;
  canTranscript?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const automation = useAutomationAccess();
  return (
    <>
      <Popover
        className="w-56"
        trigger={({ props }) => (
          <Button variant="outline" size="icon" aria-label="More actions" {...props}>
            <MoreHorizontal className="size-4" />
          </Button>
        )}
      >
        {(close) => (
          <>
            {canTranscript && (
              <MenuItem
                onClick={() => {
                  close();
                  setOpen(true);
                }}
              >
                <MailPlus className="size-4" aria-hidden /> Email transcript
              </MenuItem>
            )}
            {automation.settings && (
              <Link
                role="menuitem"
                href={`/settings/automation/log?ticket_id=${ticket.id}`}
                onClick={close}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-foreground-2 transition-colors hover:bg-surface-2"
              >
                <ListChecks className="size-4" aria-hidden /> Automation runs
              </Link>
            )}
          </>
        )}
      </Popover>
      {open && <TranscriptDialog ticket={ticket} onClose={() => setOpen(false)} />}
    </>
  );
}

function TranscriptDialog({
  ticket,
  onClose,
}: {
  ticket: Pick<Ticket, "id" | "contact">;
  onClose: () => void;
}) {
  const customerEmail = ticket.contact?.email ?? "";
  const send = useSendTranscript(ticket.id);
  const [mode, setMode] = useState<"customer" | "other">(customerEmail ? "customer" : "other");
  const [to, setTo] = useState("");
  const [error, setError] = useState<{ message: string; settings?: boolean } | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const address = to.trim();
    if (mode === "other" && !address) return setError({ message: "Enter an email address." });
    send.mutate(mode === "other" ? address : undefined, {
      onSuccess: (r) => {
        const n = r.messages;
        toast.success(`Transcript sent to ${r.to} (${n} ${n === 1 ? "message" : "messages"})`);
        onClose();
      },
      onError: (err) => {
        const message = apiMessage(err);
        if (err instanceof ApiError) {
          // A chat customer has no address: switch to typing one.
          if (err.status === 422 && /no email address/i.test(message)) setMode("other");
          // No support address yet: the fix is in Settings › Email.
          if (err.status === 409 && /settings/i.test(message)) {
            return setError({ message, settings: true });
          }
        }
        setError({ message });
      },
    });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Email transcript"
      description="Sends the conversation the customer saw. Private notes and system lines are left out."
    >
      <form onSubmit={submit} noValidate className="space-y-3">
        <fieldset className="space-y-2">
          <legend className="sr-only">Send to</legend>
          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="radio"
              name="transcript-to"
              className="mt-0.5 accent-[var(--primary)]"
              checked={mode === "customer"}
              disabled={!customerEmail}
              onChange={() => setMode("customer")}
            />
            <span>
              To the customer
              {customerEmail ? (
                <span className="text-muted-foreground"> ({customerEmail})</span>
              ) : (
                <span className="block text-xs text-muted-foreground">
                  This customer has no email address.
                </span>
              )}
            </span>
          </label>
          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="radio"
              name="transcript-to"
              className="mt-0.5 accent-[var(--primary)]"
              checked={mode === "other"}
              onChange={() => setMode("other")}
            />
            <span>To another address</span>
          </label>
        </fieldset>
        {mode === "other" && (
          <Input
            type="email"
            aria-label="Email address"
            placeholder="name@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        )}
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            {error.message}
            {error.settings && (
              <>
                {" "}
                <Link href="/settings/email" className="font-medium underline">
                  Open Settings › Email
                </Link>
              </>
            )}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={send.isPending}>
            Send
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
