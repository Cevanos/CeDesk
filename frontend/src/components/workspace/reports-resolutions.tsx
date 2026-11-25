"use client";

import { ArrowDown, ArrowUp, CircleCheckBig, UserRound, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { SectionHeader } from "@/components/ui/section";
import { DataTable, RowTh, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { LoadError } from "@/components/workspace/list-shared";
import { GetCsvButton } from "@/components/workspace/report-csv";
import { useReportFilters } from "@/components/workspace/reports-frame";
import { resolutionsQuery, useResolutionsReport } from "@/hooks/workspace/receipts";
import type { ResolutionCounts, ResolutionRow } from "@/lib/api/differentiator-types";

const pct = (n: number, of: number) => (of > 0 ? `${Math.round((n / of) * 100)}%` : "–");

const COLS: { key: keyof ResolutionCounts; label: string }[] = [
  { key: "resolved", label: "Resolved" },
  { key: "with_receipt", label: "With receipt" },
  { key: "verified", label: "Verified" },
  { key: "verified_agent", label: "By agent" },
  { key: "verified_shopify", label: "Shopify" },
  { key: "verified_stripe", label: "Stripe" },
  { key: "pending", label: "Pending" },
  { key: "notified", label: "Notified" },
];

/** "Resolutions" tab: how many resolved tickets carry a receipt, and how many were checked. */
export function ResolutionsTab() {
  const { params, badRange } = useReportFilters();
  const q = useResolutionsReport(params);
  if (badRange) return null;
  if (q.isPending) return <Skeleton className="h-72 rounded-xl" />;
  if (q.isError) return <LoadError error={q.error} />;
  const { total, teams, agents } = q.data;

  if (total.resolved === 0) {
    return (
      <Card>
        <EmptyState title="No resolved tickets in this period">
          Pick a wider range, or turn on resolution receipts in Settings.
        </EmptyState>
      </Card>
    );
  }

  const tiles = [
    { label: "Resolved", value: String(total.resolved), sub: null },
    {
      label: "With receipt",
      value: String(total.with_receipt),
      sub: pct(total.with_receipt, total.resolved),
    },
    { label: "Verified", value: String(total.verified), sub: pct(total.verified, total.resolved) },
    {
      label: "Pending item",
      value: String(total.pending),
      sub: pct(total.pending, total.resolved),
    },
    {
      label: "Customer notified",
      value: String(total.notified),
      sub: pct(total.notified, total.resolved),
    },
  ];

  return (
    <div className="mt-6 space-y-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Verified: {total.verified_agent} by agent, {total.verified_shopify} with Shopify,{" "}
          {total.verified_stripe} with Stripe.
        </p>
        <GetCsvButton
          path="reports/resolutions"
          query={resolutionsQuery(params)}
          fallback="cedesk-resolutions.csv"
        />
      </div>
      <section aria-label="Summary">
        <SectionHeader
          icon={CircleCheckBig}
          title="Summary"
          description="How resolutions were closed out."
        />
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {tiles.map((t) => (
            <li key={t.label}>
              <Card className="p-4">
                <p className="text-xs text-muted-foreground">{t.label}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">{t.value}</p>
                {t.sub && <p className="text-xs text-muted-foreground">{t.sub} of resolved</p>}
              </Card>
            </li>
          ))}
        </ul>
      </section>
      <RowsTable title="By team" rows={teams} />
      <RowsTable title="By agent" rows={agents} />
    </div>
  );
}

function RowsTable({ title, rows }: { title: string; rows: ResolutionRow[] }) {
  const [sort, setSort] = useState<{ key: keyof ResolutionCounts | "name"; dir: 1 | -1 }>({
    key: "resolved",
    dir: -1,
  });
  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) =>
        sort.key === "name"
          ? a.name.localeCompare(b.name) * sort.dir
          : (a[sort.key] - b[sort.key]) * sort.dir,
      ),
    [rows, sort],
  );
  const head = (key: typeof sort.key, label: string, right = true) => {
    const on = sort.key === key;
    return (
      <Th right={right} aria-sort={on ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
        <button
          type="button"
          onClick={() =>
            setSort({ key, dir: on ? (sort.dir === 1 ? -1 : 1) : key === "name" ? 1 : -1 })
          }
          className="inline-flex items-center gap-1 hover:text-foreground"
        >
          {label}
          {on &&
            (sort.dir === 1 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
        </button>
      </Th>
    );
  };

  return (
    <section aria-label={title}>
      <SectionHeader
        icon={title === "By team" ? Users : UserRound}
        title={title}
        description={
          title === "By team"
            ? "Resolved tickets and receipts per team."
            : "Resolved tickets and receipts per agent."
        }
      />
      <Card>
        {rows.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">Nothing to show.</p>
        ) : (
          <DataTable minWidth="40rem">
            <THead>
              <tr>
                {head("name", title.replace("By ", ""), false)}
                {COLS.map((c) => head(c.key, c.label))}
              </tr>
            </THead>
            <TBody>
              {sorted.map((r) => (
                <Tr key={r.id}>
                  <RowTh>{r.name}</RowTh>
                  {COLS.map((c) => (
                    <Td key={c.key} right>
                      {r[c.key]}
                      {c.key === "with_receipt" || c.key === "verified" ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          {pct(r[c.key], r.resolved)}
                        </span>
                      ) : null}
                    </Td>
                  ))}
                </Tr>
              ))}
            </TBody>
          </DataTable>
        )}
      </Card>
    </section>
  );
}
