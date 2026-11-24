import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Check,
  FileClock,
  GitBranch,
  Globe2,
  Inbox,
  KeyRound,
  LifeBuoy,
  LockKeyhole,
  Megaphone,
  Route,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Timer,
  Users,
  Workflow,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import {
  HandoffMockup,
  HelpMockup,
  InboxMockup,
  ReportsMockup,
  RulesMockup,
} from "@/components/landing/mockups";
import { ResourcesMenu } from "@/components/layout/resources-menu";
import { Logo, ThemeToggle } from "@/components/ui/brand";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Building blocks
 * ------------------------------------------------------------------ */

const GRADIENT_TEXT =
  "bg-gradient-to-r from-primary-ink via-primary to-[#2f7bff] bg-clip-text text-transparent";
const GRID_BG =
  "bg-[linear-gradient(var(--border)_1px,transparent_1px),linear-gradient(90deg,var(--border)_1px,transparent_1px)] bg-[size:56px_56px]";

function LinkButton({
  href,
  variant = "primary",
  size = "lg",
  className,
  children,
}: {
  href: string;
  variant?: "primary" | "outline" | "ghost" | "light" | "glass";
  size?: "md" | "lg";
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-xl font-medium whitespace-nowrap transition-all duration-200 active:scale-[0.98]",
        size === "lg" ? "h-12 px-7 text-[15px]" : "h-10 px-4 text-sm",
        variant === "primary" &&
          "bg-primary text-primary-foreground shadow-[0_1px_0_rgb(255_255_255/0.3)_inset,0_12px_28px_-10px_var(--primary)] hover:-translate-y-0.5 hover:bg-primary-hover",
        variant === "outline" &&
          "border border-border-strong bg-surface text-foreground hover:-translate-y-0.5 hover:bg-surface-2",
        variant === "ghost" && "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
        variant === "light" &&
          "bg-white text-[#1a1240] shadow-[0_12px_30px_-12px_rgb(0_0_0/0.5)] hover:-translate-y-0.5 hover:bg-white/90",
        variant === "glass" &&
          "border border-white/20 bg-white/5 text-white backdrop-blur hover:bg-white/10",
        className,
      )}
    >
      {children}
    </Link>
  );
}

function Eyebrow({ children, dark }: { children: React.ReactNode; dark?: boolean }) {
  return (
    <p
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs font-semibold tracking-[0.12em] uppercase",
        dark
          ? "border-white/15 bg-white/5 text-[#c9bfff]"
          : "border-primary/20 bg-primary-soft text-primary-ink",
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </p>
  );
}

function SectionHead({
  eyebrow,
  title,
  lead,
  align = "center",
  dark,
}: {
  eyebrow: string;
  title: React.ReactNode;
  lead?: string;
  align?: "center" | "left";
  dark?: boolean;
}) {
  return (
    <div className={cn("max-w-2xl", align === "center" && "mx-auto text-center")}>
      <Eyebrow dark={dark}>{eyebrow}</Eyebrow>
      <h2 className="mt-4 text-[1.85rem] leading-[1.1] font-semibold tracking-[-0.03em] text-balance sm:text-[2.45rem]">
        {title}
      </h2>
      {lead && (
        <p
          className={cn(
            "mt-3 text-base leading-relaxed text-pretty sm:text-[1.05rem]",
            dark ? "text-white/70" : "text-muted-foreground",
          )}
        >
          {lead}
        </p>
      )}
    </div>
  );
}

/** A soft coloured glow that sits behind a mockup. */
function Glow({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none absolute rounded-full blur-3xl", className)}
    />
  );
}

