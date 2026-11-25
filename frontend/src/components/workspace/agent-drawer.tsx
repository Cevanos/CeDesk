"use client";

import { Gauge, Layers, Plus, ShieldCheck, Sparkles, Users, X } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";

import { STATUS_META } from "@/components/layout/user-menu";
import { Badge, Button, Input } from "@/components/ui/primitives";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { useRoutingDefaults, useTeams, useUpdateAgentRouting } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { AgentRow, AgentStatus, ChannelGroup } from "@/lib/api/types";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { ROLE_LABEL } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { Avatar, CHANNELS, LoadBar, totalCapacity } from "./agent-shared";

export function AgentDrawer({
  agent,
  canEdit,
  onClose,
}: {
  agent: AgentRow | null;
  canEdit: boolean;
  onClose: () => void;
}) {
  const open = !!agent;
  const ref = React.useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open);

  React.useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", key);
    };
  }, [open, onClose]);

  if (!agent || typeof document === "undefined") return null;

  return createPortal(
    <div
      role="presentation"
      className="fixed inset-0 z-[60] flex justify-end bg-[#0b1226]/55 backdrop-blur-sm"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <aside
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`${agent.name} details`}
        className="flex h-full w-full animate-[slide-in_0.35s_cubic-bezier(0.2,0.7,0.2,1)_both] flex-col overflow-y-auto bg-background shadow-hero outline-none sm:max-w-[30rem] sm:rounded-l-2xl"
      >
        <DrawerBody agent={agent} canEdit={canEdit} onClose={onClose} />
      </aside>
    </div>,
    document.body,
  );
}

function SectionTitle({
  icon: Icon,
  children,
  aside,
}: {
  icon: React.ElementType;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
        <Icon className="size-3.5" /> {children}
      </h3>
      {aside}
    </div>
  );
}

