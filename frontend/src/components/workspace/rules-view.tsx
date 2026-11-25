"use client";

import {
  FlaskConical,
  History,
  Lock,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { MenuItem, Dialog, Popover } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { RuleTestDrawer } from "@/components/workspace/automation-test";
import {
  AUTOMATION_ACTION_KINDS,
  AUTOMATION_CONDITION_KINDS,
  automationActionKinds,
  automationConditionKinds,
  ItemBuilder,
  summarize,
  type Item,
} from "@/components/workspace/item-builder";
import {
  FormError,
  fieldErrors,
  LoadError,
  movedIds,
  Problems,
  ReorderButtons,
} from "@/components/workspace/list-shared";
import { ReplayDialog } from "@/components/workspace/replay-dialog";
import { useContactFields } from "@/hooks/use-contacts";
import {
  callsWebhook,
  useAutomationAccess,
  useDeleteRule,
  useReorderRules,
  useRules,
  useSaveRule,
} from "@/hooks/use-workspace";
import type { Rule, RuleEvent } from "@/lib/api/types";

export const EVENT_LABEL: Record<RuleEvent, string> = {
  created: "When a ticket is created",
  customer_replied: "When the customer replies",
  sla_breached: "When the SLA is breached",
  snooze_ended: "When a snooze ends",
};
const EVENT_OPTIONS = (Object.keys(EVENT_LABEL) as RuleEvent[]).map((v) => ({
  value: v,
  label: EVENT_LABEL[v],
}));

const DEFAULT_PER_HOUR = 1000;
const DEFAULT_PER_DAY = 10000;
export const WEBHOOK_LOCKED = "Only an Admin can change this (it calls a webhook)";

export function RulesView() {
  const contactFields = useContactFields().data;
  const rules = useRules();
  const del = useDeleteRule();
  const reorder = useReorderRules();
  const toggle = useSaveRule();
  const access = useAutomationAccess();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [testing, setTesting] = useState<Rule | null>(null);
  const [replaying, setReplaying] = useState<Rule | null>(null);

  const move = (i: number, dir: -1 | 1) => {
    const ids = movedIds(
      (rules.data ?? []).map((r) => r.id),
      i,
      dir,
    );
    if (ids) reorder.mutate(ids);
  };

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Rules run by themselves, top to bottom, when a ticket is created, the customer replies,
          its SLA is breached or a snooze ends. Agent edits never run rules. Tickets show a grey
          “Rule applied” line. For steps with waits, try{" "}
          <Link href="/settings/workflows" className="font-medium text-primary-ink hover:underline">
            Workflows
          </Link>
          .
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add rule
        </Button>
      </div>

      {rules.isPending ? (
        <Skeleton className="h-32" />
      ) : rules.isError ? (
        <LoadError error={rules.error} />
      ) : rules.data.length === 0 ? (
        <Card>
          <EmptyState icon={<Workflow className="size-6" />} title="No rules yet">
            For example: when a new ticket mentions “refund”, set the team to Billing.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {rules.data.map((r, i) => {
              const locked = callsWebhook(r.actions as Item[]) && !access.manageAccess;
              return (
                <li key={r.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <ReorderButtons
                      name={r.name}
                      index={i}
                      count={rules.data.length}
                      disabled={reorder.isPending}
                      onMove={(d) => move(i, d)}
                    />
                    <div className="min-w-0 flex-1 basis-56">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{r.name}</span>
                        <Badge tone={r.enabled ? "success" : "neutral"}>
                          {r.enabled ? "On" : "Off"}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {EVENT_LABEL[r.event] ?? r.event} ·{" "}
                        {r.conditions
                          .map((c) =>
                            summarize(c as Item, AUTOMATION_CONDITION_KINDS, contactFields),
                          )
                          .join(" and ")}{" "}
                        →{" "}
                        {r.actions
                          .map((a) => summarize(a as Item, AUTOMATION_ACTION_KINDS))
                          .join(", ")}
                      </div>
                      {locked && (
                        <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                          <Lock className="size-3" aria-hidden /> {WEBHOOK_LOCKED}
                        </p>
                      )}
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--primary)]"
                        checked={r.enabled}
                        disabled={toggle.isPending || locked}
                        aria-label={`${r.name} enabled`}
                        onChange={(e) => toggle.mutate({ id: r.id, enabled: e.target.checked })}
                      />
                      Enabled
                    </label>
                    <Button size="sm" variant="outline" onClick={() => setEditing(r)}>
                      {locked ? (
                        "View"
                      ) : (
                        <>
                          <Pencil className="size-3.5" /> Edit
                        </>
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={del.isPending || locked}
                      onClick={() =>
                        ask({
                          title: "Delete this rule?",
                          message:
                            "It stops running on tickets straight away. This can't be undone.",
                          detail: r.name,
                          confirmLabel: "Delete",
                          run: () =>
                            del.mutate(r.id, { onSuccess: () => toast.success("Rule deleted") }),
                        })
                      }
                    >
                      <Trash2 className="size-3.5" /> Delete
                    </Button>
                    <Popover
                      className="w-52"
                      trigger={({ props }) => (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`More actions for ${r.name}`}
                          {...props}
                        >
                          <MoreHorizontal className="size-4" />
                        </Button>
                      )}
                    >
                      {(close) => (
                        <>
                          <MenuItem
                            onClick={() => {
                              close();
                              setTesting(r);
                            }}
                          >
                            <FlaskConical className="size-4" aria-hidden /> Test on a ticket
                          </MenuItem>
                          <MenuItem
                            onClick={() => {
                              close();
                              setReplaying(r);
                            }}
                          >
                            <History className="size-4" aria-hidden /> Replay on past tickets
                          </MenuItem>
                          <Link
                            role="menuitem"
                            href={`/settings/automation/log?rule_id=${r.id}`}
                            onClick={close}
                            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-foreground-2 hover:bg-surface-2"
                          >
                            <History className="size-4" aria-hidden /> Run log
                          </Link>
                        </>
                      )}
                    </Popover>
                  </div>
                  <Problems items={r.problems} />
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add rule" : "Edit rule"}
        className="max-w-2xl"
      >
        {editing !== null && (
          <RuleForm
            key={editing === "new" ? "new" : editing.id}
            rule={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>

      {testing && (
        <RuleTestDrawer
          open
          onClose={() => setTesting(null)}
          title={testing.name}
          ruleId={testing.id}
        />
      )}
      {replaying && (
        <ReplayDialog
          title={replaying.name}
          target={{ kind: "rule", ruleId: replaying.id }}
          onClose={() => setReplaying(null)}
        />
      )}
    </div>
  );
}

function RuleForm({ rule, onClose }: { rule: Rule | null; onClose: () => void }) {
  const save = useSaveRule();
  const access = useAutomationAccess();
  const [name, setName] = useState(rule?.name ?? "");
  const [event, setEvent] = useState<RuleEvent>(rule?.event ?? "created");
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [conditions, setConditions] = useState<Item[]>(
    (rule?.conditions as Item[] | undefined) ?? [],
  );
  const [actions, setActions] = useState<Item[]>((rule?.actions as Item[] | undefined) ?? []);
  const [perHour, setPerHour] = useState(rule?.max_runs_per_hour?.toString() ?? "");
  const [perDay, setPerDay] = useState(rule?.max_runs_per_day?.toString() ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [testOpen, setTestOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);

  // Someone without `manage_access` may look at a webhook rule but not change it.
  const readOnly = callsWebhook(actions) && !access.manageAccess;

  const limit = (text: string, min: number, max: number): number | undefined | "bad" => {
    if (text.trim() === "") return undefined;
    const n = Number(text);
    return Number.isInteger(n) && n >= min && n <= max ? n : "bad";
  };

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (readOnly) return;
        const next: Record<string, string> = {};
        if (!name.trim()) next.name = "Enter a name";
        if (conditions.length === 0) next.conditions = "Add at least one condition";
        if (actions.length === 0) next.actions = "Add at least one action";
        const hour = limit(perHour, 1, 100_000);
        const day = limit(perDay, 1, 1_000_000);
        if (hour === "bad") next.max_runs_per_hour = "Use a whole number from 1 to 100000";
        if (day === "bad") next.max_runs_per_day = "Use a whole number from 1 to 1000000";
        if (
          typeof hour !== "string" &&
          typeof day !== "string" &&
          (day ?? DEFAULT_PER_DAY) < (hour ?? DEFAULT_PER_HOUR)
        ) {
          next.max_runs_per_day = "The daily limit can't be lower than the hourly one";
        }
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            id: rule?.id,
            name: name.trim(),
            event,
            enabled,
            conditions,
            actions,
            ...(typeof hour === "number" ? { max_runs_per_hour: hour } : {}),
            ...(typeof day === "number" ? { max_runs_per_day: day } : {}),
          },
          {
            onSuccess: () => {
              toast.success("Rule saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      {readOnly && (
        <p
          role="note"
          className="flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground"
        >
          <Lock className="size-4 flex-none" aria-hidden /> {WEBHOOK_LOCKED}
        </p>
      )}
      <div inert={readOnly} className="space-y-4">
        <Field label="Name" htmlFor="r-name" error={errs.name}>
          <Input
            id="r-name"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="When" htmlFor="r-event" error={errs.event}>
          <Select
            id="r-event"
            value={event}
            options={EVENT_OPTIONS}
            onChange={(e) => {
              const next = e.target.value as RuleEvent;
              setEvent(next);
              // "SLA is …" makes no sense on a brand new ticket, so drop it.
              if (next === "created" && conditions.some((c) => c.kind === "sla_in")) {
                setConditions((cs) => cs.filter((c) => c.kind !== "sla_in"));
                toast.info("“SLA is” conditions were removed: a new ticket has no SLA state yet.");
              }
            }}
          />
          {event === "snooze_ended" && (
            <p className="mt-1 text-xs text-muted-foreground">
              Runs when the system reopens a snoozed ticket at its time, not when an agent unsnoozes
              it or the customer writes.
            </p>
          )}
        </Field>
        <ItemBuilder
          title="Conditions (all must match)"
          addLabel="Add condition"
          kinds={automationConditionKinds(event)}
          items={conditions}
          onChange={setConditions}
        />
        {errs.conditions && (
          <p role="alert" className="text-xs text-danger">
            {errs.conditions}
          </p>
        )}
        <ItemBuilder
          title="Actions (run in order)"
          addLabel="Add action"
          kinds={automationActionKinds(access.manageAccess)}
          items={actions}
          onChange={setActions}
        />
        {errs.actions && (
          <p role="alert" className="text-xs text-danger">
            {errs.actions}
          </p>
        )}
        <details className="rounded-lg border border-border px-3 py-2">
          <summary className="cursor-pointer text-[13px] font-medium text-foreground-2">
            Advanced
          </summary>
          <div className="mt-3 space-y-3">
            <p className="text-xs text-muted-foreground">
              Stops a rule that runs far more than you expect, for example after a mistake in its
              conditions.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="At most … runs an hour"
                htmlFor="r-hour"
                error={errs.max_runs_per_hour}
                hint={`Default ${DEFAULT_PER_HOUR}`}
              >
                <Input
                  id="r-hour"
                  type="number"
                  min={1}
                  max={100000}
                  value={perHour}
                  placeholder={String(DEFAULT_PER_HOUR)}
                  onChange={(e) => setPerHour(e.target.value)}
                />
              </Field>
              <Field
                label="… runs a day"
                htmlFor="r-day"
                error={errs.max_runs_per_day}
                hint={`Default ${DEFAULT_PER_DAY}. Not lower than the hourly limit.`}
              >
                <Input
                  id="r-day"
                  type="number"
                  min={1}
                  max={1000000}
                  value={perDay}
                  placeholder={String(DEFAULT_PER_DAY)}
                  onChange={(e) => setPerDay(e.target.value)}
                />
              </Field>
            </div>
          </div>
        </details>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4 accent-[var(--primary)]"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Enabled
        </label>
      </div>
      <FormError message={errs._form} />
      <div className="flex flex-wrap justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setTestOpen(true)}>
            <FlaskConical className="size-4" /> Test on a ticket
          </Button>
          {access.settings && (
            <Button variant="outline" onClick={() => setReplayOpen(true)}>
              <History className="size-4" /> Replay on past tickets
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>
            {readOnly ? "Close" : "Cancel"}
          </Button>
          {!readOnly && (
            <Button type="submit" loading={save.isPending}>
              Save rule
            </Button>
          )}
        </div>
      </div>
      {replayOpen && (
        <ReplayDialog
          title={name.trim() || "Draft rule"}
          target={{
            kind: "draft",
            draft: { name: name.trim() || "Draft", event, conditions, actions },
          }}
          onClose={() => setReplayOpen(false)}
        />
      )}
      {testOpen && (
        <RuleTestDrawer
          open
          onClose={() => setTestOpen(false)}
          title={name.trim() || "Draft rule"}
          draft={{ name: name.trim() || "Draft", event, conditions, actions }}
        />
      )}
    </form>
  );
}
