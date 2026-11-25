"use client";

import {
  Building2,
  ChevronRight,
  Download,
  Filter,
  Mail,
  Phone,
  Search,
  Settings2,
  Star,
  Ticket,
  Upload,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ImportDialog } from "@/components/contacts/import-dialog";
import { SegmentNameDialog, SegmentsPanel } from "@/components/contacts/segments-panel";
import { AiBadge, AiPanel } from "@/components/ui/ai";
import { DatePicker } from "@/components/ui/date-picker";
import { InfiniteScroll } from "@/components/ui/infinite-scroll";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Select,
  Skeleton,
} from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  exportContacts,
  useContactFields,
  useContactList,
  useDebounced,
  useSaveSegment,
  useSegment,
  useSegmentContacts,
} from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { ContactFilters, ContactRow, ContactSort, Segment } from "@/lib/api/contacts-types";
import {
  describeFilters,
  EMPTY_FILTERS,
  filtersToSegment,
  isFiltered,
  segmentToFilters,
  SORT_LABEL,
} from "@/lib/contacts";
import { bind, useFormik, yup } from "@/lib/forms";
import { hasAction } from "@/lib/permissions";
import { cn, timeAgo } from "@/lib/utils";

const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

function Chip({ children, onRemove }: { children: React.ReactNode; onRemove?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-primary-soft px-2.5 py-1 text-xs font-medium text-primary-ink">
      {children}
      {onRemove && (
        <button
          type="button"
          aria-label="Remove filter"
          onClick={onRemove}
          className="grid size-4 place-items-center rounded-full hover:bg-primary/15"
        >
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}

function Toggle({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        on
          ? "border-primary bg-primary-soft text-primary-ink"
          : "border-border text-muted-foreground hover:bg-surface-2",
      )}
    >
      {children}
    </button>
  );
}

/** One chip per contact field value: pick a field, then its value. */
function AttributeFilter({
  filters,
  onChange,
}: {
  filters: ContactFilters;
  onChange: (f: ContactFilters) => void;
}) {
  const fields = useContactFields();
  const [key, setKey] = useState("");
  const field = fields.data?.find((f) => f.key === key);
  const add = (v: string) => {
    if (!key || !v.trim()) return;
    onChange({ ...filters, attributes: { ...filters.attributes, [key]: v.trim() } });
    valueForm.resetForm();
  };
  const valueForm = useFormik({
    initialValues: { value: "" },
    validationSchema: yup.object({
      value: yup
        .string()
        .max(500, "Use at most 500 characters.")
        .test(
          "number",
          "Enter a number.",
          (t) => field?.kind !== "number" || !t?.trim() || Number.isFinite(Number(t.trim())),
        ),
    }),
    onSubmit: (v) => add(v.value),
  });
  const value = valueForm.values.value;
  const setValue = (v: string) => void valueForm.setFieldValue("value", v);
  if (!fields.data?.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label="Contact field"
        className="w-44"
        value={key}
        placeholder="Contact field…"
        options={fields.data.map((f) => ({ value: f.key, label: f.label }))}
        searchable={false}
        onChange={(e) => {
          setKey(e.target.value);
          setValue("");
        }}
      />
      {field &&
        (field.kind === "select" || field.kind === "checkbox" ? (
          <Select
            aria-label={`${field.label} value`}
            className="w-40"
            value={value}
            placeholder="Value…"
            options={(field.kind === "checkbox" ? ["true", "false"] : (field.options ?? [])).map(
              (o) => ({ value: o, label: o === "true" ? "Yes" : o === "false" ? "No" : o }),
            )}
            searchable={false}
            onChange={(e) => add(e.target.value)}
          />
        ) : field.kind === "date" ? (
          <DatePicker
            aria-label={`${field.label} value`}
            className="h-9 w-48"
            value=""
            placeholder="Pick a date…"
            onChange={add}
          />
        ) : (
          <form className="flex gap-1.5" noValidate onSubmit={valueForm.handleSubmit}>
            <Input
              aria-label={`${field.label} value`}
              className="h-9 w-40"
              type={field.kind === "number" ? "number" : "text"}
              placeholder="Value"
              {...bind(valueForm, "value")}
            />
            <Button type="submit" size="sm" variant="outline" className="h-9">
              Add
            </Button>
          </form>
        ))}
    </div>
  );
}

const AVATAR_TONES = [
  "from-indigo-500 to-violet-500",
  "from-sky-500 to-cyan-500",
  "from-emerald-500 to-teal-500",
  "from-amber-500 to-orange-500",
  "from-rose-500 to-pink-500",
  "from-fuchsia-500 to-purple-500",
];

function ContactAvatar({ name, id, className }: { name: string; id: string; className?: string }) {
  const hash = [...id].reduce((n, c) => n + c.charCodeAt(0), 0);
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]!.toUpperCase())
      .join("") || "?";
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-10 shrink-0 place-items-center rounded-full bg-gradient-to-br text-xs font-semibold text-white shadow-sm ring-2 ring-surface",
        AVATAR_TONES[hash % AVATAR_TONES.length],
        className,
      )}
    >
      {initials}
    </span>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: React.ReactNode;
  tone: string;
}) {
  return (
    <Card className="flex items-center gap-3 rounded-2xl p-4 transition-all hover:-translate-y-0.5 hover:shadow-pop">
      <span className={cn("grid size-10 shrink-0 place-items-center rounded-xl", tone)}>
        <Icon className="size-5" />
      </span>
      <div className="min-w-0">
        <p className="truncate text-xs font-medium text-muted-foreground">{label}</p>
        <p className="text-xl leading-tight font-semibold tracking-tight tabular-nums">{value}</p>
      </div>
    </Card>
  );
}

