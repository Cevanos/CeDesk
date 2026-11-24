import {
  Archive,
  BarChart3,
  Bell,
  Check,
  Clock,
  Inbox,
  Megaphone,
  Paperclip,
  Search,
  Send,
  Settings,
  Sparkles,
  UserRound,
  Users,
  Zap,
} from "lucide-react";

import { LogoMark } from "@/components/ui/brand";
import { cn } from "@/lib/utils";

/**
 * Product mockups drawn in code instead of screenshots: vector-sharp at any size, they follow
 * the light/dark theme and use only made-up demo data. Everything inside is sized in `em`, and
 * the frame sets its font size from its own width (`cqw`), so a mockup scales as one picture.
 */

function Frame({
  label,
  className,
  scale = 1.25,
  children,
}: {
  label: string;
  className?: string;
  /** Font size as a share of the frame width; smaller means more content fits. */
  scale?: number;
  children: React.ReactNode;
}) {
  return (
    <div
      role="img"
      aria-label={label}
      className={cn(
        "[container-type:inline-size] overflow-hidden rounded-2xl border border-border-strong bg-surface shadow-hero",
        className,
      )}
    >
      <div style={{ fontSize: `${scale}cqw` }} className="text-foreground select-none">
        <div className="flex items-center gap-[.5em] border-b border-border bg-surface-2 px-[1.2em] py-[.8em]">
          <span className="size-[.8em] rounded-full bg-[#ff6159]/80" />
          <span className="size-[.8em] rounded-full bg-[#ffbd2e]/80" />
          <span className="size-[.8em] rounded-full bg-[#28c940]/80" />
        </div>
        {children}
      </div>
    </div>
  );
}

function Pill({
  tone,
  children,
}: {
  tone: "danger" | "warning" | "primary" | "success" | "neutral" | "info";
  children: React.ReactNode;
}) {
  const t = {
    danger: "bg-danger-soft text-danger",
    warning: "bg-warning-soft text-warning",
    primary: "bg-primary-soft text-primary-ink",
    success: "bg-success-soft text-success",
    info: "bg-info-soft text-info",
    neutral: "bg-surface-3 text-muted-foreground",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-[.3em] rounded-full px-[.7em] py-[.15em] text-[.8em] font-semibold whitespace-nowrap",
        t,
      )}
    >
      {children}
    </span>
  );
}

