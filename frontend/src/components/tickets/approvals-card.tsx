"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  AddToggle,
  ExtraCard,
  Muted,
  QueryState,
  toastError,
  type ExtraProps,
} from "@/components/tickets/extras-shared";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { useApprovals, useAskApproval } from "@/hooks/use-ticket-extras";
import { useAgents } from "@/hooks/use-workspace";
import { formatDateTime } from "@/lib/utils";

const TONE = { approved: "success", rejected: "danger", pending: "warning" } as const;

function AskForm({ ticketId, close }: { ticketId: string; close: () => void }) {
  const agents = useAgents();
  const ask = useAskApproval(ticketId);
  const [who, setWho] = useState("");
  const [title, setTitle] = useState("");
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!who || !title.trim()) return;
        ask.mutate(
          { approver_id: who, title: title.trim() },
          {
            onSuccess: () => {
              toast.success("Approval requested");
              close();
            },
            onError: toastError,
          },
        );
      }}
    >
      <Select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Approver">
        <option value="">Ask who?</option>
        {agents.data?.map((a) => (
          <option key={a.member_id} value={a.member_id}>
            {a.name}
          </option>
        ))}
      </Select>
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="What needs approval?"
        aria-label="What needs approval"
        maxLength={200}
        className="h-9"
      />
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={ask.isPending}>
          Ask
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function ApprovalsCard({ ticketId, canEdit }: ExtraProps) {
  const q = useApprovals(ticketId);
  return (
    <ExtraCard title="Approvals">
      <QueryState q={q} />
      {q.data &&
        (q.data.length ? (
          <ul className="mb-3 space-y-2">
            {q.data.map((a) => {
              const status = (a.status ?? "pending").toLowerCase();
              return (
                <li key={a.id} className="text-sm">
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 flex-1 wrap-break-word">{a.title ?? "Approval"}</span>
                    <Badge tone={TONE[status as keyof typeof TONE] ?? "neutral"}>{status}</Badge>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {a.approver_name ? `Asked ${a.approver_name}` : "Awaiting approver"}
                    {a.created_at && ` · ${formatDateTime(a.created_at)}`}
                  </div>
                  {a.decision_note && (
                    <div className="text-xs text-muted-foreground">“{a.decision_note}”</div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <Muted className="mb-3">No approval requests.</Muted>
        ))}
      {q.data && (
        <div className="flex">
          <AddToggle label="Ask for approval" disabled={!canEdit}>
            {(close) => <AskForm ticketId={ticketId} close={close} />}
          </AddToggle>
        </div>
      )}
    </ExtraCard>
  );
}