function Rows({ rows }: { rows: ContactRow[] }) {
  const router = useRouter();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="border-b border-border bg-surface-2/60 text-[11px] tracking-wider text-muted-foreground uppercase">
          <tr>
            {["Contact", "Phone", "Organization", "Tickets", "Last activity", ""].map((h, i) => (
              <th key={h || i} scope="col" className="px-5 py-3 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((c) => (
            <tr
              key={c.id}
              onClick={() => router.push(`/customers/${c.id}?from=contacts`)}
              className="group cursor-pointer transition-colors hover:bg-primary-soft/40"
            >
              <td className="px-5 py-3.5">
                <div className="flex items-center gap-3">
                  <ContactAvatar name={c.name} id={c.id} />
                  <div className="min-w-0">
                    <Link
                      href={`/customers/${c.id}?from=contacts`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1.5 font-semibold group-hover:text-primary-ink"
                    >
                      {c.name}
                      {c.vip && (
                        <Star
                          className="size-3.5 fill-amber-400 text-amber-500"
                          aria-label="VIP"
                          role="img"
                        />
                      )}
                    </Link>
                    <p className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                      <Mail className="size-3 shrink-0" /> {c.email}
                    </p>
                  </div>
                </div>
              </td>
              <td className="px-5 py-3.5 whitespace-nowrap text-muted-foreground">
                {c.phone ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Phone className="size-3.5" /> {c.phone}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-5 py-3.5 text-muted-foreground">
                {c.organization ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Building2 className="size-3.5 shrink-0" /> {c.organization}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-5 py-3.5 whitespace-nowrap">
                <span className="inline-flex items-center gap-2">
                  <span className="inline-flex items-center gap-1 tabular-nums">
                    <Ticket className="size-3.5 text-muted-foreground" /> {c.ticket_count}
                  </span>
                  {c.open_ticket_count > 0 && (
                    <Badge tone="primary">{c.open_ticket_count} open</Badge>
                  )}
                </span>
              </td>
              <td className="px-5 py-3.5 whitespace-nowrap text-muted-foreground">
                {c.last_activity_at ? timeAgo(c.last_activity_at) : "—"}
              </td>
              <td className="w-10 px-3 py-3.5 text-subtle-foreground">
                <ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5 group-hover:text-primary-ink" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ContactsView() {
  const { user } = useCurrentUser();
  const canImport = hasAction(user, "manage_contacts");
  const canExport = hasAction(user, "export_contacts");
  const canSegment = hasAction(user, "manage_segments");

  const fields = useContactFields();
  const [filters, setFilters] = useState<ContactFilters>(EMPTY_FILTERS);
  const searchForm = useFormik({
    initialValues: { text: "" },
    validationSchema: yup.object({ text: yup.string().max(200, "Use at most 200 characters.") }),
    onSubmit: () => {},
  });
  const text = searchForm.values.text;
  const setText = (v: string) => void searchForm.setFieldValue("text", v);
  const [segment, setSegment] = useState<Segment | null>(null);
  /** The segment whose filters are being changed in the list. */
  const [editing, setEditing] = useState<Segment | null>(null);
  const [importing, setImporting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const updateSegment = useSaveSegment();

  // The search box follows typing after a short pause.
  const typed = useDebounced(text, 300);
  useEffect(() => {
    setFilters((f) => (f.q === typed ? f : { ...f, q: typed }));
  }, [typed]);

  const all = useContactList(filters, segment === null);
  const seg = useSegmentContacts(segment?.id ?? null);
  const count = useSegment(segment?.id ?? null).data?.contact_count;
  const q = segment ? seg : all;
  const fetched = q.data?.pages.flatMap((p) => p.items);
  // A refused search or filter (422) keeps the old results on screen next to its message.
  const lastRows = useRef<ContactRow[]>([]);
  useEffect(() => {
    if (fetched) lastRows.current = fetched;
  }, [fetched]);
  const rows = fetched ?? (q.isError ? lastRows.current : []);
  const fieldRefs = fields.data ?? [];

  const set = (f: ContactFilters) => setFilters(f);
  const filtered = isFiltered(filters);

  const showAll = (f: ContactFilters) => {
    setSegment(null);
    setFilters(f);
    setText(f.q);
  };

  return (
    <div className="mx-auto w-full max-w-[1600px] px-3 py-5 sm:px-6 sm:py-7 lg:px-8">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight">
            Contacts <AiBadge>Customer 360</AiBadge>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Everyone who has written in, with their tickets and history in one place.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canSegment && !segment && filtered && !editing && (
            <Button variant="outline" onClick={() => setSaving(true)}>
              <Filter className="size-4" /> Save as segment
            </Button>
          )}
          {canImport && (
            <>
              <Link href="/settings/contact-fields">
                <Button variant="outline">
                  <Settings2 className="size-4" /> Fields
                </Button>
              </Link>
              <Button variant="outline" onClick={() => setImporting(true)}>
                <Upload className="size-4" /> Import CSV
              </Button>
            </>
          )}
          {canExport && (
            <Button
              variant="outline"
              loading={exporting}
              disabled={!!segment}
              title={
                segment
                  ? "Export works on the full list: choose All contacts and the same filters."
                  : "Exports what the list shows (all pages), up to 50,000 customers."
              }
              onClick={async () => {
                setExporting(true);
                try {
                  await exportContacts(filters);
                } catch (e) {
                  toast.error(msg(e));
                } finally {
                  setExporting(false);
                }
              }}
            >
              {!exporting && <Download className="size-4" />} Export CSV
            </Button>
          )}
        </div>
      </div>

      {!q.isPending && rows.some((r) => r.open_ticket_count > 0) && (
        <AiPanel title="AI insight" className="mb-5">
          {rows.filter((r) => r.open_ticket_count > 0).length} of {rows.length} customers in this
          view are waiting on a reply
          {rows.some((r) => r.vip && r.open_ticket_count > 0)
            ? `, including ${rows.filter((r) => r.vip && r.open_ticket_count > 0).length} VIP. Start with the VIPs.`
            : "."}
        </AiPanel>
      )}

      {!q.isPending && rows.length > 0 && (
        <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
          <StatTile
            icon={Users}
            label={segment ? "In segment" : "Contacts in view"}
            value={count ?? rows.length}
            tone="bg-primary-soft text-primary-ink"
          />
          <StatTile
            icon={Star}
            label="VIP in view"
            value={rows.filter((r) => r.vip).length}
            tone="bg-amber-100 text-amber-600 dark:bg-amber-500/15"
          />
          <StatTile
            icon={Ticket}
            label="With open tickets"
            value={rows.filter((r) => r.open_ticket_count > 0).length}
            tone="bg-sky-100 text-sky-600 dark:bg-sky-500/15"
          />
          <StatTile
            icon={Mail}
            label="Open tickets"
            value={rows.reduce((n, r) => n + r.open_ticket_count, 0)}
            tone="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15"
          />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[260px_1fr] xl:grid-cols-[280px_1fr]">
        <aside className="lg:sticky lg:top-4 lg:self-start">
          <SegmentsPanel
            activeId={segment?.id ?? null}
            canManage={canSegment}
            onSelect={(s) => {
              setEditing(null);
              if (s) setSegment(s);
              else showAll(EMPTY_FILTERS);
            }}
            onEditFilters={(s) => {
              setEditing(s);
              showAll(segmentToFilters(s.filters));
            }}
          />
        </aside>

        <main className="min-w-0 space-y-4">
          {editing && (
            <div
              role="status"
              className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/30 bg-primary-soft px-4 py-3 text-sm"
            >
              <span className="flex-1">
                Changing the filters of <strong>{editing.name}</strong>. Set them below, then save.
              </span>
              <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button
                size="sm"
                loading={updateSegment.isPending}
                onClick={() =>
                  updateSegment.mutate(
                    { id: editing.id, filters: filtersToSegment(filters, fieldRefs) },
                    {
                      onSuccess: (s) => {
                        toast.success("Segment updated");
                        setEditing(null);
                        setSegment(s);
                      },
                      onError: (e) => toast.error(msg(e)),
                    },
                  )
                }
              >
                Update segment
              </Button>
            </div>
          )}

          {segment ? (
            <Card className="rounded-2xl p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="mr-1 font-semibold">{segment.name}</h2>
                {describeFilters(segment.filters, fieldRefs).map((c) => (
                  <Chip key={c}>{c}</Chip>
                ))}
                {Object.keys(segment.filters).length === 0 && (
                  <span className="text-xs text-muted-foreground">No filters</span>
                )}
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {count == null ? "Counting…" : `${count} ${count === 1 ? "customer" : "customers"}`}
              </p>
            </Card>
          ) : (
            <Card className="space-y-3 rounded-2xl p-4">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-56 flex-1">
                  <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-foreground" />
                  <Input
                    aria-label="Search contacts"
                    placeholder="Search name, email, phone or organization"
                    className="pl-9"
                    maxLength={200}
                    {...bind(searchForm, "text")}
                  />
                </div>
                <Select
                  aria-label="Sort"
                  className="w-44"
                  value={filters.sort}
                  searchable={false}
                  options={(Object.keys(SORT_LABEL) as ContactSort[]).map((s) => ({
                    value: s,
                    label: SORT_LABEL[s],
                  }))}
                  onChange={(e) => set({ ...filters, sort: e.target.value as ContactSort })}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Toggle on={filters.vip} onClick={() => set({ ...filters, vip: !filters.vip })}>
                  VIP only
                </Toggle>
                <Toggle
                  on={filters.has_open}
                  onClick={() => set({ ...filters, has_open: !filters.has_open })}
                >
                  Has open tickets
                </Toggle>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span aria-hidden>Created from</span>
                  <DatePicker
                    aria-label="Created from"
                    type="date"
                    className="h-8 w-36"
                    value={filters.created_from}
                    max={filters.created_to || undefined}
                    onChange={(v) => set({ ...filters, created_from: v })}
                  />
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span aria-hidden>to</span>
                  <DatePicker
                    aria-label="Created to"
                    type="date"
                    className="h-8 w-36"
                    value={filters.created_to}
                    min={filters.created_from || undefined}
                    onChange={(v) => set({ ...filters, created_to: v })}
                  />
                </div>
              </div>
              <AttributeFilter filters={filters} onChange={set} />
              {Object.keys(filters.attributes).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(filters.attributes).map(([k, v]) => (
                    <Chip
                      key={k}
                      onRemove={() => {
                        const { [k]: _gone, ...rest } = filters.attributes;
                        void _gone;
                        set({ ...filters, attributes: rest });
                      }}
                    >
                      {fieldRefs.find((f) => f.key === k)?.label ?? k}: {v}
                    </Chip>
                  ))}
                </div>
              )}
              {filtered && (
                <Button variant="ghost" size="sm" onClick={() => showAll(EMPTY_FILTERS)}>
                  Clear filters
                </Button>
              )}
            </Card>
          )}

          {q.isError && <FormError message={msg(q.error)} />}
          {q.isPending ? (
            <Skeleton className="h-64 rounded-2xl" />
          ) : q.isError && rows.length === 0 ? null : rows.length === 0 ? (
            <Card className="rounded-2xl">
              <EmptyState title={filtered || segment ? "No customers match" : "No customers yet"}>
                {filtered || segment
                  ? "Try fewer filters."
                  : "Customers appear here when they write in or are imported."}
              </EmptyState>
            </Card>
          ) : (
            <Card className="overflow-hidden rounded-2xl">
              <Rows rows={rows} />
              <InfiniteScroll
                query={q}
                shown={rows.length}
                total={count}
                noun="contacts"
                className="border-t border-border"
              />
            </Card>
          )}
        </main>
      </div>

      <ImportDialog open={importing} onClose={() => setImporting(false)} />
      <SegmentNameDialog
        open={saving}
        filters={filters}
        fields={fieldRefs}
        onClose={() => setSaving(false)}
        onSaved={(s) => {
          setSegment(s);
        }}
      />
    </div>
  );
}
