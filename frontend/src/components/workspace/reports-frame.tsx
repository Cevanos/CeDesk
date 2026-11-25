"use client";

import {
  Activity,
  BarChart3,
  CalendarClock,
  CircleCheckBig,
  Gauge,
  Inbox,
  LayoutDashboard,
  LineChart,
  ShieldAlert,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { AiBadge } from "@/components/ui/ai";
import { DatePicker } from "@/components/ui/date-picker";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, Select, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useTeams } from "@/hooks/use-workspace";
import { withOffset, type ReportParams } from "@/hooks/workspace/reports";
import { hasAction } from "@/lib/permissions";
import { daysAgo, daysBetween, ymd, type ReportRange } from "@/lib/reports";
import { cn } from "@/lib/utils";

const PRESETS = [
  { id: "7", label: "7 days", days: 7, preset: "last_7d" },
  { id: "30", label: "30 days", days: 30, preset: "last_30d" },
  { id: "90", label: "90 days", days: 90, preset: "last_90d" },
] as const;
type PresetId = (typeof PRESETS)[number]["id"] | "custom" | "own";

const TABS: readonly { href: string; label: string; icon: LucideIcon; ai?: boolean }[] = [
  { href: "/reports/team", label: "Team", icon: BarChart3 },
  { href: "/reports/metrics", label: "More metrics", icon: Gauge },
  { href: "/reports/ai", label: "AI", icon: Sparkles, ai: true },
  { href: "/reports/explore", label: "Trends", icon: LineChart },
  { href: "/reports/backlog", label: "Backlog", icon: Inbox },
  { href: "/reports/resolutions", label: "Resolutions", icon: CircleCheckBig },
  { href: "/reports/dashboards", label: "Dashboards", icon: LayoutDashboard },
  { href: "/reports/live", label: "Live", icon: Activity },
  { href: "/reports/schedules", label: "Scheduled reports", icon: CalendarClock },
];

interface Controls {
  period: boolean;
  team: boolean;
  /** "Card's own range" in the period picker (a dashboard page only). */
  own: boolean;
}

function controlsFor(pathname: string): Controls {
  if (/^\/reports\/dashboards\/[^/]+/.test(pathname))
    return { period: true, team: true, own: true };
  if (pathname.startsWith("/reports/ai")) return { period: true, team: false, own: false };
  if (pathname.startsWith("/reports/explore") || pathname.startsWith("/reports/live"))
    return { period: false, team: true, own: false };
  if (pathname.startsWith("/reports/dashboards") || pathname.startsWith("/reports/schedules"))
    return { period: false, team: false, own: false };
  return { period: true, team: true, own: false };
}

interface Filters {
  /** The period as the older reports want it (dates in the browser's zone). */
  params: ReportParams;
  /** The same period for the new run routes; undefined means "the card's own range". */
  runRange: ReportRange | undefined;
  teamId: string;
  badRange: boolean;
}

const FiltersContext = createContext<Filters | null>(null);

export function useReportFilters(): Filters {
  const v = useContext(FiltersContext);
  if (!v) throw new Error("useReportFilters must be used inside the reports frame");
  return v;
}

export function NoReportsAccess() {
  return (
    <EmptyState icon={<ShieldAlert className="size-6" />} title="You don't have access to reports">
      Reports are for people whose role includes viewing reports.
    </EmptyState>
  );
}

/** Title, the period and team pickers and the tab bar, shared by every reports page. */
export function ReportsFrame({ children }: { children: ReactNode }) {
  const { user, perms, isPending } = useCurrentUser();
  if (isPending) return <Skeleton className="m-6 h-60" />;
  if (!hasAction(user, "reports", perms.viewReports)) return <NoReportsAccess />;
  return <FrameBody>{children}</FrameBody>;
}