function Floating({
  className,
  icon: Icon,
  title,
  sub,
  tone,
}: {
  className?: string;
  icon: LucideIcon;
  title: string;
  sub: string;
  tone: string;
}) {
  return (
    <div
      aria-hidden
      className={cn(
        "absolute z-10 hidden items-center gap-3 rounded-2xl border border-border-strong bg-surface/90 px-4 py-3 shadow-pop backdrop-blur-xl lg:flex",
        className,
      )}
    >
      <span className={cn("grid size-10 place-items-center rounded-xl", tone)}>
        <Icon className="size-5" />
      </span>
      <span className="text-left leading-tight">
        <span className="block text-sm font-semibold">{title}</span>
        <span className="block text-xs text-muted-foreground">{sub}</span>
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Content
 * ------------------------------------------------------------------ */

const NAV = [
  { href: "#features", label: "Features" },
  { href: "#ai", label: "AI handoff" },
  { href: "#automation", label: "Automation" },
  { href: "#security", label: "Security" },
  { href: "#faq", label: "FAQ" },
];

const SMALL_FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Route,
    title: "Smart routing",
    body: "Teams, skills and capacity decide who gets a ticket, and availability is respected.",
  },
  {
    icon: Users,
    title: "Customers, not just tickets",
    body: "Profiles with history, notes, custom fields, segments, import and merge.",
  },
  {
    icon: Megaphone,
    title: "Campaigns with consent",
    body: "Send to a segment, honour suppressions, one-click unsubscribe on every email.",
  },
  {
    icon: BookOpen,
    title: "Help center and portal",
    body: "Articles, a contact form and ticket tracking for customers, with no agent login.",
  },
  {
    icon: BarChart3,
    title: "Reports you can act on",
    body: "Team performance, backlog ageing, live queue, dashboards and scheduled reports.",
  },
  {
    icon: Workflow,
    title: "Rules and macros",
    body: "Automate triage, tagging and escalation. Test a rule on a real ticket first.",
  },
];

const STATS = [
  ["4", "roles, from light agent to admin"],
  ["SSO + 2FA", "for every workspace"],
  ["Light & dark", "fully responsive themes"],
  ["0 logins", "needed by your customers"],
];

const AI_POINTS = [
  ["Summary", "What happened, written for the next person"],
  ["Reason", "Why the AI handed over, in plain words"],
  ["Sentiment", "How the customer feels, so you open the right way"],
  ["Transcript", "The full conversation one click away. Nothing is re-asked."],
];

const STEPS = [
  {
    icon: Inbox,
    title: "Customers reach you anywhere",
    body: "Email, web form, portal, AI chat and imported history all land in one inbox.",
  },
  {
    icon: Sparkles,
    title: "AI resolves, people take over",
    body: "Cevanos AI answers what it can. When it can’t, the ticket arrives with context.",
  },
  {
    icon: Timer,
    title: "Rules protect your SLAs",
    body: "Skills, teams and targets decide who works what, and when it escalates.",
  },
  {
    icon: BarChart3,
    title: "You learn and improve",
    body: "CSAT, reports and article votes show where to invest next.",
  },
];

const AUTOMATION = [
  { icon: Zap, label: "Rules on ticket events and schedules" },
  { icon: ScrollText, label: "Macros and saved replies for the common 80%" },
  { icon: Timer, label: "Auto-close idle tickets, with a warning first" },
  { icon: Workflow, label: "Approvals and tasks inside a ticket" },
  { icon: LifeBuoy, label: "Side conversations with colleagues or vendors" },
  { icon: FileClock, label: "A full log of every automated action" },
];

const ROLES = [
  { name: "Light agent", note: "Comment and collaborate" },
  { name: "Agent", note: "Own and resolve conversations" },
  { name: "Supervisor", note: "Watch queues, reports and SLAs" },
  { name: "Admin", note: "Members, rules and security settings" },
];

const SECURITY: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: LockKeyhole,
    title: "Session token never touches JavaScript",
    body: "Sign-in uses an httpOnly cookie, and the backend address is hidden behind a same-origin proxy.",
  },
  {
    icon: ShieldCheck,
    title: "Strict CSP, per-request nonce",
    body: "Scripts run by nonce, framing is denied, and the browser only talks to its own origin.",
  },
  {
    icon: KeyRound,
    title: "SSO, two-factor and API keys",
    body: "Single sign-on per workspace, authenticator-app 2FA and API keys you can revoke.",
  },
  {
    icon: ScrollText,
    title: "Audit log and role checks",
    body: "Sensitive changes are recorded. The UI hides what a role can’t do and the API enforces it.",
  },
];

