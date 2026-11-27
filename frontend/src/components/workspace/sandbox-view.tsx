"use client";

import { ArrowDown, ArrowUp, FlaskConical, Pencil, Plus, Rocket, Trash2 } from "lucide-react";
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
  Textarea,
} from "@/components/ui/primitives";
import {
  CONDITION_KINDS,
  conditionKindsFor,
  ItemBuilder,
  MACRO_STEP_KINDS,
  RULE_ACTION_KINDS,
  summarize,
  type Item,
} from "@/components/workspace/item-builder";
import { FormError, LoadError } from "@/components/workspace/list-shared";
import { CompareDialog, TryDialog } from "@/components/workspace/sandbox-dialogs";
import { msg, msg422 } from "@/components/workspace/sandbox-errors";
import {
  useCreateSandbox,
  useDeleteSandbox,
  useSandbox,
  useSaveSandboxList,
  type Sandbox,
  type SandboxKind,
  type SandboxMacro,
  type SandboxReply,
  type SandboxRule,
} from "@/hooks/use-workspace";
import type { RuleEvent } from "@/lib/api/types";
import { bind, fieldError, useFormik } from "@/lib/forms";
import { cn } from "@/lib/utils";

import { EVENT_LABEL } from "./rules-view";

export function SandboxView() {
  const q = useSandbox();
  const make = useCreateSandbox();
  const [error, setError] = useState<string | null>(null);

  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <LoadError error={q.error} />;
  if (q.data === null) {
    return (
      <Card>
        <EmptyState icon={<FlaskConical className="size-6" />} title="No sandbox yet">
          A sandbox is a private copy of your rules, macros and saved replies. Change them here, try
          them on sample tickets, then deploy to live when you are happy.
        </EmptyState>
        <div className="space-y-3 px-4 pb-6 text-center">
          <Button
            loading={make.isPending}
            onClick={() => {
              setError(null);
              make.mutate(undefined, { onError: (e) => setError(msg(e)) });
            }}
          >
            Make a sandbox
          </Button>
          <FormError message={error ?? undefined} />
        </div>
      </Card>
    );
  }
  return <SandboxEditor sandbox={q.data} />;
}

const TABS: { kind: SandboxKind; label: string }[] = [
  { kind: "rules", label: "Rules" },
  { kind: "macros", label: "Macros" },
  { kind: "saved_replies", label: "Saved replies" },
];