function FrameBody({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user } = useCurrentUser();
  const controls = controlsFor(pathname);
  const [today] = useState(() => new Date());
  const [preset, setPreset] = useState<PresetId>("30");
  const [custom, setCustom] = useState({ from: daysAgo(today, 29), to: ymd(today) });
  const [pickedTeam, setPickedTeam] = useState("");
  const teams = useTeams();

  // A role limited to one team only ever sees that team: lock the picker to it.
  const lockedTeam = user?.access?.scope === "team_only" ? (user.access.team_id ?? "") : "";
  const teamId = lockedTeam || pickedTeam;

  // "Card's own range" only exists on a dashboard page; anywhere else it means 30 days.
  const active: PresetId = preset === "own" && !controls.own ? "30" : preset;

  const range = useMemo(() => {
    if (active === "custom") return custom;
    const days = PRESETS.find((p) => p.id === active)?.days ?? 30;
    return { from: daysAgo(today, days - 1), to: ymd(today) };
  }, [active, custom, today]);

  const badRange = !range.from || !range.to || range.from > range.to;
  const tooLong = !badRange && daysBetween(range.from, range.to) > 366;

  const value = useMemo<Filters>(() => {
    const params: ReportParams = { ...range, team_id: teamId || undefined };
    let runRange: ReportRange | undefined;
    if (active === "own") runRange = undefined;
    else if (active === "custom")
      runRange = { from: withOffset(range.from), to: withOffset(range.to, true) };
    else runRange = { preset: PRESETS.find((p) => p.id === active)?.preset ?? "last_30d" };
    return { params, runRange, teamId, badRange: badRange || tooLong };
  }, [range, teamId, active, badRange, tooLong]);

  const periodButtons: { id: PresetId; label: string }[] = [
    ...PRESETS.map((p) => ({ id: p.id as PresetId, label: p.label })),
    { id: "custom", label: "Custom" },
    ...(controls.own ? [{ id: "own" as PresetId, label: "Card's own range" }] : []),
  ];

  return (
    <FiltersContext.Provider value={value}>
      <div className="mx-auto w-full max-w-6xl animate-rise px-3 py-5 sm:px-6 sm:py-8">
        <PageHeader
          icon={BarChart3}
          title="Reports"
          badge={pathname.startsWith("/reports/ai") && <AiBadge>AI insights</AiBadge>}
          description="How the team is doing: volume, speed, SLA and customer satisfaction."
          actions={
            controls.period || controls.team ? (
              <div className="flex flex-wrap items-end gap-3">
                {controls.period && (
                  <div
                    role="group"
                    aria-label="Period"
                    className="inline-flex flex-wrap rounded-xl border border-border bg-surface-2 p-1"
                  >
                    {periodButtons.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        aria-pressed={active === p.id}
                        onClick={() => setPreset(p.id)}
                        className={cn(
                          "rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-all",
                          active === p.id
                            ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                )}
                {controls.team && (
                  <div className="w-44">
                    <Select
                      aria-label="Team"
                      value={teamId}
                      disabled={!!lockedTeam}
                      onChange={(e) => setPickedTeam(e.target.value)}
                      options={
                        lockedTeam
                          ? [{ value: lockedTeam, label: user?.access?.team_name ?? "Your team" }]
                          : [
                              { value: "", label: "All teams" },
                              ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name })),
                            ]
                      }
                    />
                  </div>
                )}
              </div>
            ) : undefined
          }
        />

        {controls.period && active === "custom" && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <label htmlFor="rep-from">From</label>
              <DatePicker
                id="rep-from"
                type="date"
                className="w-40"
                value={custom.from}
                max={custom.to || undefined}
                onChange={(v) => setCustom((c) => ({ ...c, from: v }))}
              />
            </div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <label htmlFor="rep-to">To</label>
              <DatePicker
                id="rep-to"
                type="date"
                className="w-40"
                value={custom.to}
                min={custom.from || undefined}
                onChange={(v) => setCustom((c) => ({ ...c, to: v }))}
              />
            </div>
            {badRange && (
              <p role="alert" className="text-xs text-danger">
                Pick a start date that is not after the end date.
              </p>
            )}
            {tooLong && (
              <p role="alert" className="text-xs text-danger">
                A report covers at most 366 days.
              </p>
            )}
          </div>
        )}

        <nav
          aria-label="Report"
          className="mt-5 flex max-w-full [scrollbar-width:none] overflow-x-auto rounded-xl border border-border bg-surface-2 p-1 sm:inline-flex [&::-webkit-scrollbar]:hidden"
        >
          {TABS.map((t) => {
            const current = pathname === t.href || pathname.startsWith(`${t.href}/`);
            return (
              <Link
                key={t.href}
                href={t.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap transition-all",
                  current
                    ? "bg-surface text-foreground shadow-card ring-1 ring-border"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <t.icon
                  className={cn(
                    "size-3.5",
                    t.ai && "text-ai-via",
                    current && !t.ai && "text-primary-ink",
                  )}
                  aria-hidden
                />
                {t.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-6" aria-live="polite">
          {children}
        </div>
      </div>
    </FiltersContext.Provider>
  );
}