const FOOTER_LINKS: [string, string][] = [
  ["/login", "Sign in"],
  ["#features", "Features"],
  ["/signup", "Create a workspace"],
  ["#security", "Security"],
  ["/forgot-password", "Reset password"],
  ["#faq", "FAQ"],
];

export const FAQ = [
  {
    q: "What is CeDesk?",
    a: "CeDesk is a help desk where people and AI resolve customer conversations together. It gives your team a shared inbox, SLAs, routing, automation, a customer portal and reports in one product.",
  },
  {
    q: "How does the AI hand over to a person?",
    a: "Cevanos AI can answer customers first. When it needs a human, it creates a ticket that carries a summary, the reason for the handover and the customer’s sentiment, so nobody asks the customer to repeat themselves.",
  },
  {
    q: "Can customers use it without an account?",
    a: "Yes. The help center, contact form, portal, ticket view and satisfaction rating all work from a link, with no agent login.",
  },
  {
    q: "Can I move over from another help desk?",
    a: "Yes. Settings > Imports brings tickets across from another help desk, and contacts can be imported from a file.",
  },
  {
    q: "Is it open source?",
    a: "The CeDesk frontend is MIT licensed, so you can run and change it yourself. The CeDesk name and logo are trademarks of Cevanos, and Cevanos AI and managed cloud hosting are separate commercial products.",
  },
  {
    q: "What features does the CeDesk ticketing system include?",
    a: "A shared inbox with saved views and bulk actions, SLA management, smart ticket routing by team, skill and capacity, rules, macros and workflows, customer profiles, email campaigns, a help center and customer portal, CSAT surveys, reports and dashboards, SSO, two-factor sign-in and an audit log.",
  },
  {
    q: "Can I self-host this help desk software?",
    a: "Yes. The frontend is open source under the MIT license and ships with a Dockerfile. Point it at your CeDesk API with a few environment variables and run it on your own infrastructure.",
  },
  {
    q: "Does it work on phones and in dark mode?",
    a: "Yes to both. The layout adapts down to small screens, and the theme follows your system or your choice of light or dark.",
  },
];

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export function LandingPage() {
  return (
    <div className="relative">
      {/* Nav */}
      <header className="sticky top-0 z-50 border-b border-border/60 bg-background/75 backdrop-blur-2xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-5 sm:px-8">
          <Link href="/" aria-label="CeDesk home">
            <Logo />
          </Link>
          <nav
            aria-label="Primary"
            className="hidden items-center gap-1 rounded-full border border-border bg-surface/70 p-1 lg:flex"
          >
            {NAV.map((n) => (
              <a
                key={n.href}
                href={n.href}
                className="rounded-full px-4 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
              >
                {n.label}
              </a>
            ))}
            <ResourcesMenu align="left" triggerClassName="rounded-full px-4 py-1.5 h-auto" />
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle className="hidden sm:inline-flex" />
            <LinkButton href="/login" variant="ghost" size="md" className="hidden sm:inline-flex">
              Sign in
            </LinkButton>
            <LinkButton href="/signup" size="md">
              Get started
            </LinkButton>
          </div>
        </div>
      </header>

      <main id="main">
        {/* Hero */}
        <section className="relative isolate overflow-hidden">
          <div
            aria-hidden
            className="absolute inset-0 -z-10 bg-[radial-gradient(70rem_36rem_at_50%_-8%,rgb(119_98_251/0.28),transparent_65%),radial-gradient(40rem_26rem_at_90%_20%,rgb(47_123_255/0.14),transparent_65%)]"
          />
          <div
            aria-hidden
            className={cn(
              "absolute inset-0 -z-10 [mask-image:radial-gradient(ellipse_70%_60%_at_50%_0%,black,transparent_78%)] opacity-70",
              GRID_BG,
            )}
          />

          <div className="mx-auto max-w-7xl px-5 pt-10 text-center sm:px-8 sm:pt-14">
            <a
              href="#ai"
              className="inline-flex animate-rise items-center gap-2 rounded-full border border-border-strong bg-surface/80 py-1.5 pr-4 pl-1.5 text-sm font-medium shadow-card backdrop-blur transition-colors hover:border-primary/50"
            >
              <span className="rounded-full bg-primary px-2.5 py-0.5 text-xs font-semibold text-primary-foreground">
                New
              </span>
              AI handover with full context
              <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden />
            </a>
            <h1 className="mx-auto mt-6 max-w-5xl animate-rise text-[2.7rem] leading-[1.02] font-semibold tracking-[-0.04em] text-balance [animation-delay:60ms] sm:text-[4.2rem] lg:text-[4.2rem]">
              AI help desk software that makes support{" "}
              <span className={GRADIENT_TEXT}>effortless</span>
            </h1>
            <p className="mx-auto mt-5 max-w-2xl animate-rise text-base leading-relaxed text-pretty text-muted-foreground [animation-delay:120ms] sm:text-xl">
              CeDesk is customer support software that brings your shared inbox, ticketing system,
              SLA management, automation, help center and reports into one calm workspace, where
              people and AI resolve conversations together.
            </p>
            <div className="mt-8 flex animate-rise flex-col items-center justify-center gap-3 [animation-delay:180ms] sm:flex-row">
              <LinkButton href="/signup">
                Create a workspace <ArrowRight className="size-4" aria-hidden />
              </LinkButton>
              <LinkButton href="/login" variant="outline">
                Sign in to your desk
              </LinkButton>
            </div>
            <ul className="mt-8 flex animate-rise flex-wrap items-center justify-center gap-x-7 gap-y-2 text-sm text-muted-foreground [animation-delay:240ms]">
              {["Customer portal included", "SSO and two-factor", "Open source frontend"].map(
                (t) => (
                  <li key={t} className="inline-flex items-center gap-1.5">
                    <Check className="size-4 text-success" aria-hidden />
                    {t}
                  </li>
                ),
              )}
            </ul>
          </div>

          <div className="relative mx-auto mt-8 max-w-6xl px-5 pb-9 sm:px-8 sm:pb-12">
            <Glow className="inset-x-16 top-16 -bottom-6 bg-primary/30" />
            <Glow className="-top-4 right-0 size-72 bg-[#2f7bff]/20" />
            <div className="relative [perspective:2000px]">
              <div className="animate-rise [animation-delay:300ms] lg:[transform:rotateX(4deg)]">
                <InboxMockup />
              </div>
              <Floating
                className="top-[72%] left-[1%] animate-float"
                icon={Sparkles}
                title="AI handed over"
                sub="With summary and mood"
                tone="bg-primary-soft text-primary-ink"
              />
              <Floating
                className="top-[66%] right-[13%] animate-float [animation-delay:-3s]"
                icon={Timer}
                title="SLA on track"
                sub="1h 12m left to first response"
                tone="bg-success-soft text-success"
              />
              <Floating
                className="-right-4 -bottom-6 animate-float [animation-delay:-5s]"
                icon={Check}
                title="Rated 5 out of 5"
                sub="One-click customer feedback"
                tone="bg-warning-soft text-warning"
              />
            </div>
          </div>
        </section>

        {/* Stats */}
        <section aria-label="At a glance" className="border-y border-border bg-surface">
          <dl className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-border sm:grid-cols-4">
            {STATS.map(([k, v]) => (
              <div key={k} className="px-5 py-6 text-center sm:py-7">
                <dt
                  className={cn("text-2xl font-semibold tracking-tight sm:text-3xl", GRADIENT_TEXT)}
                >
                  {k}
                </dt>
                <dd className="mt-1.5 text-sm text-muted-foreground">{v}</dd>
              </div>
            ))}
          </dl>
        </section>

        {/* Features: bento */}
        <section id="features" className="scroll-mt-20 px-5 py-9 sm:px-8 sm:py-12">
          <div className="mx-auto max-w-7xl">
            <SectionHead
              eyebrow="Customer support software"
              title="Everything a modern help desk and ticketing system needs"
              lead="No stitching tools together. Support, customers, campaigns and reporting share one data model, so context is never lost."
            />

            <div className="mt-8 grid gap-4 lg:grid-cols-6">
              <article className="group relative overflow-hidden rounded-3xl border border-border bg-surface p-6 shadow-card lg:col-span-3">
                <span className="grid size-11 place-items-center rounded-xl bg-primary-soft text-primary-ink">
                  <Inbox className="size-5" aria-hidden />
                </span>
                <h3 className="mt-4 text-xl font-semibold tracking-tight">
                  A shared inbox that keeps up
                </h3>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                  Status tabs, saved views, filters that live in the URL, bulk actions, snooze and
                  followers. Built to stay fast with thousands of tickets.
                </p>
                <div aria-hidden className="mt-5 flex flex-wrap gap-2">
                  {["Open 203", "Pending 23", "Resolved 68", "Snoozed 2", "Saved views"].map(
                    (t, i) => (
                      <span
                        key={t}
                        className={cn(
                          "rounded-full border px-3 py-1.5 text-xs font-semibold",
                          i === 0
                            ? "border-primary/40 bg-primary-soft text-primary-ink"
                            : "border-border bg-surface-2 text-muted-foreground",
                        )}
                      >
                        {t}
                      </span>
                    ),
                  )}
                </div>
              </article>

              <article className="relative overflow-hidden rounded-3xl border border-border bg-surface p-6 shadow-card lg:col-span-3">
                <span className="grid size-11 place-items-center rounded-xl bg-warning-soft text-warning">
                  <Timer className="size-5" aria-hidden />
                </span>
                <h3 className="mt-4 text-xl font-semibold tracking-tight">
                  SLAs that nudge, not nag
                </h3>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                  Set first-response and resolution targets per ticket type. Agents see time left at
                  a glance, and supervisors see what is about to slip.
                </p>
                <ul aria-hidden className="mt-5 space-y-3">
                  {[
                    ["First response", 72, "bg-success"],
                    ["Resolution", 41, "bg-warning"],
                    ["Next reply", 88, "bg-danger"],
                  ].map(([k, v, c]) => (
                    <li key={k as string} className="grid grid-cols-[7rem_1fr] items-center gap-3">
                      <span className="text-xs font-medium text-muted-foreground">{k}</span>
                      <span className="h-2 overflow-hidden rounded-full bg-surface-3">
                        <span
                          className={cn("block h-full rounded-full", c as string)}
                          style={{ width: `${v}%` }}
                        />
                      </span>
                    </li>
                  ))}
                </ul>
              </article>

              {SMALL_FEATURES.map(({ icon: Icon, title, body }) => (
                <article
                  key={title}
                  className="group rounded-3xl border border-border bg-surface p-6 shadow-card transition-all duration-300 hover:-translate-y-1 hover:border-primary/40 hover:shadow-pop lg:col-span-2"
                >
                  <span className="grid size-11 place-items-center rounded-xl bg-primary-soft text-primary-ink transition-colors duration-300 group-hover:bg-primary group-hover:text-primary-foreground">
                    <Icon className="size-5" aria-hidden />
                  </span>
                  <h3 className="mt-4 text-lg font-semibold tracking-tight">{title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* AI handoff */}
        <section
          id="ai"
          className="relative scroll-mt-20 overflow-hidden bg-surface-2 px-5 py-9 sm:px-8 sm:py-12"
        >
          <Glow className="-top-20 -left-20 size-96 bg-primary/15" />
          <div className="relative mx-auto grid max-w-7xl items-center gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-14">
            <div>
              <SectionHead
                align="left"
                eyebrow="AI customer support"
                title={
                  <>
                    AI to human handover with the <span className={GRADIENT_TEXT}>whole story</span>
                  </>
                }
                lead="Cevanos AI handles what it can. When a person is needed, the conversation arrives ready to work."
              />
              <dl className="mt-7 space-y-4">
                {AI_POINTS.map(([k, v]) => (
                  <div key={k} className="flex gap-4">
                    <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-success-soft text-success">
                      <Check className="size-3.5" aria-hidden />
                    </span>
                    <div>
                      <dt className="font-semibold">{k}</dt>
                      <dd className="text-sm text-muted-foreground">{v}</dd>
                    </div>
                  </div>
                ))}
              </dl>
            </div>
            <div className="relative">
              <Glow className="inset-10 bg-primary/25" />
              <HandoffMockup className="relative" />
            </div>
          </div>
        </section>

        {/* How it works */}
        <section className="px-5 py-9 sm:px-8 sm:py-12">
          <div className="mx-auto max-w-7xl">
            <SectionHead
              eyebrow="How it works"
              title="How the help desk works, from first message to happy customer"
              lead="A simple loop that gets better every week."
            />
            <ol className="relative mt-8 grid gap-10 md:grid-cols-2 lg:grid-cols-4 lg:gap-6">
              <div
                aria-hidden
                className="absolute top-6 right-[12%] left-[12%] hidden h-px bg-gradient-to-r from-transparent via-border-strong to-transparent lg:block"
              />
              {STEPS.map((s, i) => (
                <li key={s.title} className="relative text-center">
                  <span className="relative mx-auto grid size-16 place-items-center rounded-2xl border border-border-strong bg-surface text-primary-ink shadow-pop">
                    <s.icon className="size-6" aria-hidden />
                    <span className="absolute -top-2 -right-2 grid size-6 place-items-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
                      {i + 1}
                    </span>
                  </span>
                  <h3 className="mt-6 text-base font-semibold tracking-tight">{s.title}</h3>
                  <p className="mx-auto mt-2 max-w-[16rem] text-sm leading-relaxed text-muted-foreground">
                    {s.body}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Automation */}
        <section id="automation" className="scroll-mt-20 px-5 pb-9 sm:px-8 sm:pb-12">
          <div className="relative mx-auto max-w-7xl overflow-hidden rounded-[2rem] border border-border bg-surface shadow-card">
            <Glow className="-right-20 -bottom-20 size-96 bg-primary/10" />
            <div className="relative grid items-center gap-8 p-6 sm:p-9 lg:grid-cols-2 lg:gap-10 lg:p-10">
              <div className="relative order-2 lg:order-1">
                <Glow className="inset-8 bg-primary/20" />
                <RulesMockup className="relative mx-auto max-w-lg" />
              </div>
              <div className="order-1 lg:order-2">
                <SectionHead
                  align="left"
                  eyebrow="Rules, macros and workflows"
                  title="Support automation that runs the routine"
                  lead="Spend your people on the conversations that need them."
                />
                <ul className="mt-8 grid gap-3 sm:grid-cols-2">
                  {AUTOMATION.map(({ icon: Icon, label }) => (
                    <li
                      key={label}
                      className="flex items-start gap-3 rounded-2xl border border-border bg-surface-2/60 p-4 text-sm"
                    >
                      <Icon className="mt-0.5 size-4 shrink-0 text-primary-ink" aria-hidden />
                      {label}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* Reports + self-service */}
        <section className="relative overflow-hidden bg-surface-2 px-5 py-9 sm:px-8 sm:py-12">
          <Glow className="top-1/3 -right-24 size-96 bg-[#2f7bff]/15" />
          <div className="relative mx-auto max-w-7xl space-y-12">
            <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-14">
              <div className="relative">
                <Glow className="inset-10 bg-primary/20" />
                <ReportsMockup className="relative mx-auto max-w-xl" />
              </div>
              <div>
                <SectionHead
                  align="left"
                  eyebrow="Reports"
                  title="Support reports and analytics your team can act on"
                  lead="Live queue, backlog ageing, team performance and custom dashboards. Schedule the ones people need in their inbox."
                />
                <LinkButton href="/signup" variant="outline" size="md" className="mt-9">
                  See it in your workspace <ArrowRight className="size-4" aria-hidden />
                </LinkButton>
              </div>
            </div>

            <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-14">
              <div className="order-2 lg:order-1">
                <SectionHead
                  align="left"
                  eyebrow="Self-service"
                  title="A help center and customer portal customers actually use"
                  lead="Articles, a contact form, a portal and a ticket page, all on your workspace’s own link. Answer once, deflect the repeats."
                />
                <ul className="mt-7 space-y-4 text-[15px]">
                  {[
                    "Publish articles and see which ones help",
                    "Customers follow tickets without signing up",
                    "One-click satisfaction rating after every resolution",
                  ].map((t) => (
                    <li key={t} className="flex items-start gap-3">
                      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary-soft text-primary-ink">
                        <Globe2 className="size-3.5" aria-hidden />
                      </span>
                      {t}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="relative order-1 lg:order-2">
                <Glow className="inset-10 bg-primary/20" />
                <HelpMockup className="relative mx-auto max-w-xl" />
              </div>
            </div>
          </div>
        </section>

        {/* Security (dark) */}
        <section
          id="security"
          className="relative isolate scroll-mt-20 overflow-hidden bg-nav px-5 py-9 text-white sm:px-8 sm:py-12"
        >
          <div
            aria-hidden
            className="absolute inset-0 -z-10 bg-[radial-gradient(50rem_30rem_at_85%_-10%,rgb(119_98_251/0.45),transparent_60%),radial-gradient(40rem_28rem_at_0%_110%,rgb(47_123_255/0.28),transparent_62%)]"
          />
          <div
            aria-hidden
            className="absolute inset-0 -z-10 bg-[linear-gradient(rgb(255_255_255/0.05)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.05)_1px,transparent_1px)] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)] bg-[size:56px_56px]"
          />
          <div className="mx-auto max-w-7xl">
            <SectionHead
              dark
              eyebrow="Security and control"
              title="Secure help desk software with SSO, 2FA and an audit log"
              lead="Security choices are made in the open, with the reasoning written down in the repository."
            />
            <div className="mt-8 grid gap-4 sm:grid-cols-2">
              {SECURITY.map(({ icon: Icon, title, body }) => (
                <article
                  key={title}
                  className="flex gap-5 rounded-3xl border border-white/10 bg-white/[0.04] p-6 backdrop-blur transition-colors hover:bg-white/[0.07]"
                >
                  <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-white/10 text-[#c9bfff]">
                    <Icon className="size-5" aria-hidden />
                  </span>
                  <div>
                    <h3 className="text-base font-semibold tracking-tight">{title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-white/65">{body}</p>
                  </div>
                </article>
              ))}
            </div>

            <div className="mt-4 rounded-3xl border border-white/10 bg-white/[0.04] p-6 sm:p-9">
              <h3 className="text-lg font-semibold tracking-tight">Four roles, the right access</h3>
              <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {ROLES.map((r, i) => (
                  <li key={r.name} className="rounded-2xl bg-white/[0.06] p-5">
                    <span className="font-mono text-xs text-[#c9bfff]">0{i + 1}</span>
                    <p className="mt-2 font-semibold">{r.name}</p>
                    <p className="mt-1 text-sm text-white/65">{r.note}</p>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* Open source / cloud */}
        <section className="px-5 py-9 sm:px-8 sm:py-12">
          <div className="mx-auto max-w-7xl">
            <SectionHead
              eyebrow="Your way to run it"
              title="An open source help desk you can self-host"
              lead="Start with the open-source frontend today. Add Cevanos AI and managed hosting when you want them."
            />
            <div className="mx-auto mt-8 grid max-w-4xl gap-5 md:grid-cols-2">
              <article className="flex flex-col rounded-3xl border border-border bg-surface p-6 shadow-card">
                <span className="grid size-12 place-items-center rounded-2xl bg-surface-2 text-foreground">
                  <GitBranch className="size-6" aria-hidden />
                </span>
                <h3 className="mt-4 text-xl font-semibold tracking-tight">Open source</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  MIT-licensed frontend. Run it on your own infrastructure with a Docker image and a
                  few environment variables.
                </p>
                <ul className="mt-6 space-y-2.5 text-sm">
                  {["Full source, no lock-in", "Docker image included", "Community-friendly"].map(
                    (t) => (
                      <li key={t} className="flex items-center gap-2.5">
                        <Check className="size-4 text-success" aria-hidden /> {t}
                      </li>
                    ),
                  )}
                </ul>
              </article>
              <article className="relative flex flex-col overflow-hidden rounded-3xl border border-primary/40 bg-surface p-6 shadow-pop ring-1 ring-primary/20">
                <Glow className="-top-16 -right-16 size-56 bg-primary/25" />
                <span className="relative grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-[0_10px_24px_-10px_var(--primary)]">
                  <Sparkles className="size-6" aria-hidden />
                </span>
                <h3 className="relative mt-4 text-xl font-semibold tracking-tight">
                  Cevanos AI and managed cloud
                </h3>
                <p className="relative mt-2 text-sm leading-relaxed text-muted-foreground">
                  The AI channel that answers customers first, plus hosting we look after for you.
                </p>
                <ul className="relative mt-6 space-y-2.5 text-sm">
                  {[
                    "AI answers with human handover",
                    "Managed hosting",
                    "Talk to us about plans",
                  ].map((t) => (
                    <li key={t} className="flex items-center gap-2.5">
                      <Check className="size-4 text-success" aria-hidden /> {t}
                    </li>
                  ))}
                </ul>
                <LinkButton href="/signup" size="md" className="relative mt-8 self-start">
                  Create a workspace
                </LinkButton>
              </article>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section id="faq" className="scroll-mt-20 bg-surface-2 px-5 py-9 sm:px-8 sm:py-12">
          <div className="mx-auto max-w-3xl">
            <SectionHead eyebrow="FAQ" title="Help desk software FAQ" />
            <div className="mt-8 divide-y divide-border overflow-hidden rounded-3xl border border-border bg-surface shadow-card">
              {FAQ.map((f) => (
                <details key={f.q} className="group px-7 py-1">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-4 text-base font-medium marker:content-none [&::-webkit-details-marker]:hidden">
                    {f.q}
                    <span
                      aria-hidden
                      className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-lg leading-none text-muted-foreground transition-transform duration-200 group-open:rotate-45"
                    >
                      +
                    </span>
                  </summary>
                  <p className="pb-6 text-sm leading-relaxed text-muted-foreground">{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section className="px-5 py-9 sm:px-8 sm:py-12">
          <div className="relative isolate mx-auto max-w-6xl overflow-hidden rounded-[2rem] bg-nav px-6 py-10 text-center text-white sm:px-12 sm:py-12">
            <div
              aria-hidden
              className="absolute inset-0 -z-10 bg-[radial-gradient(50rem_30rem_at_85%_-10%,rgb(119_98_251/0.65),transparent_60%),radial-gradient(40rem_28rem_at_0%_110%,rgb(47_123_255/0.4),transparent_62%)]"
            />
            <div
              aria-hidden
              className="absolute inset-0 -z-10 bg-[linear-gradient(rgb(255_255_255/0.06)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.06)_1px,transparent_1px)] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)] bg-[size:48px_48px]"
            />
            <h2 className="mx-auto max-w-3xl text-[2.2rem] leading-[1.06] font-semibold tracking-[-0.03em] text-balance sm:text-[2.7rem]">
              Give your customers a faster answer, and your team a calmer day
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-base text-pretty text-white/70 sm:text-lg">
              Create a workspace in minutes, or sign in to pick up where you left off.
            </p>
            <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <LinkButton href="/signup" variant="light">
                Create a workspace <ArrowRight className="size-4" aria-hidden />
              </LinkButton>
              <LinkButton href="/login" variant="glass">
                Sign in
              </LinkButton>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-border bg-surface">
        <div className="mx-auto flex max-w-7xl flex-col gap-10 px-5 py-10 sm:px-8 md:flex-row md:items-start md:justify-between">
          <div className="max-w-xs">
            <Logo />
            <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
              The help desk where people and AI resolve customer conversations together.
            </p>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-2 gap-x-16 gap-y-3 text-sm">
            {FOOTER_LINKS.map(([href, label]) =>
              href.startsWith("#") ? (
                <a key={href} href={href} className="text-muted-foreground hover:text-foreground">
                  {label}
                </a>
              ) : (
                <Link
                  key={href}
                  href={href}
                  className="text-muted-foreground hover:text-foreground"
                >
                  {label}
                </Link>
              ),
            )}
          </nav>
        </div>
        <div className="border-t border-border">
          <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-3 px-5 py-5 text-xs text-subtle-foreground sm:flex-row sm:px-8">
            <p>© {new Date().getFullYear()} Cevanos · CeDesk</p>
            <ThemeToggle />
          </div>
        </div>
      </footer>
    </div>
  );
}
