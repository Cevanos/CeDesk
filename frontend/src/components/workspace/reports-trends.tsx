"use client";

import {
  BarChart3,
  Bookmark,
  Inbox,
  LineChart,
  MoreHorizontal,
  PieChart,
  Play,
  Save,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { DatePicker } from "@/components/ui/date-picker";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Card, Field, Input, Select, Skeleton } from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { LoadError } from "@/components/workspace/list-shared";
import { RunCsvButton } from "@/components/workspace/report-csv";
import { ResultCard, ResultView } from "@/components/workspace/report-result";
import { useReportFilters } from "@/components/workspace/reports-frame";
import { useCurrentUser } from "@/hooks/use-auth";
import { useMemberOptions, useTeams } from "@/hooks/use-workspace";
import { withOffset } from "@/hooks/workspace/reports";
import {
  useCreateSavedReport,
  useDashboards,
  useDeleteSavedReport,
  useReportOptions,
  useRunReport,
  useSavedReports,
  useSchedules,
  useUpdateSavedReport,
  type RunSource,
} from "@/hooks/workspace/reports-explore";
import { useTicketTypes } from "@/hooks/workspace/ticket-types";
import { ApiError } from "@/lib/api/client";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { inboxActions } from "@/lib/permissions";
import {
  RANGE_PRESETS,
  allowedGroupBy,
  capitalize,
  hasPresetRange,
  normalizeDefinition,
  type MetricInfo,
  type PresetInfo,
  type RangePreset,
  type ReportDefinition,
  type ReportFilters,
  type ReportOptions,
  type SavedReport,
} from "@/lib/reports";
import { cn } from "@/lib/utils";

const GROUP_LABEL: Record<string, string> = {
  none: "None",
  day: "Day",
  week: "Week",
  month: "Month",
  team: "Team",
  assignee: "Assignee",
  priority: "Priority",
  type: "Ticket type",
  channel: "Channel",
  tag: "Tag",
  topic: "Topic",
  sentiment: "Sentiment",
};

const PRIORITIES = ["urgent", "high", "medium", "low"];
const CHANNELS = ["email", "web", "portal", "api", "chat", "phone"];
const SENTIMENTS = ["negative", "neutral", "positive"];

/** Where the 19 built-in reports go in the list. */
const PRESET_GROUPS: { title: string; keys: string[] }[] = [
  {
    title: "Over time",
    keys: [
      "created_per_day",
      "resolved_per_day",
      "first_response_over_time",
      "resolution_over_time",
      "csat_trend",
      "reopen_rate_over_time",
      "backlog_by_day",
    ],
  },
  {
    title: "Breakdowns",
    keys: [
      "created_by_tag",
      "created_by_type",
      "created_by_priority",
      "created_by_team",
      "created_by_channel",
      "first_response_by_team",
      "resolution_by_team",
      "csat_by_team",
    ],
  },
  {
    title: "Open now",
    keys: ["open_by_priority", "open_by_type", "open_by_team", "open_by_channel"],
  },
];

/** The builder's own state; turned into a definition when it runs. */
interface Form {
  metric: string;
  group_by: string;
  filters: ReportFilters;
  /** A preset, or `custom` with dates (YYYY-MM-DD). */
  range: RangePreset | "custom";
  from: string;
  to: string;
}

/** A saved report needs a preset range, even a report that is "as of now". */
const forSave = (d: ReportDefinition): ReportDefinition => ({
  ...d,
  range: d.range ?? { preset: "last_30d" },
});

const emptyForm = (): Form => ({
  metric: "created",
  group_by: "day",
  filters: {},
  range: "last_30d",
  from: "",
  to: "",
});

function formFromDefinition(d: ReportDefinition, prev?: Form): Form {
  const r = d.range;
  return {
    metric: d.metric,
    group_by: d.group_by ?? "none",
    filters: { ...(d.filters ?? {}) },
    range: r && "preset" in r ? r.preset : r ? "custom" : (prev?.range ?? "last_30d"),
    from: r && "from" in r ? r.from.slice(0, 10) : (prev?.from ?? ""),
    to: r && "to" in r ? r.to.slice(0, 10) : (prev?.to ?? ""),
  };
}