function Avatar({ children, tone = "primary" }: { children: string; tone?: "primary" | "info" }) {
  return (
    <span
      className={cn(
        "grid size-[2.4em] shrink-0 place-items-center rounded-full text-[.8em] font-semibold",
        tone === "primary" ? "bg-primary-soft text-primary-ink" : "bg-info-soft text-info",
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Inbox
 * ------------------------------------------------------------------ */

const NAV = [
  { icon: Inbox, label: "Tickets", active: true },
  { icon: Users, label: "Contacts" },
  { icon: UserRound, label: "Agents" },
  { icon: Archive, label: "Archive" },
  { icon: Megaphone, label: "Campaigns" },
  { icon: BarChart3, label: "Reports" },
  { icon: Settings, label: "Settings" },
];

const TICKETS = [
  {
    id: "T-1042",
    pri: ["danger", "Urgent"],
    subject: "Refund for damaged T-shirt (order #1042)",
    who: "Maya Patel",
    ch: "Email",
    ai: true,
    time: "6m ago",
    bar: "bg-danger",
  },
  {
    id: "T-1041",
    pri: ["warning", "High"],
    subject: "Where is my order #1077?",
    who: "Liam Chen",
    ch: "Chat",
    ai: true,
    time: "22m ago",
    bar: "bg-warning",
  },
  {
    id: "T-1039",
    pri: ["info", "Medium"],
    subject: "Change the delivery address on my subscription",
    who: "Sofia Rossi",
    ch: "Web form",
    ai: false,
    time: "1h ago",
    bar: "bg-info",
  },
  {
    id: "T-1036",
    pri: ["neutral", "Low"],
    subject: "Do you ship to Norway?",
    who: "Noah Kim",
    ch: "Portal",
    ai: false,
    time: "3h ago",
    bar: "bg-border-strong",
  },
] as const;

export function InboxMockup({ className }: { className?: string }) {
  return (
    <Frame
      label="The CeDesk ticket inbox, showing counts and a prioritised list"
      className={className}
    >
      <div className="flex">
        <aside className="hidden w-[15em] shrink-0 flex-col border-r border-border bg-surface p-[1em] sm:flex">
          <div className="flex items-center gap-[.7em] px-[.4em] py-[.4em]">
            <LogoMark className="size-[2.2em] rounded-[.5em]" />
            <span className="text-[1.2em] font-semibold tracking-tight">
              Ce<span className="text-primary-ink">Desk</span>
            </span>
          </div>
          <div className="mt-[1em] rounded-[.7em] bg-primary py-[.75em] text-center text-[1em] font-semibold text-primary-foreground shadow-[0_.4em_1em_-.3em_var(--primary)]">
            + New ticket
          </div>
          <ul className="mt-[1.2em] space-y-[.2em]">
            {NAV.map(({ icon: Icon, label, active }) => (
              <li
                key={label}
                className={cn(
                  "flex items-center gap-[.8em] rounded-[.6em] px-[.8em] py-[.65em] font-medium",
                  active ? "bg-primary-soft text-primary-ink" : "text-muted-foreground",
                )}
              >
                <Icon className="size-[1.3em]" />
                {label}
              </li>
            ))}
          </ul>
          <div className="mt-auto flex items-center gap-[.7em] border-t border-border pt-[1em]">
            <Avatar>A</Avatar>
            <div className="leading-tight">
              <p className="font-semibold">Admin</p>
              <p className="text-[.8em] text-muted-foreground">Admin · Acme Store</p>
            </div>
          </div>
        </aside>

        <div className="min-w-0 flex-1 bg-background p-[1.6em]">
          <div className="flex items-start justify-between gap-[1em]">
            <div>
              <p className="text-[1.9em] leading-none font-semibold tracking-tight">Tickets</p>
              <p className="mt-[.5em] text-muted-foreground">
                Every conversation, human and AI, in one queue.
              </p>
            </div>
            <div className="flex items-center gap-[.8em]">
              <div className="hidden items-center gap-[.6em] rounded-[.7em] border border-border bg-surface px-[1em] py-[.6em] text-muted-foreground md:flex">
                <Search className="size-[1.1em]" /> Search or jump to…
              </div>
              <Bell className="size-[1.3em] text-muted-foreground" />
              <Avatar>A</Avatar>
            </div>
          </div>

          <div className="mt-[1.4em] grid grid-cols-4 gap-[1em]">
            {[
              ["203", "Open", "Needs attention", "bg-primary-soft text-primary-ink", true],
              ["23", "Pending", "Waiting on someone", "bg-warning-soft text-warning", false],
              ["68", "Resolved", "All done", "bg-success-soft text-success", false],
              ["2", "Snoozed", "Hidden until later", "bg-info-soft text-info", false],
            ].map(([n, l, s, c, on]) => (
              <div
                key={l as string}
                className={cn(
                  "rounded-[.9em] border bg-surface p-[1em]",
                  on
                    ? "border-primary/50 shadow-[0_0_0_.2em_var(--primary-soft)]"
                    : "border-border",
                )}
              >
                <span
                  className={cn("grid size-[2.4em] place-items-center rounded-[.6em]", c as string)}
                >
                  <Check className="size-[1.2em]" />
                </span>
                <p className="mt-[.6em] text-[1.9em] leading-none font-semibold tracking-tight">
                  {n}
                </p>
                <p className="mt-[.3em] font-medium">{l}</p>
                <p className="hidden text-[.85em] text-muted-foreground md:block">{s}</p>
              </div>
            ))}
          </div>

          <ul className="mt-[1.4em] overflow-hidden rounded-[.9em] border border-border bg-surface">
            {TICKETS.map((t) => (
              <li
                key={t.id}
                className="relative flex items-center gap-[1em] border-b border-border py-[1em] pr-[1.2em] pl-[1.4em] last:border-0"
              >
                <span className={cn("absolute inset-y-0 left-0 w-[.3em]", t.bar)} />
                <Avatar>
                  {t.who
                    .split(" ")
                    .map((w) => w[0])
                    .join("")}
                </Avatar>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-[.5em]">
                    <span className="font-mono text-[.85em] text-muted-foreground">{t.id}</span>
                    <Pill tone={t.pri[0]}>{t.pri[1]}</Pill>
                    {t.ai && (
                      <Pill tone="primary">
                        <Sparkles className="size-[1em]" /> AI handover
                      </Pill>
                    )}
                  </div>
                  <p className="mt-[.3em] truncate text-[1.1em] font-semibold">{t.subject}</p>
                  <p className="text-[.9em] text-muted-foreground">
                    {t.who} · {t.ch}
                  </p>
                </div>
                <span className="hidden text-[.85em] text-muted-foreground sm:block">{t.time}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Frame>
  );
}

/* ------------------------------------------------------------------ *
 * Ticket with an AI handover
 * ------------------------------------------------------------------ */

export function HandoffMockup({ className }: { className?: string }) {
  return (
    <Frame
      label="A ticket where the AI handed over to a person, with a summary, the reason and the customer's sentiment"
      className={className}
      scale={1.45}
    >
      <div className="flex bg-background">
        <div className="min-w-0 flex-1 space-y-[1em] p-[1.4em]">
          <div className="flex items-center gap-[.6em]">
            <span className="font-mono text-[.85em] text-muted-foreground">T-1042</span>
            <Pill tone="danger">Urgent</Pill>
            <Pill tone="success">Open</Pill>
          </div>
          <p className="text-[1.4em] leading-tight font-semibold tracking-tight">
            Refund for damaged T-shirt
          </p>

          <div className="flex gap-[.7em]">
            <Avatar>MP</Avatar>
            <div className="rounded-[.9em] rounded-tl-[.2em] border border-border bg-surface px-[1em] py-[.8em]">
              <p className="text-[.8em] font-semibold text-muted-foreground">Maya Patel</p>
              <p className="mt-[.2em]">
                My T-shirt arrived torn. I want my money back, not a swap.
              </p>
            </div>
          </div>

          <div className="flex gap-[.7em]">
            <span className="grid size-[2.4em] shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
              <Sparkles className="size-[1.1em]" />
            </span>
            <div className="rounded-[.9em] rounded-tl-[.2em] bg-primary-soft px-[1em] py-[.8em] text-primary-ink">
              <p className="text-[.8em] font-semibold">Cevanos AI</p>
              <p className="mt-[.2em]">
                I’m sorry about that. A refund needs approval, so I’m bringing in a teammate.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-[.8em] text-[.8em] font-semibold tracking-wide text-muted-foreground uppercase">
            <span className="h-px flex-1 bg-border" /> Handed over to a person
            <span className="h-px flex-1 bg-border" />
          </div>

          <div className="rounded-[1em] border border-primary/30 bg-surface p-[1em] shadow-[0_.6em_1.6em_-.8em_var(--primary)]">
            <p className="flex items-center gap-[.5em] font-semibold text-primary-ink">
              <Zap className="size-[1.1em]" /> AI handover
            </p>
            <dl className="mt-[.7em] space-y-[.55em]">
              {[
                ["Summary", "Damaged item, wants a refund. Order #1042 shipped last week."],
                ["Reason", "Refunds above the limit need a person to approve."],
              ].map(([k, v]) => (
                <div key={k} className="grid grid-cols-[5em_1fr] gap-[.6em]">
                  <dt className="text-[.85em] font-semibold text-muted-foreground uppercase">
                    {k}
                  </dt>
                  <dd>{v}</dd>
                </div>
              ))}
              <div className="grid grid-cols-[5em_1fr] items-center gap-[.6em]">
                <dt className="text-[.85em] font-semibold text-muted-foreground uppercase">Mood</dt>
                <dd>
                  <Pill tone="warning">Frustrated</Pill>
                </dd>
              </div>
            </dl>
          </div>

          <div className="flex items-center gap-[.8em] rounded-[.9em] border border-border bg-surface px-[1em] py-[.8em]">
            <p className="flex-1 text-muted-foreground">Reply to Maya…</p>
            <Paperclip className="size-[1.2em] text-muted-foreground" />
            <span className="inline-flex items-center gap-[.4em] rounded-[.6em] bg-primary px-[1em] py-[.5em] font-semibold text-primary-foreground">
              <Send className="size-[1em]" /> Send
            </span>
          </div>
        </div>

        <aside className="hidden w-[15em] shrink-0 border-l border-border bg-surface p-[1.2em] md:block">
          <p className="text-[.8em] font-semibold tracking-wide text-muted-foreground uppercase">
            Properties
          </p>
          <dl className="mt-[.8em] space-y-[.9em]">
            {[
              ["Assignee", "Admin"],
              ["Team", "Billing"],
              ["Channel", "AI chat"],
              ["Type", "Refund"],
            ].map(([k, v]) => (
              <div key={k}>
                <dt className="text-[.8em] text-muted-foreground">{k}</dt>
                <dd className="font-medium">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-[1.2em] rounded-[.8em] bg-warning-soft p-[.9em] text-warning">
            <p className="flex items-center gap-[.4em] text-[.85em] font-semibold">
              <Clock className="size-[1em]" /> First response
            </p>
            <p className="mt-[.2em] text-[1.3em] font-semibold">1h 12m left</p>
          </div>
        </aside>
      </div>
    </Frame>
  );
}

/* ------------------------------------------------------------------ *
 * Rules
 * ------------------------------------------------------------------ */

function Block({
  label,
  tone,
  children,
}: {
  label: string;
  tone: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative rounded-[1em] border border-border bg-surface p-[1em]">
      <span
        className={cn(
          "absolute -top-[.7em] left-[1em] rounded-full px-[.8em] py-[.1em] text-[.8em] font-bold tracking-wide uppercase",
          tone,
        )}
      >
        {label}
      </span>
      <div className="mt-[.3em] flex flex-wrap gap-[.5em]">{children}</div>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-[.6em] border border-border bg-surface-2 px-[.8em] py-[.4em] font-medium">
      {children}
    </span>
  );
}

export function RulesMockup({ className }: { className?: string }) {
  return (
    <Frame
      label="A rule that escalates urgent tickets from VIP customers"
      className={className}
      scale={1.6}
    >
      <div className="space-y-[1.4em] bg-background p-[1.6em]">
        <div className="flex items-center justify-between">
          <p className="text-[1.3em] font-semibold tracking-tight">Escalate VIP urgents</p>
          <Pill tone="success">
            <span className="size-[.6em] rounded-full bg-success" /> Active
          </Pill>
        </div>
        <Block label="When" tone="bg-info-soft text-info">
          <Chip>Ticket is created</Chip>
          <Chip>or priority changes</Chip>
        </Block>
        <Block label="If" tone="bg-warning-soft text-warning">
          <Chip>Priority is Urgent</Chip>
          <Chip>Customer tag is VIP</Chip>
        </Block>
        <Block label="Then" tone="bg-success-soft text-success">
          <Chip>Assign to Escalations</Chip>
          <Chip>Notify supervisors</Chip>
          <Chip>Set SLA to 1h</Chip>
        </Block>
        <p className="flex items-center gap-[.5em] text-[.9em] text-muted-foreground">
          <Check className="size-[1.1em] text-success" /> Tested on a real ticket · last run 2
          minutes ago
        </p>
      </div>
    </Frame>
  );
}

/* ------------------------------------------------------------------ *
 * Reports
 * ------------------------------------------------------------------ */

export function ReportsMockup({ className }: { className?: string }) {
  const pts = [34, 40, 31, 46, 52, 44, 58, 63, 55, 70, 74, 69];
  const w = 360;
  const h = 120;
  const xy = pts.map((p, i) => [(i / (pts.length - 1)) * w, h - (p / 80) * h] as const);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const last = xy[xy.length - 1] ?? [w, h];
  const bars = [
    ["Billing", 82],
    ["Shipping", 64],
    ["Product", 48],
    ["Returns", 36],
  ] as const;
  return (
    <Frame
      label="A team report with resolved tickets over time and workload by team"
      className={className}
      scale={1.5}
    >
      <div className="space-y-[1.2em] bg-background p-[1.4em]">
        <div className="grid grid-cols-3 gap-[1em]">
          {[
            ["First response", "9m", "−18%"],
            ["Resolution", "3.2h", "−11%"],
            ["CSAT", "4.7", "+0.3"],
          ].map(([k, v, d]) => (
            <div key={k} className="rounded-[.9em] border border-border bg-surface p-[1em]">
              <p className="text-[.85em] text-muted-foreground">{k}</p>
              <p className="mt-[.2em] text-[1.8em] leading-none font-semibold tracking-tight">
                {v}
              </p>
              <p className="mt-[.4em] text-[.85em] font-semibold text-success">{d}</p>
            </div>
          ))}
        </div>

        <div className="rounded-[.9em] border border-border bg-surface p-[1em]">
          <p className="font-semibold">Resolved tickets</p>
          <svg viewBox={`0 0 ${w} ${h + 6}`} className="mt-[.6em] w-full" aria-hidden>
            <defs>
              <linearGradient id="rm-area" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="var(--primary)" stopOpacity="0.35" />
                <stop offset="1" stopColor="var(--primary)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[0.25, 0.5, 0.75].map((g) => (
              <line
                key={g}
                x1="0"
                x2={w}
                y1={h * g}
                y2={h * g}
                stroke="var(--border)"
                strokeWidth="1"
              />
            ))}
            <path d={`${line} L${w} ${h} L0 ${h} Z`} fill="url(#rm-area)" />
            <path
              d={line}
              fill="none"
              stroke="var(--primary)"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle
              cx={last[0]}
              cy={last[1]}
              r="4.5"
              fill="var(--primary)"
              stroke="var(--surface)"
              strokeWidth="2"
            />
          </svg>
        </div>

        <div className="rounded-[.9em] border border-border bg-surface p-[1em]">
          <p className="font-semibold">Open by team</p>
          <ul className="mt-[.7em] space-y-[.7em]">
            {bars.map(([n, v]) => (
              <li key={n} className="grid grid-cols-[5em_1fr_2em] items-center gap-[.8em]">
                <span className="text-muted-foreground">{n}</span>
                <span className="h-[.8em] overflow-hidden rounded-full bg-surface-3">
                  <span
                    className="block h-full rounded-full bg-gradient-to-r from-primary to-[#2f7bff]"
                    style={{ width: `${v}%` }}
                  />
                </span>
                <span className="text-right font-semibold tabular-nums">{v}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Frame>
  );
}

/* ------------------------------------------------------------------ *
 * Help center
 * ------------------------------------------------------------------ */

export function HelpMockup({ className }: { className?: string }) {
  return (
    <Frame
      label="A public help center with search and article categories"
      className={className}
      scale={1.5}
    >
      <div className="bg-background">
        <div className="bg-nav px-[1.6em] py-[2em] text-center text-white">
          <p className="text-[1.6em] font-semibold tracking-tight">How can we help?</p>
          <div className="mx-auto mt-[1em] flex max-w-[26em] items-center gap-[.7em] rounded-[.8em] bg-white px-[1em] py-[.8em] text-[#64708a]">
            <Search className="size-[1.2em]" /> Search articles
          </div>
        </div>
        <div className="grid grid-cols-3 gap-[1em] p-[1.4em]">
          {["Orders", "Shipping", "Returns", "Payments", "Account", "Products"].map((c, i) => (
            <div key={c} className="rounded-[.9em] border border-border bg-surface p-[1em]">
              <span className="grid size-[2.2em] place-items-center rounded-[.6em] bg-primary-soft text-primary-ink">
                <Sparkles className="size-[1.1em]" />
              </span>
              <p className="mt-[.6em] font-semibold">{c}</p>
              <p className="text-[.85em] text-muted-foreground">{4 + i * 2} articles</p>
            </div>
          ))}
        </div>
        <ul className="mx-[1.4em] mb-[1.4em] divide-y divide-border rounded-[.9em] border border-border bg-surface">
          {["Track my order", "Start a return", "Change my address"].map((a) => (
            <li key={a} className="flex items-center justify-between px-[1em] py-[.8em]">
              {a}
              <span className="text-[.85em] text-muted-foreground">Helpful · 96%</span>
            </li>
          ))}
        </ul>
      </div>
    </Frame>
  );
}
