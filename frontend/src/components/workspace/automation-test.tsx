"use client";

import { Check, FlaskConical, Minus, Search, TriangleAlert, X } from "lucide-react";
import { useState } from "react";

import { Drawer } from "@/components/ui/drawer";
import { Badge, Button, Input, Spinner } from "@/components/ui/primitives";
import {
  AUTOMATION_ACTION_KINDS,
  AUTOMATION_CONDITION_KINDS,
  summarize,
  type Item,
} from "@/components/workspace/item-builder";
import { FormError } from "@/components/workspace/list-shared";
import { useContactFields } from "@/hooks/use-contacts";
import { MIN_SEARCH_CHARS, useDebouncedValue, useTicketSearch } from "@/hooks/use-global-search";
import {
  useMemberOptions,
  useTeams,
  useTestFlow,
  useTestRule,
  useTicketTypes,
  type RuleDraft,
} from "@/hooks/use-workspace";
import type {
  FlowGraph,
  FlowTestResult,
  RuleTestResult,
  TicketAfter,
} from "@/lib/api/automation-types";
import { ApiError } from "@/lib/api/client";
import type { TicketSummary } from "@/lib/api/types";
import { formatMinutes } from "@/lib/automation/graph";
import { cn } from "@/lib/utils";

const TEST_ONLY = "Test only: nothing on the ticket changes";

function errorText(e: unknown) {
  if (e instanceof ApiError) {
    return e.status === 422
      ? e.details.map((d) => d.message).join(" ") || e.message
      : e.userMessage;
  }
  return "Something went wrong.";
}

export function formatWhen(iso: string | null | undefined) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ---------- ticket picker ---------- */