function SandboxEditor({ sandbox }: { sandbox: Sandbox }) {
  const del = useDeleteSandbox();
  const { ask, dialog } = useConfirm();
  const [tab, setTab] = useState<SandboxKind>("rules");
  const [showTry, setShowTry] = useState(false);
  const [showCompare, setShowCompare] = useState(false);

  return (
    <div className="space-y-4">
      {dialog}
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-0 flex-1 basis-60">
          <div className="flex items-center gap-2">
            <h2 className="text-[15px] font-semibold">Sandbox</h2>
            <Badge tone="info">Not live</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Last changed{sandbox.updated_by_name ? ` by ${sandbox.updated_by_name}` : ""},{" "}
            {new Date(sandbox.updated_at).toLocaleString()}
          </p>
        </div>
        <Button variant="outline" onClick={() => setShowTry(true)}>
          <FlaskConical className="size-4" /> Try on samples
        </Button>
        <Button variant="outline" onClick={() => setShowCompare(true)}>
          Compare with live
        </Button>
        <Button onClick={() => setShowCompare(true)}>
          <Rocket className="size-4" /> Deploy to live…
        </Button>
        <Button
          variant="ghost"
          loading={del.isPending}
          onClick={() =>
            ask({
              title: "Delete the sandbox?",
              message: "Your live rules, macros and saved replies are not touched.",
              confirmLabel: "Delete sandbox",
              run: () =>
                del.mutate(undefined, { onSuccess: () => toast.success("Sandbox deleted") }),
            })
          }
        >
          <Trash2 className="size-4" /> Delete sandbox
        </Button>
      </Card>

      <div
        role="tablist"
        aria-label="Sandbox content"
        className="flex gap-1 border-b border-border"
      >
        {TABS.map((t) => (
          <button
            key={t.kind}
            role="tab"
            type="button"
            id={`sb-tab-${t.kind}`}
            aria-selected={tab === t.kind}
            aria-controls={`sb-panel-${t.kind}`}
            onClick={() => setTab(t.kind)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              tab === t.kind
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
            <span className="ml-1.5 text-xs text-muted-foreground">{sandbox[t.kind].length}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`sb-panel-${tab}`} aria-labelledby={`sb-tab-${tab}`}>
        {/* Keyed by the save time so a fresh server copy replaces the draft. */}
        {tab === "rules" && <RulesTab key={sandbox.updated_at} items={sandbox.rules} />}
        {tab === "macros" && <MacrosTab key={sandbox.updated_at} items={sandbox.macros} />}
        {tab === "saved_replies" && (
          <RepliesTab key={sandbox.updated_at} items={sandbox.saved_replies} />
        )}
      </div>

      <TryDialog open={showTry} onClose={() => setShowTry(false)} />
      <CompareDialog open={showCompare} onClose={() => setShowCompare(false)} />
    </div>
  );
}

/* ---------- whole-list editor ---------- */

function ListEditor<T extends { id?: string; problems?: string[] }>({
  kind,
  items,
  noun,
  empty,
  summary,
  form,
}: {
  kind: SandboxKind;
  items: T[];
  noun: string;
  empty: string;
  summary: (item: T) => React.ReactNode;
  form: (p: { item: T | null; onSave: (item: T) => void; onCancel: () => void }) => React.ReactNode;
}) {
  const save = useSaveSandboxList();
  const { ask, dialog } = useConfirm();
  const [draft, setDraft] = useState<T[]>(items);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(items);

  const move = (i: number, dir: -1 | 1) =>
    setDraft((d) => {
      const n = [...d];
      const a = n[i];
      const b = n[i + dir];
      if (a === undefined || b === undefined) return d;
      n[i] = b;
      n[i + dir] = a;
      return n;
    });

  return (
    <div>
      {dialog}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Edit the whole list, then save it to the sandbox. Nothing here is live.
        </p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setEditing("new")}>
            <Plus className="size-4" /> Add {noun}
          </Button>
          <Button
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => {
              setError(null);
              save.mutate(
                { kind, items: draft },
                {
                  onSuccess: () => toast.success("Saved to the sandbox"),
                  onError: (e) => setError(msg422(e)),
                },
              );
            }}
          >
            Save {dirty ? "changes" : ""}
          </Button>
        </div>
      </div>
      <FormError message={error ?? undefined} />

      {draft.length === 0 ? (
        <Card>
          <EmptyState title={empty} />
        </Card>
      ) : (
        <Card className="mt-3 overflow-hidden">
          <ul className="divide-y divide-border">
            {draft.map((it, i) => (
              <li key={it.id ?? `new-${i}`} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex flex-col">
                    <button
                      type="button"
                      aria-label={`Move ${noun} ${i + 1} up`}
                      disabled={i === 0}
                      onClick={() => move(i, -1)}
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowUp className="size-4" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${noun} ${i + 1} down`}
                      disabled={i === draft.length - 1}
                      onClick={() => move(i, 1)}
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowDown className="size-4" />
                    </button>
                  </div>
                  <div className="min-w-0 flex-1 basis-56">{summary(it)}</div>
                  {!it.id && <Badge tone="info">New</Badge>}
                  <Button size="sm" variant="outline" onClick={() => setEditing(i)}>
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      ask({
                        title: `Remove this ${noun}?`,
                        message: "It's removed from the list until you save your changes.",
                        confirmLabel: "Remove",
                        run: () => setDraft((d) => d.filter((_, j) => j !== i)),
                      })
                    }
                  >
                    <Trash2 className="size-3.5" /> Remove
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? `Add ${noun}` : `Edit ${noun}`}
        className="max-w-2xl"
      >
        {editing !== null &&
          form({
            item: editing === "new" ? null : (draft[editing] ?? null),
            onCancel: () => setEditing(null),
            onSave: (item) => {
              setDraft((d) =>
                editing === "new" ? [...d, item] : d.map((x, j) => (j === editing ? item : x)),
              );
              setEditing(null);
            },
          })}
      </Dialog>
    </div>
  );
}

/* ---------- rules ---------- */

const EVENT_OPTIONS = (Object.keys(EVENT_LABEL) as RuleEvent[]).map((v) => ({
  value: v,
  label: EVENT_LABEL[v],
}));

function RulesTab({ items }: { items: SandboxRule[] }) {
  return (
    <ListEditor<SandboxRule>
      kind="rules"
      items={items}
      noun="rule"
      empty="No rules in the sandbox"
      summary={(r) => (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{r.name}</span>
            <Badge tone={r.enabled ? "success" : "neutral"}>{r.enabled ? "On" : "Off"}</Badge>
          </div>
          <div className="text-xs text-muted-foreground">
            {EVENT_LABEL[r.event as RuleEvent] ?? r.event} ·{" "}
            {r.conditions.map((c) => summarize(c as Item, CONDITION_KINDS)).join(" and ")} →{" "}
            {r.actions.map((a) => summarize(a as Item, RULE_ACTION_KINDS)).join(", ")}
          </div>
        </>
      )}
      form={(p) => <RuleDraftForm {...p} />}
    />
  );
}

function RuleDraftForm({
  item,
  onSave,
  onCancel,
}: {
  item: SandboxRule | null;
  onSave: (r: SandboxRule) => void;
  onCancel: () => void;
}) {
  const form = useFormik({
    initialValues: {
      name: item?.name ?? "",
      event: ((item?.event as RuleEvent) ?? "created") as RuleEvent,
      enabled: item?.enabled ?? true,
      conditions: ((item?.conditions as Item[]) ?? []) as Item[],
      actions: ((item?.actions as Item[]) ?? []) as Item[],
    },
    validate: (v) => {
      const next: Record<string, string> = {};
      if (!v.name.trim()) next.name = "Enter a name";
      if (v.conditions.length === 0) next.conditions = "Add at least one condition";
      if (v.actions.length === 0) next.actions = "Add at least one action";
      return next;
    },
    onSubmit: (v) =>
      onSave({
        ...(item?.id ? { id: item.id } : {}),
        name: v.name.trim(),
        event: v.event,
        enabled: v.enabled,
        conditions: v.conditions,
        actions: v.actions,
      }),
  });
  const { event, enabled, conditions, actions } = form.values;
  const setConditions = (next: Item[] | ((cs: Item[]) => Item[])) =>
    form.setFieldValue(
      "conditions",
      typeof next === "function" ? next(form.values.conditions) : next,
    );
  const setActions = (next: Item[] | ((cs: Item[]) => Item[])) =>
    form.setFieldValue("actions", typeof next === "function" ? next(form.values.actions) : next);
  const errs = (k: string) => fieldError(form, k);
  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field label="Name" htmlFor="sb-r-name" error={errs("name")}>
        <Input id="sb-r-name" maxLength={80} {...bind(form, "name")} />
      </Field>
      <Field label="When" htmlFor="sb-r-event">
        <Select
          id="sb-r-event"
          value={event}
          options={EVENT_OPTIONS}
          onChange={(e) => {
            const next = e.target.value as RuleEvent;
            form.setFieldValue("event", next);
            if (next === "created") setConditions((cs) => cs.filter((c) => c.kind !== "sla_in"));
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
      {errs("conditions") && (
        <p role="alert" className="text-xs text-danger">
          {errs("conditions")}
        </p>
      )}
      <ItemBuilder
        title="Actions (run in order)"
        addLabel="Add action"
        kinds={RULE_ACTION_KINDS}
        items={actions}
        onChange={setActions}
      />
      {errs("actions") && (
        <p role="alert" className="text-xs text-danger">
          {errs("actions")}
        </p>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          name="enabled"
          checked={enabled}
          onChange={form.handleChange}
        />
        Enabled
      </label>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Done</Button>
      </div>
    </form>
  );
}

/* ---------- macros ---------- */

function MacrosTab({ items }: { items: SandboxMacro[] }) {
  return (
    <ListEditor<SandboxMacro>
      kind="macros"
      items={items}
      noun="macro"
      empty="No macros in the sandbox"
      summary={(m) => (
        <>
          <span className="font-medium">{m.name}</span>
          <div className="text-xs text-muted-foreground">
            {m.steps.map((s) => summarize(s as Item, MACRO_STEP_KINDS)).join(", ")}
          </div>
        </>
      )}
      form={(p) => <MacroDraftForm {...p} />}
    />
  );
}

function MacroDraftForm({
  item,
  onSave,
  onCancel,
}: {
  item: SandboxMacro | null;
  onSave: (m: SandboxMacro) => void;
  onCancel: () => void;
}) {
  const form = useFormik({
    initialValues: { name: item?.name ?? "", steps: ((item?.steps as Item[]) ?? []) as Item[] },
    validate: (v) => {
      const next: Record<string, string> = {};
      if (!v.name.trim()) next.name = "Enter a name";
      if (v.steps.length === 0) next.steps = "Add at least one step";
      return next;
    },
    onSubmit: (v) =>
      onSave({ ...(item?.id ? { id: item.id } : {}), name: v.name.trim(), steps: v.steps }),
  });
  const errs = (k: string) => fieldError(form, k);
  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field label="Name" htmlFor="sb-m-name" error={errs("name")}>
        <Input id="sb-m-name" maxLength={80} {...bind(form, "name")} />
      </Field>
      <ItemBuilder
        title="Steps"
        addLabel="Add step"
        kinds={MACRO_STEP_KINDS}
        items={form.values.steps}
        onChange={(steps) => form.setFieldValue("steps", steps)}
      />
      {errs("steps") && (
        <p role="alert" className="text-xs text-danger">
          {errs("steps")}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Done</Button>
      </div>
    </form>
  );
}

/* ---------- saved replies ---------- */

function RepliesTab({ items }: { items: SandboxReply[] }) {
  return (
    <ListEditor<SandboxReply>
      kind="saved_replies"
      items={items}
      noun="saved reply"
      empty="No saved replies in the sandbox"
      summary={(r) => (
        <>
          <div className="font-mono text-sm font-semibold">/{r.shortcut}</div>
          <p className="line-clamp-2 text-sm whitespace-pre-wrap text-muted-foreground">{r.body}</p>
        </>
      )}
      form={(p) => <ReplyDraftForm {...p} />}
    />
  );
}

function ReplyDraftForm({
  item,
  onSave,
  onCancel,
}: {
  item: SandboxReply | null;
  onSave: (r: SandboxReply) => void;
  onCancel: () => void;
}) {
  const form = useFormik({
    initialValues: { shortcut: item?.shortcut ?? "", body: item?.body ?? "" },
    validate: (v) => {
      const sc = v.shortcut.trim().replace(/^\/+/, "");
      const next: Record<string, string> = {};
      if (!sc) next.shortcut = "Enter a shortcut";
      else if (/\s/.test(sc)) next.shortcut = "No spaces in a shortcut";
      if (!v.body.trim()) next.body = "Enter the reply text";
      return next;
    },
    onSubmit: (v) =>
      onSave({
        ...(item?.id ? { id: item.id } : {}),
        shortcut: v.shortcut.trim().replace(/^\/+/, ""),
        body: v.body,
      }),
  });
  const errs = (k: string) => fieldError(form, k);
  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
      <Field label="Shortcut" htmlFor="sb-sr-sc" error={errs("shortcut")}>
        <Input
          id="sb-sr-sc"
          {...bind(form, "shortcut")}
          maxLength={33}
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      <Field label="Reply" htmlFor="sb-sr-body" error={errs("body")}>
        <Textarea id="sb-sr-body" rows={7} {...bind(form, "body")} maxLength={10000} />
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Done</Button>
      </div>
    </form>
  );
}

/* ---------- try on samples ---------- */
