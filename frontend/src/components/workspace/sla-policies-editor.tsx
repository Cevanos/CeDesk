"use client";

import { GripVertical, Lock, Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { PRIORITIES, PRIORITY_LABEL } from "@/components/tickets/badges";
import { formatMinutes } from "@/components/tickets/sla";
import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Card, Field, Input } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import {
  FormError,
  fieldErrors,
  movedIds,
  ReorderButtons,
} from "@/components/workspace/list-shared";
import {
  useResetSlaPolicies,
  useSaveSlaPolicies,
  type SlaPolicyInput,
} from "@/hooks/use-workspace";
import type { SlaPolicy } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const CHANNELS = ["email", "web", "portal", "api", "chat", "phone"];
const cap = (v: string) => v[0]!.toUpperCase() + v.slice(1);

/** A policy being edited. `key` is local only (the server identifies policies by position). */
export interface PolicyDraft {
  key: string;
  name: string;
  enabled: boolean;
  vip_only: boolean;
  channels: string[];
  priorities: string[];
  /** Minutes as typed; empty = use the priority targets. */
  first: string;
  resolve: string;
}

export function toDraft(p: SlaPolicy, key: string): PolicyDraft {
  return {
    key,
    name: p.name,
    enabled: p.enabled,
    vip_only: p.vip_only,
    channels: [...p.channels],
    priorities: [...p.priorities],
    first: p.first_reply_minutes == null ? "" : String(p.first_reply_minutes),
    resolve: p.resolve_minutes == null ? "" : String(p.resolve_minutes),
  };
}

const minutes = (v: string) => (v.trim() === "" ? null : Number(v));

export function toInput(d: PolicyDraft): SlaPolicyInput {
  return {
    name: d.name.trim(),
    enabled: d.enabled,
    vip_only: d.vip_only,
    channels: d.channels,
    priorities: d.priorities,
    first_reply_minutes: minutes(d.first),
    resolve_minutes: minutes(d.resolve),
  };
}

/** Move the policy at `from` to `to`. The last row (the catch-all) never moves and nothing moves past it. */
export function reorderPolicies<T>(rows: T[], from: number, to: number): T[] {
  const last = rows.length - 1;
  if (from === to || from < 0 || to < 0 || from >= last || to >= last) return rows;
  const next = [...rows];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item!);
  return next;
}

/** Validation message for a draft's targets, or undefined. */
export function targetError(d: Pick<PolicyDraft, "first" | "resolve">) {
  const first = minutes(d.first);
  const resolve = minutes(d.resolve);
  for (const v of [first, resolve]) {
    if (v !== null && (!Number.isInteger(v) || v < 1)) return "Use whole minutes, 1 or more.";
  }
  if (first !== null && resolve !== null && first > resolve)
    return "First reply can't be later than resolution.";
  return undefined;
}

let seq = 0;
const nextKey = () => `p${++seq}`;