export function TicketPicker({
  value,
  onPick,
}: {
  value: TicketSummary | null;
  onPick: (t: TicketSummary | null) => void;
}) {
  const [text, setText] = useState("");
  const q = useDebouncedValue(text);
  const results = useTicketSearch(q, !value);

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm">
        <span className="font-medium">{value.label}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{value.subject}</span>
        <button
          type="button"
          onClick={() => onPick(null)}
          className="text-xs font-medium text-primary-ink hover:underline"
        >
          Change
        </button>
      </div>
    );
  }
  // Show results only for the text on screen: while the debounce or the request is
  // catching up, the list would otherwise be empty or belong to an earlier search.
  const settled = text.trim() === q.trim() && !results.isPending && !results.isPlaceholderData;
  const list = settled ? (results.data ?? []) : [];
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground"
          aria-hidden
        />
        <Input
          type="search"
          aria-label="Find a ticket by number or subject"
          placeholder="Ticket number or subject"
          className="pl-9"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      {text.trim().length >= MIN_SEARCH_CHARS && (
        <ul className="max-h-56 overflow-y-auto rounded-lg border border-border">
          {!settled && (
            <li className="p-3">
              <Spinner />
            </li>
          )}
          {settled && list.length === 0 && (
            <li className="p-3 text-sm text-muted-foreground">No tickets found.</li>
          )}
          {list.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                onClick={() => onPick(t)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2"
              >
                <span className="font-medium">{t.label}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{t.subject}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------- the ticket after ---------- */

function Row({ name, value, changed }: { name: string; value: string; changed: boolean }) {
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <dt className="text-muted-foreground">{name}</dt>
      <dd className={cn("text-right", changed && "font-bold text-foreground")}>{value}</dd>
    </div>
  );
}

const cap = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : "None");

/** "The ticket after". Values that differ from the ticket as it is now are bold. */
export function TicketAfterView({
  after,
  before,
}: {
  after: TicketAfter;
  before: TicketSummary | null;
}) {
  const teams = useTeams();
  const types = useTicketTypes();
  const members = useMemberOptions();
  const teamName = (after.team_id && teams.data?.find((t) => t.id === after.team_id)?.name) || "";
  const typeName = (after.type_id && types.data?.find((t) => t.id === after.type_id)?.name) || "";
  const followers = after.followers.map(
    (id) => members.data?.find((m) => m.id === id)?.name ?? "Someone",
  );
  const tagsBefore = new Set(before?.tags ?? []);
  const newTags = after.tags.filter((t) => !tagsBefore.has(t));
  const known = !!before;

  return (
    <section aria-label="The ticket after" className="rounded-lg border border-border p-3">
      <h4 className="mb-1 text-[13px] font-semibold">The ticket after</h4>
      <dl className="divide-y divide-border">
        <Row
          name="Status"
          value={cap(after.status)}
          changed={known && before.status !== after.status}
        />
        <Row
          name="Priority"
          value={cap(after.priority)}
          changed={known && before.priority !== after.priority}
        />
        <Row
          name="Team"
          value={teamName || "None"}
          changed={known && (before.team_name ?? "") !== teamName}
        />
        <Row
          name="Type"
          value={typeName || "None"}
          changed={known && (before.type_name ?? "") !== typeName}
        />
        <Row name="Tags" value={after.tags.join(", ") || "None"} changed={newTags.length > 0} />
        <Row name="Snoozed" value={after.snoozed ? "Yes" : "No"} changed={after.snoozed} />
        <Row name="Followers" value={followers.join(", ") || "None"} changed={false} />
      </dl>
    </section>
  );
}

function TestOnlyNote() {
  return (
    <p className="flex items-center gap-2 rounded-lg bg-info-soft px-3 py-2 text-xs text-info">
      <FlaskConical className="size-3.5 flex-none" aria-hidden /> {TEST_ONLY}
    </p>
  );
}

function BlockedNote({ text }: { text: string }) {
  return (
    <p
      role="note"
      className="flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning"
    >
      <TriangleAlert className="mt-0.5 size-3.5 flex-none" aria-hidden />
      <span>Right now this would be stopped: {text}</span>
    </p>
  );
}

function Tick({ ok }: { ok: boolean }) {
  return ok ? (
    <Check className="size-4 flex-none text-success" aria-label="Matched" />
  ) : (
    <X className="size-4 flex-none text-danger" aria-label="Did not match" />
  );
}

/* ---------- rule test ---------- */

export function RuleTestDrawer({
  open,
  onClose,
  title,
  ruleId,
  draft,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** A saved rule, or ... */
  ruleId?: string;
  /** ... what is in the editor (not saved yet). */
  draft?: RuleDraft;
}) {
  const test = useTestRule();
  const [ticket, setTicket] = useState<TicketSummary | null>(null);
  const [result, setResult] = useState<RuleTestResult | null>(null);

  const run = (t: TicketSummary) =>
    test.mutate(
      { ticketId: t.id, ruleId, draft },
      { onSuccess: setResult, onError: () => setResult(null) },
    );

  return (
    <Drawer open={open} onClose={onClose} title={title} description="Test on a ticket">
      <div className="space-y-4">
        <TestOnlyNote />
        <TicketPicker
          value={ticket}
          onPick={(t) => {
            setTicket(t);
            setResult(null);
            test.reset();
          }}
        />
        <Button disabled={!ticket} loading={test.isPending} onClick={() => ticket && run(ticket)}>
          Run test
        </Button>
        {test.isError && <FormError message={errorText(test.error)} />}
        {result && <RuleTestResultView result={result} ticket={ticket} />}
      </div>
    </Drawer>
  );
}

function RuleTestResultView({
  result,
  ticket,
}: {
  result: RuleTestResult;
  ticket: TicketSummary | null;
}) {
  const contactFields = useContactFields().data;
  return (
    <div className="space-y-3">
      <Badge tone={result.matched ? "success" : "neutral"} className="text-xs">
        {result.matched ? "Matched" : "Did not match"}
      </Badge>
      {result.blocked && <BlockedNote text={result.blocked} />}

      <section aria-label="Conditions">
        <h4 className="mb-1 text-[13px] font-semibold">Conditions</h4>
        <ul className="space-y-1">
          {result.conditions.map((c, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <Tick ok={c.matched} />
              <span>
                {summarize(c.condition as Item, AUTOMATION_CONDITION_KINDS, contactFields)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Actions">
        <h4 className="mb-1 text-[13px] font-semibold">
          {result.matched ? "Actions it would run" : "Actions (not run: it did not match)"}
        </h4>
        <ul className="space-y-1">
          {result.actions.map((a, i) => (
            <li
              key={i}
              className={cn(
                "flex items-start gap-2 text-sm",
                (a.would === null || !result.matched) && "text-subtle-foreground",
              )}
            >
              {a.would === null ? (
                <Minus className="size-4 flex-none" aria-hidden />
              ) : (
                <Check className="size-4 flex-none" aria-hidden />
              )}
              <span>
                {a.would ?? "No change"}
                <span className="sr-only">
                  {" "}
                  ({summarize(a.action as Item, AUTOMATION_ACTION_KINDS)})
                </span>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <TicketAfterView after={result.ticket_after} before={ticket} />
    </div>
  );
}

/* ---------- workflow test ---------- */

const OUTCOME_LABEL: Record<string, string> = {
  yes: "Yes",
  no: "No",
  done: "Done",
  no_change: "No change",
  "no change": "No change",
  waiting: "Waiting",
};
export const outcomeLabel = (o: string) => OUTCOME_LABEL[o] ?? cap(o);

export function FlowTestResultView({
  result,
  ticket,
  nodeName,
}: {
  result: FlowTestResult;
  ticket: TicketSummary | null;
  nodeName?: (id: string) => string;
}) {
  const w = result.would_wait;
  return (
    <div className="space-y-3">
      {result.stopped && (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          Stopped: {result.stopped}
        </p>
      )}
      {result.blocked && <BlockedNote text={result.blocked} />}
      <ol className="space-y-1">
        {result.steps.map((s, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{nodeName?.(s.node) ?? s.node}</span>
            <Badge tone={s.outcome === "waiting" ? "info" : "neutral"}>
              {outcomeLabel(s.outcome)}
            </Badge>
            {s.said && <span className="text-muted-foreground">{s.said}</span>}
          </li>
        ))}
        {result.steps.length === 0 && (
          <li className="text-sm text-muted-foreground">The workflow ends at once.</li>
        )}
      </ol>
      {w && (
        <p className="rounded-lg bg-info-soft px-3 py-2 text-sm text-info">
          It would wait {formatMinutes(w.minutes)}
          {w.until_reply ? " (or until the customer replies)" : ""}, until {formatWhen(w.resume_at)}
          .
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        After the wait: test again on a ticket in that state.
      </p>
      <TicketAfterView after={result.ticket_after} before={ticket} />
    </div>
  );
}

/**
 * Test a workflow: the saved one (`flowId`) or the graph on the canvas (`graph`).
 * On the canvas the answer is handed back (`onResult`) so the path can be drawn there.
 */
export function FlowTestDrawer({
  open,
  onClose,
  title,
  flowId,
  graph,
  onResult,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  flowId?: string;
  graph?: FlowGraph;
  onResult?: (result: FlowTestResult, ticket: TicketSummary) => void;
}) {
  const test = useTestFlow();
  const [ticket, setTicket] = useState<TicketSummary | null>(null);
  const [result, setResult] = useState<FlowTestResult | null>(null);

  const run = (t: TicketSummary) =>
    test.mutate(
      { ticketId: t.id, flowId, graph },
      {
        onSuccess: (r) => {
          if (onResult) {
            onResult(r, t);
            onClose();
          } else setResult(r);
        },
        onError: () => setResult(null),
      },
    );

  return (
    <Drawer open={open} onClose={onClose} title={title} description="Test on a ticket">
      <div className="space-y-4">
        <TestOnlyNote />
        <TicketPicker
          value={ticket}
          onPick={(t) => {
            setTicket(t);
            setResult(null);
            test.reset();
          }}
        />
        <Button disabled={!ticket} loading={test.isPending} onClick={() => ticket && run(ticket)}>
          Run test
        </Button>
        {test.isError && <FormError message={errorText(test.error)} />}
        {result && <FlowTestResultView result={result} ticket={ticket} />}
      </div>
    </Drawer>
  );
}
