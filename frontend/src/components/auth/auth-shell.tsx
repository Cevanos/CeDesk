import { Inbox, ShieldCheck, Sparkles, Zap } from "lucide-react";
import Link from "next/link";

import { Logo, ThemeToggle } from "@/components/ui/brand";

const POINTS = [
  {
    icon: Sparkles,
    title: "AI hands over with full context",
    body: "Summary, reason and sentiment arrive with the ticket.",
  },
  {
    icon: Zap,
    title: "Routed to the right person",
    body: "Teams, skills and capacity decide who gets it.",
  },
  {
    icon: ShieldCheck,
    title: "Roles you control",
    body: "Light agents to admins, each sees only what they should.",
  },
];

/**
 * Two-pane auth layout. Form on the left; on large screens a brand panel on
 * the right with a product illustration. Collapses to a single column below `lg`.
 */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-svh grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="flex min-w-0 flex-col px-5 py-6 sm:px-10 sm:py-8 lg:px-16">
        <header className="flex items-center justify-between">
          <Link
            href="/"
            aria-label="CeDesk home"
            className="rounded-lg focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none"
          >
            <Logo />
          </Link>
          <ThemeToggle />
        </header>

        <main
          id="main"
          className="mx-auto flex w-full max-w-[26rem] flex-1 flex-col justify-center py-10"
        >
          {children}
        </main>

        <footer className="text-xs text-subtle-foreground">
          © {new Date().getFullYear()} Cevanos · CeDesk
        </footer>
      </div>

      <aside
        aria-hidden
        className="relative hidden overflow-hidden bg-nav text-white lg:flex lg:flex-col lg:justify-center lg:px-12 xl:px-16"
      >
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_40rem_at_90%_-10%,rgb(119_98_251/0.55),transparent_60%),radial-gradient(50rem_36rem_at_-10%_110%,rgb(91_60_240/0.45),transparent_62%)]" />
        <div className="pointer-events-none absolute -top-24 -right-20 size-96 animate-drift rounded-full bg-primary/30 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 -left-20 size-96 animate-drift rounded-full bg-[#2f7bff]/20 blur-3xl [animation-direction:reverse]" />
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgb(255_255_255/0.04)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.04)_1px,transparent_1px)] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)] bg-[size:48px_48px]" />

        <div className="relative mx-auto w-full max-w-[34rem]">
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-[11px] font-semibold tracking-[0.12em] text-white/80 uppercase backdrop-blur">
            <Sparkles className="size-3.5 text-[#8fe3f5]" /> Cevanos AI inside
          </span>
          <Illustration />
          <h2 className="mt-12 text-[2rem] leading-[1.15] font-semibold tracking-[-0.02em] text-balance xl:text-[2.4rem]">
            One desk for every customer conversation,{" "}
            <span className="bg-gradient-to-r from-[#c4b9ff] via-[#8fb4ff] to-[#5fe0f2] bg-clip-text text-transparent">
              human and AI.
            </span>
          </h2>
          <ul className="mt-9 space-y-5">
            {POINTS.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex items-center gap-4">
                <span className="grid size-11 flex-none place-items-center rounded-full border border-white/15 bg-white/10 backdrop-blur">
                  <Icon className="size-5 text-[#c4b9ff]" strokeWidth={2.1} />
                </span>
                <div>
                  <div className="text-[15px] font-semibold">{title}</div>
                  <div className="mt-0.5 text-sm text-white/65">{body}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

function Illustration() {
  const rows = [
    { id: "T-1005", t: "Order not delivered", p: "Urgent", c: "bg-[#ff6b5a]", who: "Asha K." },
    { id: "T-1004", t: "Refund for damaged item", p: "High", c: "bg-[#f0b45a]", who: "Rohan G." },
    { id: "T-1003", t: "Change delivery address", p: "Medium", c: "bg-[#7fb0ff]", who: "Priya S." },
  ];
  return (
    <div className="relative mx-auto h-72 w-full max-w-[28rem]">
      <div className="absolute inset-x-0 top-6 animate-float rounded-2xl border border-white/15 bg-white/[0.07] p-4 shadow-[0_30px_70px_-30px_rgb(0_0_0/0.8)] backdrop-blur-xl">
        <div className="mb-3 flex items-center gap-2 text-[12px] font-medium text-white/70">
          <Inbox className="size-4" /> Open tickets
          <span className="ml-auto rounded-full bg-white/10 px-2 py-0.5">12</span>
        </div>
        <ul className="space-y-2">
          {rows.map((r) => (
            <li
              key={r.id}
              className="flex items-center gap-3 rounded-xl bg-white/[0.06] px-3 py-2.5"
            >
              <span className={`size-2 rounded-full ${r.c}`} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{r.t}</div>
                <div className="text-[11px] text-white/55">
                  {r.id} · {r.who}
                </div>
              </div>
              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold">
                {r.p}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="absolute -right-2 -bottom-1 w-56 animate-float rounded-2xl border border-white/20 bg-gradient-to-br from-[#7c5cff] via-[#5b8cff] to-[#1fc8e3] p-3.5 shadow-[0_24px_50px_-20px_rgb(60_120_255/0.9)] [animation-delay:-3s]">
        <div className="flex items-center gap-1.5 text-[10px] font-bold tracking-wider text-white/80 uppercase">
          <Sparkles className="size-3" /> AI handover
        </div>
        <p className="mt-1.5 text-[12px] leading-snug text-white/95">
          Refund above AI limit. Customer verified. Sentiment: upset.
        </p>
      </div>
    </div>
  );
}
