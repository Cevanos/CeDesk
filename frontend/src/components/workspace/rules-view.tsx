"use client";

import { Pencil, Plus, Trash2, Workflow } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
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
import {
  CONDITION_KINDS,
  conditionKindsFor,
  ItemBuilder,
  RULE_ACTION_KINDS,
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
import { useDeleteRule, useReorderRules, useRules, useSaveRule } from "@/hooks/use-workspace";
import type { Rule, RuleEvent } from "@/lib/api/types";

export const EVENT_LABEL: Record<RuleEvent, string> = {
  created: "When a ticket is created",
  customer_replied: "When the customer replies",
  sla_breached: "When the SLA is breached",
};
const EVENT_OPTIONS = (Object.keys(EVENT_LABEL) as RuleEvent[]).map((v) => ({
  value: v,
  label: EVENT_LABEL[v],
}));

export function RulesView() {
  const rules = useRules();
  const del = useDeleteRule();
  const reorder = useReorderRules();
  const toggle = useSaveRule();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<Rule | "new" | null>(null);

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
          Rules run by themselves, top to bottom, when a ticket is created, the customer replies or
          its SLA is breached. Agent edits never run rules. Tickets show a grey “Rule applied” line.
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
            {rules.data.map((r, i) => (
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
                      {r.conditions.map((c) => summarize(c as Item, CONDITION_KINDS)).join(" and ")}{" "}
                      → {r.actions.map((a) => summarize(a as Item, RULE_ACTION_KINDS)).join(", ")}
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-[var(--primary)]"
                      checked={r.enabled}
                      disabled={toggle.isPending}
                      aria-label={`${r.name} enabled`}
                      onChange={(e) => toggle.mutate({ id: r.id, enabled: e.target.checked })}
                    />
                    Enabled
                  </label>
                  <Button size="sm" variant="outline" onClick={() => setEditing(r)}>
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={del.isPending}
                    onClick={() =>
                      ask({
                        title: "Delete this rule?",
                        message: "It stops running on tickets straight away. This can't be undone.",
                        detail: r.name,
                        confirmLabel: "Delete",
                        run: () =>
                          del.mutate(r.id, { onSuccess: () => toast.success("Rule deleted") }),
                      })
                    }
                  >
                    <Trash2 className="size-3.5" /> Delete
                  </Button>
                </div>
                <Problems items={r.problems} />
              </li>
            ))}
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
    </div>
  );
}

function RuleForm({ rule, onClose }: { rule: Rule | null; onClose: () => void }) {
  const save = useSaveRule();
  const [name, setName] = useState(rule?.name ?? "");
  const [event, setEvent] = useState<RuleEvent>(rule?.event ?? "created");
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [conditions, setConditions] = useState<Item[]>(
    (rule?.conditions as Item[] | undefined) ?? [],
  );
  const [actions, setActions] = useState<Item[]>((rule?.actions as Item[] | undefined) ?? []);
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!name.trim()) next.name = "Enter a name";
        if (conditions.length === 0) next.conditions = "Add at least one condition";
        if (actions.length === 0) next.actions = "Add at least one action";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: rule?.id, name: name.trim(), event, enabled, conditions, actions },
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
      <Field label="Name" htmlFor="r-name" error={errs.name}>
        <Input id="r-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
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
      </Field>
      <ItemBuilder
        title="Conditions (all must match)"
        addLabel="Add condition"
        kinds={conditionKindsFor(event)}
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
        kinds={RULE_ACTION_KINDS}
        items={actions}
        onChange={setActions}
      />
      {errs.actions && (
        <p role="alert" className="text-xs text-danger">
          {errs.actions}
        </p>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Enabled
      </label>
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save rule
        </Button>
      </div>
    </form>
  );
}
