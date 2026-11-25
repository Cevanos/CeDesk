"use client";

import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  LayoutDashboard,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog, MenuItem, Popover } from "@/components/ui/overlay";
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
import { SectionHeader } from "@/components/ui/section";
import { LoadError } from "@/components/workspace/list-shared";
import { useCsvDownload } from "@/components/workspace/report-csv";
import { ResultView } from "@/components/workspace/report-result";
import { useReportFilters } from "@/components/workspace/reports-frame";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  cardBody,
  useCreateDashboard,
  useDashboard,
  useDashboards,
  useDeleteDashboard,
  useReportOptions,
  useRunCard,
  useSavedReports,
  useUpdateDashboard,
} from "@/hooks/workspace/reports-explore";
import { ApiError, downloadFilePost } from "@/lib/api/client";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";
import { inboxActions } from "@/lib/permissions";
import { csvName, type CardInput, type Dashboard, type DashboardCard } from "@/lib/reports";

const MAX_CARDS = 20;
const UNAVAILABLE = "Report not available";

const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

/* ---------- list ---------- */

export function DashboardsList() {
  const q = useDashboards();
  const [editing, setEditing] = useState(false);
  const router = useRouter();

  if (q.isPending) return <Skeleton className="h-48 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;

  // Shared first (with the owner), then the person's own.
  const list = [...q.data].sort(
    (a, b) => Number(b.shared) - Number(a.shared) || a.name.localeCompare(b.name),
  );

  return (
    <div className="space-y-4">
      <SectionHeader
        icon={LayoutDashboard}
        title="Your dashboards"
        description={`A dashboard is a set of up to ${MAX_CARDS} reports on one page.`}
        aside={
          <Button size="sm" onClick={() => setEditing(true)}>
            <Plus className="size-3.5" aria-hidden /> New dashboard
          </Button>
        }
      />
      {list.length === 0 ? (
        <Card>
          <EmptyState title="No dashboards yet">
            Make one to keep your most-used reports together.
          </EmptyState>
        </Card>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {list.map((d) => (
            <li key={d.id}>
              <Link
                href={`/reports/dashboards/${d.id}`}
                className="block rounded-xl border border-border bg-surface p-4 shadow-card transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-pop"
              >
                <div className="flex items-start justify-between gap-2">
                  <h2 className="min-w-0 truncate text-[15px] font-semibold">{d.name}</h2>
                  {d.shared && <Badge tone="primary">Shared</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {d.cards.length} card{d.cards.length === 1 ? "" : "s"}
                  {d.shared && d.owner_name ? ` · ${d.owner_name}` : ""}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <DashboardEditor
          onClose={() => setEditing(false)}
          onSaved={(d) => router.push(`/reports/dashboards/${d.id}`)}
        />
      )}
    </div>
  );
}

/* ---------- one dashboard ---------- */

export function DashboardPage({ id }: { id: string }) {
  const q = useDashboard(id);
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const remove = useDeleteDashboard();
  const { ask, dialog } = useConfirm();
  const { runRange, teamId, badRange } = useReportFilters();

  if (q.isPending) return <Skeleton className="h-64 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const d = q.data;
  const cards = [...d.cards].sort((a, b) => a.position - b.position);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/reports/dashboards"
            aria-label="Back to dashboards"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-surface-2"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <h2 className="truncate text-lg font-semibold tracking-tight">{d.name}</h2>
          {d.shared && (
            <Badge tone="primary">{d.owner_name ? `Shared by ${d.owner_name}` : "Shared"}</Badge>
          )}
        </div>
        {d.can_edit && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" aria-hidden /> Edit
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                ask({
                  title: "Delete this dashboard?",
                  message: "Delete this dashboard? The reports on it are not deleted.",
                  detail: d.name,
                  confirmLabel: "Delete",
                  run: () =>
                    remove
                      .mutateAsync(d.id)
                      .then(() => router.push("/reports/dashboards"))
                      .catch((e) => toast.error(msg(e))),
                })
              }
            >
              <Trash2 className="size-3.5" aria-hidden /> Delete
            </Button>
          </div>
        )}
      </div>

      <SectionHeader
        icon={LayoutDashboard}
        title="Cards"
        description="Each card is one report for the chosen period."
      />
      {cards.length === 0 ? (
        <Card>
          <EmptyState title="No cards yet">Edit the dashboard to add reports.</EmptyState>
        </Card>
      ) : badRange ? null : (
        <div className="grid gap-4 md:grid-cols-2">
          {cards.map((c) => (
            <CardView key={c.id} dashboard={d} card={c} range={runRange} teamId={teamId} />
          ))}
        </div>
      )}

      {editing && <DashboardEditor dashboard={d} onClose={() => setEditing(false)} />}
      {dialog}
    </div>
  );
}

function CardView({
  dashboard,
  card,
  range,
  teamId,
}: {
  dashboard: Dashboard;
  card: DashboardCard;
  range: ReturnType<typeof useReportFilters>["runRange"];
  teamId: string;
}) {
  const unavailable = card.label === UNAVAILABLE;
  const params = { range, team_id: teamId || undefined };
  // Cards run on their own (at most 3 at a time): one that fails does not stop the rest.
  const q = useRunCard(dashboard.id, card.id, params, !unavailable);
  const router = useRouter();
  const csv = useCsvDownload();

  if (unavailable) {
    return (
      <Card className="grid min-h-32 place-items-center bg-surface-2 p-4 text-sm text-muted-foreground">
        {UNAVAILABLE}
      </Card>
    );
  }

  const builderHref = card.preset
    ? `/reports/explore?preset=${encodeURIComponent(card.preset)}`
    : `/reports/explore?report=${encodeURIComponent(card.report_id ?? "")}`;

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-semibold" title={card.label}>
          {card.label}
        </h3>
        <Popover
          trigger={({ props }) => (
            <button
              type="button"
              aria-label={`Actions for ${card.label}`}
              className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-surface-2"
              {...props}
            >
              <MoreHorizontal className="size-4" />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuItem
                disabled={csv.busy}
                onClick={() => {
                  close();
                  csv.run(() =>
                    downloadFilePost(
                      `dashboards/${dashboard.id}/cards/${card.id}/run`,
                      cardBody(params),
                      q.data ? csvName(q.data.metric, q.data.group_by) : "cedesk-report.csv",
                      { format: "csv" },
                    ),
                  );
                }}
              >
                Download CSV
              </MenuItem>
              <MenuItem
                onClick={() => {
                  close();
                  router.push(builderHref);
                }}
              >
                Open in builder
              </MenuItem>
            </>
          )}
        </Popover>
      </div>
      {q.isPending ? (
        <Skeleton className="h-32 rounded-lg" />
      ) : q.isError ? (
        <LoadError error={q.error} />
      ) : (
        <div className={q.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
          <ResultView result={q.data} compact />
        </div>
      )}
    </Card>
  );
}

/* ---------- editor ---------- */

interface EditCard {
  uid: number;
  preset: string | null;
  report_id: string | null;
  label: string;
}

let uidSeed = 0;

export function DashboardEditor({
  dashboard,
  onClose,
  onSaved,
}: {
  dashboard?: Dashboard;
  onClose: () => void;
  onSaved?: (d: Dashboard) => void;
}) {
  const { user } = useCurrentUser();
  const canShare = inboxActions(user).shareViews;
  const options = useReportOptions();
  const saved = useSavedReports();
  const create = useCreateDashboard();
  const update = useUpdateDashboard();
  const form = useFormik({
    initialValues: { name: dashboard?.name ?? "" },
    validationSchema: yup.object({
      name: yup
        .string()
        .trim()
        .min(1, "A dashboard's name is 1-80 characters.")
        .max(80, "A dashboard's name is 1-80 characters.")
        .required("A dashboard's name is 1-80 characters."),
    }),
    onSubmit: (v) => save(v.name.trim()),
  });
  const [shared, setShared] = useState(dashboard?.shared ?? false);
  const [cards, setCards] = useState<EditCard[]>(() =>
    [...(dashboard?.cards ?? [])]
      .sort((a, b) => a.position - b.position)
      .map((c) => ({ uid: ++uidSeed, preset: c.preset, report_id: c.report_id, label: c.label })),
  );
  const [pickPreset, setPickPreset] = useState("");
  const [pickSaved, setPickSaved] = useState("");
  const [error, setError] = useState<string | null>(null);
  const busy = create.isPending || update.isPending;
  const isShared = canShare && shared;
  const savedById = new Map((saved.data ?? []).map((r) => [r.id, r]));

  const add = (c: Omit<EditCard, "uid">) => {
    if (cards.length >= MAX_CARDS) {
      setError("A dashboard has at most 20 cards.");
      return;
    }
    setError(null);
    setCards((cs) => [...cs, { uid: ++uidSeed, ...c }]);
  };
  const move = (i: number, by: -1 | 1) =>
    setCards((cs) => {
      const j = i + by;
      if (j < 0 || j >= cs.length) return cs;
      const next = [...cs];
      [next[i], next[j]] = [next[j]!, next[i]!];
      return next;
    });

  const save = async (trimmed: string) => {
    // The whole card list is sent, in order.
    const body: CardInput[] = cards.map((c) =>
      c.preset ? { preset: c.preset } : { report_id: c.report_id! },
    );
    try {
      const d = dashboard
        ? await update.mutateAsync({
            id: dashboard.id,
            name: trimmed,
            shared: isShared,
            cards: body,
          })
        : await create.mutateAsync({ name: trimmed, shared: isShared, cards: body });
      toast.success(dashboard ? "Dashboard saved" : "Dashboard created");
      onClose();
      onSaved?.(d);
    } catch (err) {
      setError(msg(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={dashboard ? "Edit dashboard" : "New dashboard"}
      className="sm:max-w-xl"
    >
      <form noValidate onSubmit={form.handleSubmit} className="space-y-4">
        <Field label="Name" htmlFor="db-name" error={fieldError(form, "name")}>
          <Input
            id="db-name"
            maxLength={80}
            {...bind(form, "name")}
            onChange={(e) => {
              form.handleChange(e);
              setError(null);
            }}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={isShared}
            disabled={!canShare}
            onChange={(e) => setShared(e.target.checked)}
          />
          Share with the team
        </label>
        {isShared && (
          <p className="text-xs text-muted-foreground">
            A shared dashboard can only hold built-in reports and shared saved reports.
          </p>
        )}

        <div>
          <h3 className="mb-1.5 text-[13px] font-medium text-foreground-2">
            Cards ({cards.length}/{MAX_CARDS})
          </h3>
          {cards.length === 0 ? (
            <p className="text-sm text-muted-foreground">No cards yet. Add one below.</p>
          ) : (
            <ol className="space-y-1.5">
              {cards.map((c, i) => {
                const personal =
                  isShared && c.report_id && savedById.get(c.report_id)?.shared === false;
                return (
                  <li
                    key={c.uid}
                    className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-sm"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {c.label}
                      {c.label === UNAVAILABLE && (
                        <span className="ml-1 text-xs text-muted-foreground">(remove it)</span>
                      )}
                      {personal && (
                        <span className="ml-1 text-xs text-danger">Share this report first</span>
                      )}
                    </span>
                    <button
                      type="button"
                      aria-label={`Move ${c.label} up`}
                      disabled={i === 0}
                      onClick={() => move(i, -1)}
                      className="grid size-7 place-items-center rounded-md hover:bg-surface-3 disabled:opacity-40"
                    >
                      <ArrowUp className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${c.label} down`}
                      disabled={i === cards.length - 1}
                      onClick={() => move(i, 1)}
                      className="grid size-7 place-items-center rounded-md hover:bg-surface-3 disabled:opacity-40"
                    >
                      <ArrowDown className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${c.label}`}
                      onClick={() => setCards((cs) => cs.filter((x) => x.uid !== c.uid))}
                      className="grid size-7 place-items-center rounded-md text-danger hover:bg-surface-3"
                    >
                      <X className="size-3.5" />
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="db-add-preset" className="text-[13px] font-medium text-foreground-2">
              Add a built-in report
            </label>
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <Select
                  id="db-add-preset"
                  value={pickPreset}
                  onChange={(e) => setPickPreset(e.target.value)}
                  options={[
                    { value: "", label: "Choose…" },
                    ...(options.data?.presets ?? []).map((p) => ({ value: p.key, label: p.label })),
                  ]}
                />
              </div>
              <Button
                variant="outline"
                disabled={!pickPreset}
                onClick={() => {
                  const p = options.data?.presets.find((x) => x.key === pickPreset);
                  if (p) add({ preset: p.key, report_id: null, label: p.label });
                  setPickPreset("");
                }}
              >
                Add
              </Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="db-add-saved" className="text-[13px] font-medium text-foreground-2">
              Add a saved report
            </label>
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <Select
                  id="db-add-saved"
                  value={pickSaved}
                  onChange={(e) => setPickSaved(e.target.value)}
                  options={[
                    { value: "", label: "Choose…" },
                    ...(saved.data ?? []).map((r) => ({
                      value: r.id,
                      label: isShared && !r.shared ? `${r.name} (share this report first)` : r.name,
                      disabled: isShared && !r.shared,
                    })),
                  ]}
                />
              </div>
              <Button
                variant="outline"
                disabled={!pickSaved}
                onClick={() => {
                  const r = savedById.get(pickSaved);
                  if (r) add({ preset: null, report_id: r.id, label: r.name });
                  setPickSaved("");
                }}
              >
                Add
              </Button>
            </div>
          </div>
        </div>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