function DrawerBody({
  agent: a,
  canEdit,
  onClose,
}: {
  agent: AgentRow;
  canEdit: boolean;
  onClose: () => void;
}) {
  const update = useUpdateAgentRouting();
  const defaultsQ = useRoutingDefaults();
  const teams = useTeams();
  const skillForm = useFormik({
    initialValues: { skill: "" },
    validationSchema: yup.object({
      skill: yup.string().trim().max(40, "Use at most 40 characters"),
    }),
    onSubmit: (v, h) => {
      const val = v.skill.trim().toLowerCase();
      h.resetForm();
      if (!val || a.skills.includes(val)) return;
      patch({ skills: [...a.skills, val] }, "Skill added");
    },
  });
  const skill = skillForm.values.skill;
  const s = a.status ? (STATUS_META[a.status] ?? STATUS_META.offline) : null;
  const cap = totalCapacity(a, defaultsQ.data);
  const memberTeams = (teams.data ?? []).filter((t) => t.member_ids?.includes(a.member_id));

  const patch = (
    body: { status?: AgentStatus; channels?: ChannelGroup[]; skills?: string[] },
    msg = "Saved",
  ) =>
    update.mutate(
      { id: a.member_id, ...body },
      {
        onSuccess: () => toast.success(msg),
        onError: (e) =>
          toast.error(e instanceof ApiError ? e.userMessage : "Couldn't save changes"),
      },
    );

  const toggleChannel = (c: ChannelGroup) => {
    const has = a.channels.includes(c);
    if (has && a.channels.length === 1) {
      toast.error("An agent needs at least one channel");
      return;
    }
    patch(
      { channels: has ? a.channels.filter((x) => x !== c) : [...a.channels, c] },
      "Channels updated",
    );
  };

  return (
    <>
      <div className="relative overflow-hidden bg-nav px-6 pt-6 pb-14 text-white">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-16 -right-10 size-64 animate-drift rounded-full bg-primary/40 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-24 -left-10 size-56 rounded-full bg-info/25 blur-3xl"
        />
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="relative ml-auto grid size-8 place-items-center rounded-lg bg-white/10 text-white/80 transition-colors hover:bg-white/20"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="-mt-12 px-6">
        <Avatar name={a.name} status={a.status} size="xl" />
        <h2 className="mt-3 text-xl font-semibold tracking-tight">{a.name}</h2>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge tone="primary">
            <ShieldCheck className="size-3" /> {ROLE_LABEL[a.role] ?? a.role}
          </Badge>
          {s && (
            <span className="inline-flex items-center gap-1.5">
              <span className={cn("size-2 rounded-full", s.dot)} /> {s.label}
            </span>
          )}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-2.5 px-6">
        {[
          { label: "Open tickets", value: a.open_tickets },
          { label: "Capacity", value: cap ?? "—" },
          { label: "Free slots", value: cap != null ? Math.max(0, cap - a.open_tickets) : "—" },
        ].map((x) => (
          <div key={x.label} className="rounded-xl border border-border bg-surface p-3 shadow-card">
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{x.value}</div>
            <div className="text-[11px] text-muted-foreground">{x.label}</div>
          </div>
        ))}
      </div>

      <div className="space-y-6 px-6 py-6">
        <section>
          <SectionTitle icon={Gauge}>Workload</SectionTitle>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-card">
            {cap ? (
              <>
                <div className="mb-2 flex items-baseline justify-between text-sm">
                  <span className="font-medium">
                    {a.open_tickets} <span className="text-muted-foreground">of {cap} tickets</span>
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {Math.min(100, Math.round((a.open_tickets / cap) * 100))}%
                  </span>
                </div>
                <LoadBar open={a.open_tickets} cap={cap} className="h-2" />
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No capacity limit configured.</p>
            )}
          </div>
        </section>

        {canEdit && a.status !== null && (
          <section>
            <SectionTitle icon={Sparkles}>Availability</SectionTitle>
            <div className="grid grid-cols-2 gap-2">
              {(Object.keys(STATUS_META) as AgentStatus[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  disabled={update.isPending}
                  onClick={() =>
                    k !== a.status && patch({ status: k }, `Status: ${STATUS_META[k].label}`)
                  }
                  aria-pressed={a.status === k}
                  className={cn(
                    "flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-all disabled:opacity-60",
                    a.status === k
                      ? "border-primary bg-primary-soft font-medium text-primary-ink ring-4 ring-primary/10"
                      : "border-border bg-surface text-foreground-2 hover:border-border-strong hover:bg-surface-2",
                  )}
                >
                  <span className={cn("size-2 rounded-full", STATUS_META[k].dot)} />{" "}
                  {STATUS_META[k].label}
                </button>
              ))}
            </div>
          </section>
        )}

        <section>
          <SectionTitle icon={Layers}>Channels</SectionTitle>
          <div className="grid grid-cols-3 gap-2">
            {CHANNELS.map(({ key, label, icon: Icon }) => {
              const on = a.channels.includes(key);
              const Comp = canEdit ? "button" : "div";
              return (
                <Comp
                  key={key}
                  {...(canEdit
                    ? {
                        type: "button" as const,
                        onClick: () => toggleChannel(key),
                        disabled: update.isPending,
                        "aria-pressed": on,
                      }
                    : {})}
                  className={cn(
                    "flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-xs font-medium transition-all",
                    on
                      ? "border-primary/40 bg-primary-soft text-primary-ink"
                      : "border-border bg-surface text-subtle-foreground",
                    canEdit && "hover:border-primary/60 disabled:opacity-60",
                  )}
                >
                  <Icon className="size-5" />
                  {label}
                  <span className="text-[10px] font-normal opacity-80">
                    {on ? "Handling" : "Off"}
                  </span>
                </Comp>
              );
            })}
          </div>
        </section>

        <section>
          <SectionTitle icon={Sparkles}>Skills</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {a.skills.length === 0 && (
              <p className="text-sm text-muted-foreground">No skills assigned.</p>
            )}
            {a.skills.map((k) => (
              <Badge key={k} tone="primary" className="py-1 pr-1.5 pl-2.5 text-xs">
                {k}
                {canEdit && (
                  <button
                    type="button"
                    aria-label={`Remove ${k}`}
                    disabled={update.isPending}
                    onClick={() =>
                      patch({ skills: a.skills.filter((x) => x !== k) }, "Skill removed")
                    }
                    className="grid size-4 place-items-center rounded-full hover:bg-primary/20"
                  >
                    <X className="size-3" />
                  </button>
                )}
              </Badge>
            ))}
          </div>
          {canEdit && (
            <form className="mt-3 flex gap-2" noValidate onSubmit={skillForm.handleSubmit}>
              <Input
                {...bind(skillForm, "skill")}
                placeholder="Add a skill, e.g. billing"
                aria-label="New skill"
                className="h-9"
              />
              <Button
                type="submit"
                size="sm"
                variant="outline"
                disabled={!skill.trim() || update.isPending}
                className="h-9"
              >
                <Plus className="size-4" /> Add
              </Button>
            </form>
          )}
          {canEdit && fieldError(skillForm, "skill") && (
            <p role="alert" className="mt-1.5 text-xs text-danger">
              {fieldError(skillForm, "skill")}
            </p>
          )}
        </section>

        <section>
          <SectionTitle icon={Users}>Teams</SectionTitle>
          {memberTeams.length ? (
            <div className="flex flex-wrap gap-1.5">
              {memberTeams.map((t) => (
                <Badge key={t.id} tone="info" className="py-1 text-xs">
                  {t.name}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {teams.isPending ? "Loading…" : "Not part of any team."}
            </p>
          )}
        </section>
      </div>
    </>
  );
}