function toDefinition(f: Form, teamId: string, kind: MetricInfo["kind"] | undefined) {
  return normalizeDefinition(
    {
      metric: f.metric,
      group_by: f.group_by,
      filters: { ...f.filters, ...(teamId ? { team: teamId } : {}) },
      range:
        f.range === "custom"
          ? { from: withOffset(f.from), to: withOffset(f.to, true) }
          : { preset: f.range },
    },
    kind,
  );
}

export function TrendsTab() {
  const options = useReportOptions();
  if (options.isPending) return <Skeleton className="h-72 rounded-xl" />;
  if (options.isError) return <LoadError error={options.error} />;
  return <Trends options={options.data} />;
}

function Trends({ options }: { options: ReportOptions }) {
  const { teamId } = useReportFilters();
  const params = useSearchParams();
  const saved = useSavedReports();
  const [form, setForm] = useState<Form>(emptyForm);
  const [source, setSource] = useState<RunSource | null>(null);
  const [saving, setSaving] = useState<null | { report?: SavedReport }>(null);

  const metric = options.metrics.find((m) => m.key === form.metric);
  const kind = metric?.kind;
  const showFilters = kind !== "backlog";

  /** Runs a definition (the header's team goes in as the team filter). */
  const runDefinition = (f: Form) => {
    const k = options.metrics.find((m) => m.key === f.metric)?.kind;
    if (f.range === "custom" && (!f.from || !f.to || f.from > f.to)) {
      toast.error("Pick a start date that is not after the end date.");
      return;
    }
    setSource({ kind: "body", body: { definition: toDefinition(f, teamId, k) } });
  };

  const openPreset = (p: PresetInfo) => {
    const next = formFromDefinition(p.definition, form);
    // A built-in keeps the range picked in the builder.
    next.range = form.range;
    next.from = form.from;
    next.to = form.to;
    setForm(next);
    if (teamId || next.range === "custom") return runDefinition(next);
    setSource({
      kind: "body",
      body: { preset: p.key, range: { preset: next.range as RangePreset } },
    });
  };

  const openSaved = (r: SavedReport) => {
    const next = formFromDefinition(r.definition, form);
    setForm(next);
    // The saved route can't take a team, so a picked team runs the definition instead.
    if (teamId) runDefinition(next);
    else setSource({ kind: "saved", id: r.id });
  };

  // "Open in builder" from a dashboard card: ?preset=key or ?report=id. It is applied once, as
  // soon as the list it points into has loaded (state set while rendering, not in an effect).
  const [handled, setHandled] = useState(false);
  if (!handled) {
    const presetKey = params.get("preset");
    const reportId = params.get("report");
    const p = presetKey ? options.presets.find((x) => x.key === presetKey) : undefined;
    const r = reportId ? saved.data?.find((x) => x.id === reportId) : undefined;
    if (p || r || (!presetKey && !reportId) || (reportId && saved.data)) {
      setHandled(true);
      if (p) openPreset(p);
      else if (r) openSaved(r);
    }
  }

  const result = useRunReport(source);

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <aside
        className="h-fit space-y-5 rounded-2xl border border-border bg-surface p-3 shadow-card lg:sticky lg:top-4 lg:max-h-[calc(100svh-2rem)] lg:overflow-y-auto"
        aria-label="Reports"
      >
        <SavedList
          reports={saved.data}
          loading={saved.isPending}
          onOpen={openSaved}
          onRename={(r) => setSaving({ report: r })}
          getDefinition={() => toDefinition(form, teamId, kind)}
        />
        {PRESET_GROUPS.map((g) => {
          const items = g.keys
            .map((k) => options.presets.find((p) => p.key === k))
            .filter((p): p is PresetInfo => !!p);
          const Icon = GROUP_ICON[g.title] ?? BarChart3;
          return (
            <section key={g.title} aria-label={g.title}>
              <SideHeading icon={Icon} count={items.length}>
                {g.title}
              </SideHeading>
              <ul className="space-y-0.5">
                {items.map((p) => (
                  <li key={p.key}>
                    <PresetButton
                      label={p.label}
                      active={
                        source?.kind === "body" &&
                        "preset" in source.body &&
                        source.body.preset === p.key
                      }
                      onClick={() => openPreset(p)}
                    />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        {/* Built-ins the server added that this list does not know yet. */}
        {(() => {
          const known = new Set(PRESET_GROUPS.flatMap((g) => g.keys));
          const extra = options.presets.filter((p) => !known.has(p.key));
          return extra.length ? (
            <section aria-label="More reports">
              <SideHeading icon={Sparkles}>More</SideHeading>
              <ul className="space-y-0.5">
                {extra.map((p) => (
                  <li key={p.key}>
                    <PresetButton label={p.label} onClick={() => openPreset(p)} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null;
        })()}
      </aside>

      <div className="min-w-0 space-y-10">
        <Builder
          options={options}
          form={form}
          setForm={setForm}
          kind={kind}
          showFilters={showFilters}
          teamId={teamId}
          onRun={() => runDefinition(form)}
          onSave={() => setSaving({})}
        />

        {source === null ? (
          <Card className="flex flex-col items-center gap-3 border-dashed px-6 py-14 text-center">
            <span className="grid size-12 place-items-center rounded-2xl bg-primary-soft text-primary-ink">
              <LineChart className="size-6" aria-hidden />
            </span>
            <div>
              <p className="text-sm font-semibold">No report running</p>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Pick a report on the left, or set up your own above and press Run.
              </p>
            </div>
          </Card>
        ) : result.isPending ? (
          <Skeleton className="h-64 rounded-xl" />
        ) : result.isError ? (
          <LoadError error={result.error} />
        ) : (
          <section
            aria-label="Results"
            className={cn("transition-opacity", result.isPlaceholderData && "opacity-60")}
          >
            <SectionHeader
              icon={LineChart}
              title="Results"
              description="The report you just ran."
            />
            <ResultCard>
              <ResultView
                result={result.data}
                actions={
                  <RunCsvButton
                    source={source}
                    metric={result.data.metric}
                    groupBy={result.data.group_by}
                  />
                }
              />
            </ResultCard>
          </section>
        )}
      </div>

      {saving && (
        <SaveDialog
          existing={saving.report}
          definition={toDefinition(form, teamId, kind)}
          onClose={() => setSaving(null)}
        />
      )}
    </div>
  );
}

const GROUP_ICON: Record<string, LucideIcon> = {
  "Over time": TrendingUp,
  Breakdowns: PieChart,
  "Open now": Inbox,
};

function SideHeading({
  icon: Icon,
  count,
  children,
}: {
  icon: LucideIcon;
  count?: number;
  children: ReactNode;
}) {
  return (
    <h2 className="mb-2 flex items-center gap-2.5 rounded-xl border border-border bg-surface-2 px-2.5 py-2 text-sm font-semibold tracking-tight text-foreground">
      <span className="grid size-6 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <Icon className="size-3.5" aria-hidden />
      </span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {count !== undefined && (
        <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-muted-foreground tabular-nums ring-1 ring-border">
          {count}
        </span>
      )}
    </h2>
  );
}

function PresetButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={cn(
        "relative w-full rounded-lg py-1.5 pr-2.5 pl-3 text-left text-[13px] transition-colors",
        active
          ? "bg-primary-soft font-semibold text-primary-ink"
          : "text-foreground-2 hover:bg-surface-2 hover:text-foreground",
      )}
    >
      {active && (
        <span
          aria-hidden
          className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-primary"
        />
      )}
      {label}
    </button>
  );
}

/* ---------- builder ---------- */

function Builder({
  options,
  form,
  setForm,
  kind,
  showFilters,
  teamId,
  onRun,
  onSave,
}: {
  options: ReportOptions;
  form: Form;
  setForm: (f: Form) => void;
  kind: MetricInfo["kind"] | undefined;
  showFilters: boolean;
  teamId: string;
  onRun: () => void;
  onSave: () => void;
}) {
  const members = useMemberOptions();
  const types = useTicketTypes();
  const teams = useTeams();
  const groups = allowedGroupBy(kind, options.group_by);
  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch });
  const setFilter = (k: keyof ReportFilters, v: string) =>
    set({ filters: { ...form.filters, [k]: v || undefined } as ReportFilters });

  const pickMetric = (key: string) => {
    const k = options.metrics.find((m) => m.key === key)?.kind;
    const ok = allowedGroupBy(k, options.group_by);
    set({
      metric: key,
      group_by: ok.includes(form.group_by) ? form.group_by : k === "backlog" ? "day" : "none",
      filters: k === "backlog" ? {} : form.filters,
    });
  };

  const teamName = teams.data?.find((t) => t.id === teamId)?.name;

  return (
    <section aria-label="Report builder">
      <SectionHeader
        icon={SlidersHorizontal}
        title="Report builder"
        description="Pick a metric, how to group it and the period, then run."
      />
      <Card className="space-y-4 p-4 sm:p-5">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Metric" htmlFor="rb-metric">
            <Select
              id="rb-metric"
              value={form.metric}
              onChange={(e) => pickMetric(e.target.value)}
              options={options.metrics.map((m) => ({ value: m.key, label: m.label }))}
            />
          </Field>
          <Field
            label="Group by"
            htmlFor="rb-group"
            hint={kind === "backlog" ? "The backlog is grouped by day." : undefined}
          >
            <Select
              id="rb-group"
              value={form.group_by}
              disabled={kind === "backlog"}
              onChange={(e) => set({ group_by: e.target.value })}
              options={groups.map((g) => ({ value: g, label: GROUP_LABEL[g] ?? capitalize(g) }))}
            />
          </Field>
          {kind === "now" ? (
            <Field label="Range" htmlFor="rb-range" hint="As of now">
              <Input id="rb-range" value="As of now" disabled readOnly />
            </Field>
          ) : (
            <Field label="Range" htmlFor="rb-range">
              <Select
                id="rb-range"
                value={form.range}
                onChange={(e) => set({ range: e.target.value as Form["range"] })}
                options={[
                  ...RANGE_PRESETS.filter((r) => options.ranges.includes(r.value)).map((r) => ({
                    value: r.value,
                    label: r.label,
                  })),
                  { value: "custom", label: "Custom" },
                ]}
              />
            </Field>
          )}
        </div>

        {kind !== "now" && form.range === "custom" && (
          <div className="flex flex-wrap items-center gap-3">
            <label
              htmlFor="rb-from"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              From
              <DatePicker
                id="rb-from"
                type="date"
                className="w-40"
                value={form.from}
                max={form.to || undefined}
                onChange={(v) => set({ from: v })}
              />
            </label>
            <label
              htmlFor="rb-to"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              To
              <DatePicker
                id="rb-to"
                type="date"
                className="w-40"
                value={form.to}
                min={form.from || undefined}
                onChange={(v) => set({ to: v })}
              />
            </label>
          </div>
        )}

        {showFilters && (
          <details className="group" open={Object.values(form.filters).some(Boolean)}>
            <summary className="cursor-pointer text-[13px] font-medium text-foreground-2">
              Filters
            </summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Field label="Assignee" htmlFor="rf-assignee">
                <Select
                  id="rf-assignee"
                  value={form.filters.assignee ?? ""}
                  onChange={(e) => setFilter("assignee", e.target.value)}
                  options={[
                    { value: "", label: "Anyone" },
                    ...(members.data ?? []).map((m) => ({ value: m.id, label: m.name })),
                  ]}
                />
              </Field>
              <Field label="Priority" htmlFor="rf-priority">
                <Select
                  id="rf-priority"
                  value={form.filters.priority ?? ""}
                  onChange={(e) => setFilter("priority", e.target.value)}
                  options={[
                    { value: "", label: "Any" },
                    ...PRIORITIES.map((p) => ({ value: p, label: capitalize(p) })),
                  ]}
                />
              </Field>
              <Field label="Ticket type" htmlFor="rf-type">
                <Select
                  id="rf-type"
                  value={form.filters.type ?? ""}
                  onChange={(e) => setFilter("type", e.target.value)}
                  options={[
                    { value: "", label: "Any" },
                    ...(types.data ?? []).map((t) => ({ value: t.id, label: t.name })),
                  ]}
                />
              </Field>
              <Field label="Channel" htmlFor="rf-channel">
                <Select
                  id="rf-channel"
                  value={form.filters.channel ?? ""}
                  onChange={(e) => setFilter("channel", e.target.value)}
                  options={[
                    { value: "", label: "Any" },
                    ...CHANNELS.map((c) => ({ value: c, label: capitalize(c) })),
                  ]}
                />
              </Field>
              <Field label="Tag" htmlFor="rf-tag">
                <Input
                  id="rf-tag"
                  maxLength={60}
                  value={form.filters.tag ?? ""}
                  onChange={(e) => setFilter("tag", e.target.value)}
                />
              </Field>
              <Field label="Topic" htmlFor="rf-topic">
                <Input
                  id="rf-topic"
                  maxLength={60}
                  value={form.filters.topic ?? ""}
                  onChange={(e) => setFilter("topic", e.target.value)}
                />
              </Field>
              <Field label="Sentiment" htmlFor="rf-sentiment">
                <Select
                  id="rf-sentiment"
                  value={form.filters.sentiment ?? ""}
                  onChange={(e) => setFilter("sentiment", e.target.value)}
                  options={[
                    { value: "", label: "Any" },
                    ...SENTIMENTS.map((s) => ({ value: s, label: capitalize(s) })),
                  ]}
                />
              </Field>
              <Field label="Imported tickets" htmlFor="rf-imported">
                <Select
                  id="rf-imported"
                  value={form.filters.imported ?? "any"}
                  onChange={(e) => setFilter("imported", e.target.value)}
                  options={[
                    { value: "any", label: "Include" },
                    { value: "exclude", label: "Leave out" },
                    { value: "only", label: "Only" },
                  ]}
                />
              </Field>
            </div>
          </details>
        )}
        {kind === "backlog" && (
          <p className="text-xs text-muted-foreground">The backlog can only be filtered by team.</p>
        )}
        {teamId && (
          <p className="text-xs text-muted-foreground">
            Team: {teamName ?? "selected team"} (from the picker above).
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onRun}>
            <Play className="size-3.5" aria-hidden /> Run
          </Button>
          <Button size="sm" variant="outline" onClick={onSave}>
            <Save className="size-3.5" aria-hidden /> Save
          </Button>
        </div>
      </Card>
    </section>
  );
}

/* ---------- saved reports ---------- */

function SavedList({
  reports,
  loading,
  onOpen,
  onRename,
  getDefinition,
}: {
  reports: SavedReport[] | undefined;
  loading: boolean;
  onOpen: (r: SavedReport) => void;
  onRename: (r: SavedReport) => void;
  getDefinition: () => ReportDefinition;
}) {
  const { user } = useCurrentUser();
  const share = inboxActions(user).shareViews;
  const update = useUpdateSavedReport();
  const remove = useDeleteSavedReport();
  const dashboards = useDashboards();
  const schedules = useSchedules();
  const { ask, dialog } = useConfirm();

  const fail = (e: unknown) =>
    toast.error(e instanceof ApiError ? e.userMessage : "Couldn't save.");

  const confirmDelete = (r: SavedReport) => {
    const cards = (dashboards.data ?? [])
      .filter((d) => d.owner_id === user?.id)
      .flatMap((d) => d.cards)
      .filter((c) => c.report_id === r.id).length;
    const mails = (schedules.data ?? []).filter(
      (s) => s.owner_id === user?.id && s.target.report_id === r.id,
    ).length;
    ask({
      title: "Delete this report?",
      message:
        "Delete this report? Your own dashboard cards and scheduled emails that use it are deleted too.",
      detail:
        cards || mails
          ? `${r.name}: ${cards} dashboard card${cards === 1 ? "" : "s"}, ${mails} scheduled email${mails === 1 ? "" : "s"}`
          : r.name,
      confirmLabel: "Delete",
      run: () => remove.mutateAsync(r.id).catch(fail),
    });
  };

  const sorted = reports ?? [];
  return (
    <section aria-label="Saved reports">
      <SideHeading icon={Bookmark}>Saved reports</SideHeading>
      {loading ? (
        <Skeleton className="h-16 rounded-lg" />
      ) : sorted.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
          Nothing saved yet. Set up a report and press Save.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {sorted.map((r) => (
            <li key={r.id} className="group flex items-center gap-1">
              <button
                type="button"
                onClick={() => onOpen(r)}
                className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-3 py-1.5 text-left text-[13px] text-foreground-2 transition-colors hover:bg-surface-2 hover:text-foreground"
              >
                {r.shared && (
                  <Users
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-label="Shared"
                    role="img"
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{r.name}</span>
                {r.shared && r.owner_name && (
                  <span className="shrink-0 text-[11px] text-subtle-foreground">
                    {r.owner_name}
                  </span>
                )}
              </button>
              {r.can_edit && (
                <Popover
                  trigger={({ props }) => (
                    <button
                      type="button"
                      aria-label={`Actions for ${r.name}`}
                      className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2"
                      {...props}
                    >
                      <MoreHorizontal className="size-4" />
                    </button>
                  )}
                >
                  {(close) => (
                    <>
                      <MenuItem
                        onClick={() => {
                          close();
                          onRename(r);
                        }}
                      >
                        Rename
                      </MenuItem>
                      <MenuItem
                        onClick={() => {
                          close();
                          const def = getDefinition();
                          if (def.range && !hasPresetRange(def.range)) {
                            toast.error("A saved report uses a preset range");
                            return;
                          }
                          update
                            .mutateAsync({ id: r.id, definition: forSave(def) })
                            .then(() => toast.success("Report updated"))
                            .catch(fail);
                        }}
                      >
                        Save current changes
                      </MenuItem>
                      {share && (
                        <MenuItem
                          onClick={() => {
                            close();
                            update
                              .mutateAsync({ id: r.id, shared: !r.shared })
                              .then(() =>
                                toast.success(r.shared ? "Report unshared" : "Report shared"),
                              )
                              .catch(fail);
                          }}
                        >
                          {r.shared ? "Unshare" : "Share with the team"}
                        </MenuItem>
                      )}
                      <MenuItem
                        className="text-danger"
                        onClick={() => {
                          close();
                          confirmDelete(r);
                        }}
                      >
                        Delete
                      </MenuItem>
                    </>
                  )}
                </Popover>
              )}
            </li>
          ))}
        </ul>
      )}
      {dialog}
    </section>
  );
}

function SaveDialog({
  existing,
  definition,
  onClose,
}: {
  existing?: SavedReport;
  definition: ReportDefinition;
  onClose: () => void;
}) {
  const { user } = useCurrentUser();
  const share = inboxActions(user).shareViews;
  const create = useCreateSavedReport();
  const update = useUpdateSavedReport();
  const form = useFormik({
    initialValues: { name: existing?.name ?? "" },
    validationSchema: yup.object({
      name: yup
        .string()
        .trim()
        .min(1, "A report's name is 1-80 characters.")
        .max(80, "A report's name is 1-80 characters.")
        .required("A report's name is 1-80 characters."),
    }),
    onSubmit: (v) => save(v.name.trim()),
  });
  const [shared, setShared] = useState(existing?.shared ?? false);
  const [error, setError] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;
  const customRange = !existing && !!definition.range && !hasPresetRange(definition.range);

  const save = async (trimmed: string) => {
    try {
      if (existing) await update.mutateAsync({ id: existing.id, name: trimmed });
      else
        await create.mutateAsync({
          name: trimmed,
          definition: forSave(definition),
          shared: share && shared,
        });
      toast.success(existing ? "Report renamed" : "Report saved");
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Couldn't save.");
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={existing ? "Rename report" : "Save report"}
      description={existing ? undefined : "A saved report keeps its range up to date."}
    >
      <form noValidate onSubmit={form.handleSubmit} className="space-y-4">
        <Field
          label="Name"
          htmlFor="sr-name"
          error={fieldError(form, "name") ?? error ?? undefined}
        >
          <Input
            id="sr-name"
            maxLength={80}
            {...bind(form, "name")}
            aria-invalid={!!(fieldError(form, "name") ?? error)}
            onChange={(e) => {
              form.handleChange(e);
              setError(null);
            }}
          />
        </Field>
        {!existing && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={shared && share}
              disabled={!share}
              onChange={(e) => setShared(e.target.checked)}
            />
            Share with the team
          </label>
        )}
        {!existing && !share && (
          <p className="text-xs text-muted-foreground">
            Your role can&apos;t share reports. It is saved as a personal report.
          </p>
        )}
        {customRange && (
          <p role="alert" className="text-xs text-danger">
            A saved report uses a preset range. Pick one (for example Last 30 days) in the builder.
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={customRange}>
            {existing ? "Rename" : "Save"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