export function SlaPoliciesEditor({
  policies,
  isDefault,
  canEdit,
}: {
  policies: SlaPolicy[];
  isDefault: boolean;
  canEdit: boolean;
}) {
  const save = useSaveSlaPolicies();
  const reset = useResetSlaPolicies();
  const { ask, dialog } = useConfirm();
  const [rows, setRows] = useState<PolicyDraft[]>(() => policies.map((p) => toDraft(p, nextKey())));
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState<PolicyDraft | "new" | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [error, setError] = useState<string>();

  const lastIndex = rows.length - 1;
  const change = (next: PolicyDraft[]) => {
    setRows(next);
    setDirty(true);
  };
  const move = (from: number, to: number) => change(reorderPolicies(rows, from, to));

  const submit = () => {
    setError(undefined);
    save.mutate(rows.map(toInput), {
      onSuccess: () => {
        setDirty(false);
        toast.success("SLA policies saved");
      },
      onError: (e) => setError(fieldErrors(e)._form),
    });
  };

  return (
    <section aria-labelledby="sla-policies">
      {dialog}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="sla-policies" className="text-base font-semibold">
            Policies
          </h2>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            The first matching policy applies, from top to bottom. The last one catches every ticket
            the others don&apos;t, so it stays at the bottom.
            {isDefault && " (Using the built-in defaults.)"}
            {!canEdit && " Only an Admin can change them."}
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              loading={reset.isPending}
              disabled={isDefault && !dirty}
              onClick={() =>
                ask({
                  title: "Reset to built-in policies?",
                  message: "Your own policies are lost.",
                  confirmLabel: "Reset",
                  tone: "warning",
                  run: () =>
                    reset.mutate(undefined, {
                      onSuccess: () => toast.success("Policies reset to built-in"),
                    }),
                })
              }
            >
              <RotateCcw className="size-3.5" /> Reset to built-in
            </Button>
            <Button size="sm" onClick={() => setEditing("new")}>
              <Plus className="size-3.5" /> Add policy
            </Button>
          </div>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No policies.</p>
      ) : (
        <Card className="mt-3 overflow-hidden">
          <ul className="divide-y divide-border">
            {rows.map((p, i) => {
              const locked = i === lastIndex;
              const dropTarget = dragFrom !== null && dragOver === i && dragFrom !== i && !locked;
              return (
                <li
                  key={p.key}
                  onDragOver={(e) => {
                    if (dragFrom === null || locked) return;
                    e.preventDefault();
                    setDragOver(i);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragFrom !== null) move(dragFrom, i);
                    setDragFrom(null);
                    setDragOver(null);
                  }}
                  className={cn(
                    "flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 transition-colors sm:px-4",
                    dragFrom === i && "opacity-50",
                    dropTarget && "bg-primary-soft/60 shadow-[inset_0_2px_0_var(--primary)]",
                    locked && "bg-surface-2/50",
                  )}
                >
                  {canEdit && (
                    <div className="flex items-center gap-1">
                      {locked ? (
                        <span
                          className="grid size-8 place-items-center text-subtle-foreground"
                          title="The last policy is the fallback and always stays last."
                        >
                          <Lock className="size-4" aria-label="Locked: fallback policy" />
                        </span>
                      ) : (
                        <>
                          <span
                            draggable
                            onDragStart={(e) => {
                              e.dataTransfer.effectAllowed = "move";
                              setDragFrom(i);
                            }}
                            onDragEnd={() => {
                              setDragFrom(null);
                              setDragOver(null);
                            }}
                            aria-hidden
                            className="grid size-8 cursor-grab touch-none place-items-center rounded-md text-subtle-foreground hover:bg-surface-2 active:cursor-grabbing"
                          >
                            <GripVertical className="size-4" />
                          </span>
                          <ReorderButtons
                            name={p.name}
                            index={i}
                            count={lastIndex}
                            onMove={(d) => {
                              const ids = movedIds(
                                rows.map((r) => r.key),
                                i,
                                d,
                              );
                              if (ids) move(i, i + d);
                            }}
                          />
                        </>
                      )}
                    </div>
                  )}
                  <div className="min-w-0 flex-1 basis-56">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{p.name}</span>
                      {locked && <Badge tone="primary">Fallback</Badge>}
                      {p.vip_only && <Badge tone="warning">VIP only</Badge>}
                      {!p.enabled && <Badge>Off</Badge>}
                    </div>
                    <div className="text-xs text-muted-foreground">{summary(p)}</div>
                  </div>
                  <Switch
                    checked={p.enabled}
                    label={`${p.name} on`}
                    disabled={!canEdit || locked}
                    onChange={(v) =>
                      change(rows.map((r) => (r.key === p.key ? { ...r, enabled: v } : r)))
                    }
                  />
                  {canEdit && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => setEditing(p)}>
                        <Pencil className="size-3.5" /> Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={locked}
                        aria-label={`Delete ${p.name}`}
                        onClick={() =>
                          ask({
                            title: "Delete this policy?",
                            message: "It's removed from the list until you save your changes.",
                            detail: p.name,
                            confirmLabel: "Delete",
                            run: () => change(rows.filter((r) => r.key !== p.key)),
                          })
                        }
                      >
                        <Trash2 className="size-3.5" /> Delete
                      </Button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      {error && (
        <div className="mt-3">
          <FormError message={error} />
        </div>
      )}
      {canEdit && dirty && (
        <div className="mt-3 flex items-center justify-end gap-3">
          <span className="text-xs text-muted-foreground">You have unsaved changes.</span>
          <Button
            variant="ghost"
            onClick={() => {
              setRows(policies.map((p) => toDraft(p, nextKey())));
              setDirty(false);
              setError(undefined);
            }}
          >
            Discard
          </Button>
          <Button onClick={submit} loading={save.isPending}>
            Save policies
          </Button>
        </div>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add policy" : "Edit policy"}
        className="sm:max-w-xl"
      >
        {editing !== null && (
          <PolicyForm
            key={editing === "new" ? "new" : editing.key}
            draft={editing === "new" ? null : editing}
            locked={editing !== "new" && rows.at(-1)?.key === editing.key}
            onCancel={() => setEditing(null)}
            onSave={(d) => {
              if (editing === "new") {
                // New policies go above the fallback.
                const at = Math.max(rows.length - 1, 0);
                change([...rows.slice(0, at), d, ...rows.slice(at)]);
              } else {
                change(rows.map((r) => (r.key === d.key ? d : r)));
              }
              setEditing(null);
            }}
          />
        )}
      </Dialog>
    </section>
  );
}

function summary(p: PolicyDraft) {
  const bits = [
    p.channels.length ? p.channels.join(", ") : "All channels",
    p.priorities.length ? p.priorities.join(", ") : "All priorities",
  ];
  const first = minutes(p.first);
  const resolve = minutes(p.resolve);
  if (first === null && resolve === null) bits.push("uses the priority targets");
  if (first !== null && Number.isFinite(first)) bits.push(`reply ${formatMinutes(first)}`);
  if (resolve !== null && Number.isFinite(resolve)) bits.push(`resolve ${formatMinutes(resolve)}`);
  return bits.join(" · ");
}

function CheckGroup({
  legend,
  hint,
  options,
  value,
  onChange,
}: {
  legend: string;
  hint: string;
  options: { value: string; label: string }[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <fieldset>
      <legend className="text-[13px] font-medium text-foreground-2">{legend}</legend>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1.5">
        {options.map((o) => (
          <label key={o.value} className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              checked={value.includes(o.value)}
              onChange={(e) =>
                onChange(
                  e.target.checked ? [...value, o.value] : value.filter((v) => v !== o.value),
                )
              }
            />
            {o.label}
          </label>
        ))}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </fieldset>
  );
}

function PolicyForm({
  draft,
  locked,
  onSave,
  onCancel,
}: {
  draft: PolicyDraft | null;
  locked: boolean;
  onSave: (d: PolicyDraft) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [d, setD] = useState<PolicyDraft>(
    draft ?? {
      key: nextKey(),
      name: "",
      enabled: true,
      vip_only: false,
      channels: [],
      priorities: [],
      first: "",
      resolve: "",
    },
  );
  const [errs, setErrs] = useState<Record<string, string>>({});
  const set = (p: Partial<PolicyDraft>) => setD((x) => ({ ...x, ...p }));

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!d.name.trim()) next.name = "Enter a name";
        const t = targetError(d);
        if (t) next.targets = t;
        setErrs(next);
        if (Object.keys(next).length === 0) onSave({ ...d, name: d.name.trim() });
      }}
    >
      <Field label="Name" htmlFor={`${id}-name`} error={errs.name}>
        <Input
          id={`${id}-name`}
          maxLength={80}
          value={d.name}
          onChange={(e) => set({ name: e.target.value })}
        />
      </Field>

      {locked ? (
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
          This is the fallback policy. It matches every ticket the ones above don&apos;t, so its
          conditions and on/off switch can&apos;t be changed.
        </p>
      ) : (
        <>
          <CheckGroup
            legend="Channels"
            hint="Leave all unticked to match every channel."
            options={CHANNELS.map((c) => ({ value: c, label: cap(c) }))}
            value={d.channels}
            onChange={(channels) => set({ channels })}
          />
          <CheckGroup
            legend="Priorities"
            hint="Leave all unticked to match every priority."
            options={PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABEL[p] }))}
            value={d.priorities}
            onChange={(priorities) => set({ priorities })}
          />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              checked={d.vip_only}
              onChange={(e) => set({ vip_only: e.target.checked })}
            />
            Only for VIP customers
          </label>
        </>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {(["first", "resolve"] as const).map((k) => {
          const n = minutes(d[k]);
          return (
            <Field
              key={k}
              label={k === "first" ? "First reply (minutes)" : "Resolution (minutes)"}
              htmlFor={`${id}-${k}`}
              hint={
                n !== null && Number.isFinite(n) && n > 0
                  ? formatMinutes(n)
                  : "Empty = use the priority targets"
              }
            >
              <Input
                id={`${id}-${k}`}
                type="number"
                min={1}
                inputMode="numeric"
                value={d[k]}
                aria-invalid={!!errs.targets}
                onChange={(e) => set({ [k]: e.target.value })}
              />
            </Field>
          );
        })}
      </div>
      {errs.targets && (
        <p role="alert" className="text-xs text-danger">
          {errs.targets}
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
