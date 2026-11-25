import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Braces,
  ChevronRight,
  Inbox,
  KeyRound,
  LifeBuoy,
  Megaphone,
  Plug,
  Rocket,
  ShieldCheck,
  Sparkles,
  Ticket,
  Users,
  Wand2,
  Workflow,
  Wrench,
  Download,
  UserCog,
  Radio,
  type LucideIcon,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DocsSearch } from "@/components/docs/docs-search";
import { DocsToc } from "@/components/docs/docs-toc";
import {
  getAllSlugs,
  getDoc,
  getSectionLinks,
  getSections,
  type DocSection,
} from "@/lib/server/docs";
import { cn } from "@/lib/utils";

type Props = { params: Promise<{ slug?: string[] }> };

export const dynamicParams = false;

export async function generateStaticParams() {
  return (await getAllSlugs()).map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug = [] } = await params;
  if (slug.length === 0) return { title: "Documentation" };
  const doc = await getDoc(slug);
  if (!doc) return {};
  return { title: doc.title, description: doc.description || undefined };
}

const ICONS: Record<string, LucideIcon> = {
  "getting-started": Rocket,
  "working-with-tickets": Ticket,
  "ticket-tools": Wrench,
  channels: Inbox,
  customers: Users,
  automation: Wand2,
  campaigns: Megaphone,
  reports: BarChart3,
  "security-and-privacy": ShieldCheck,
  "account-and-access": KeyRound,
  integrations: Plug,
  "help-center": LifeBuoy,
  imports: Download,
  "best-practices": Sparkles,
  "how-to": Workflow,
  developers: Braces,
  api: Radio,
  glossary: BookOpen,
};
function SectionIcon({ slug, className }: { slug: string; className?: string }) {
  const Cmp = ICONS[slug] ?? UserCog;
  return <Cmp className={className} aria-hidden />;
}

export default async function DocsPage({ params }: Props) {
  const { slug = [] } = await params;
  const sections = await getSections();
  if (slug.length === 0) return <Home sections={sections} />;

  const doc = await getDoc(slug);
  if (!doc) notFound();

  const section = doc.section;
  const sectionMeta = sections.find((s) => s.slug === section);
  const sectionTitle = sectionMeta?.title ?? (section === "api" ? "API reference" : null);
  const links = section ? await getSectionLinks(section) : [];
  const isSectionIndex = slug.length === 1 && Boolean(sectionMeta);
  const here = `/docs/${slug.join("/")}`;
  const idx = links.findIndex((l) => l.href === here);
  const prev = idx > 0 ? links[idx - 1] : null;
  const next = idx >= 0 && idx < links.length - 1 ? links[idx + 1] : null;

  return (
    <div className="mx-auto grid max-w-[90rem] gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)] lg:gap-10 lg:py-8 xl:grid-cols-[16rem_minmax(0,1fr)_14rem]">
      {/* Sidebar */}
      <aside className="lg:sticky lg:top-24 lg:h-[calc(100dvh-7rem)] lg:overflow-y-auto lg:pr-2">
        <details key={here} className="group lg:hidden">
          <summary className="flex cursor-pointer items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 text-sm font-semibold">
            Browse topics
            <ChevronRight className="size-4 transition group-open:rotate-90" aria-hidden />
          </summary>
          <div className="mt-2">
            <SideNav sections={sections} current={section} links={links} here={here} />
          </div>
        </details>
        <div className="hidden lg:block">
          <SideNav sections={sections} current={section} links={links} here={here} />
        </div>
      </aside>

      {/* Article */}
      <article className="min-w-0">
        <nav
          aria-label="Breadcrumb"
          className="mb-5 flex items-center gap-1.5 text-sm text-muted-foreground"
        >
          <Link href="/docs" className="hover:text-foreground">
            Docs
          </Link>
          {sectionTitle && (
            <>
              <ChevronRight className="size-3.5" aria-hidden />
              {slug.length > 1 ? (
                <Link href={`/docs/${section}`} className="hover:text-foreground">
                  {sectionTitle}
                </Link>
              ) : (
                <span className="text-foreground">{sectionTitle}</span>
              )}
            </>
          )}
        </nav>

        <header className="mb-8 border-b border-border pb-8">
          <span className="mb-4 inline-flex size-12 items-center justify-center rounded-2xl bg-primary-soft text-primary-ink ring-1 ring-primary/15">
            <SectionIcon slug={section ?? slug[0] ?? ""} className="size-6" />
          </span>
          <h1 className="text-4xl font-semibold tracking-tight text-balance text-foreground sm:text-[2.6rem] sm:leading-[1.1]">
            {doc.title}
            {doc.draft && (
              <span className="ml-3 rounded-md bg-warning-soft px-2 py-0.5 align-middle text-xs font-semibold text-warning">
                Draft
              </span>
            )}
          </h1>
          {doc.meta && <p className="mt-3 text-sm text-subtle-foreground">{doc.meta}</p>}
          {isSectionIndex && sectionMeta && (
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              {sectionMeta.description}
            </p>
          )}
        </header>

        {isSectionIndex && links.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {links.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="group rounded-2xl border border-border bg-surface p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
              >
                <span className="flex items-center justify-between gap-2 font-semibold text-foreground">
                  {l.title}
                  <ArrowRight
                    className="size-4 shrink-0 text-primary-ink opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100"
                    aria-hidden
                  />
                </span>
                {l.summary && (
                  <span className="mt-1.5 block text-sm leading-relaxed text-muted-foreground">
                    {l.summary}
                  </span>
                )}
              </Link>
            ))}
          </div>
        ) : (
          // Trusted HTML built from our own docs repository (see src/lib/server/docs.ts).
          // eslint-disable-next-line react/no-danger
          <div className="docs-prose" dangerouslySetInnerHTML={{ __html: doc.html }} />
        )}

        {(prev || next) && !isSectionIndex && (
          <nav
            aria-label="Pagination"
            className="mt-14 grid gap-4 border-t border-border pt-8 sm:grid-cols-2"
          >
            {prev ? (
              <Link
                href={prev.href}
                className="group rounded-2xl border border-border bg-surface p-4 transition hover:border-primary/40 hover:shadow-md"
              >
                <span className="flex items-center gap-1 text-xs text-subtle-foreground">
                  <ArrowLeft className="size-3.5" aria-hidden /> Previous
                </span>
                <span className="mt-1 block font-semibold text-foreground group-hover:text-primary-ink">
                  {prev.title}
                </span>
              </Link>
            ) : (
              <span />
            )}
            {next && (
              <Link
                href={next.href}
                className="group rounded-2xl border border-border bg-surface p-4 text-right transition hover:border-primary/40 hover:shadow-md"
              >
                <span className="flex items-center justify-end gap-1 text-xs text-subtle-foreground">
                  Next <ArrowRight className="size-3.5" aria-hidden />
                </span>
                <span className="mt-1 block font-semibold text-foreground group-hover:text-primary-ink">
                  {next.title}
                </span>
              </Link>
            )}
          </nav>
        )}
      </article>

      {/* On this page */}
      <aside className="hidden xl:block">
        <div className="sticky top-24">
          <DocsToc items={doc.toc} />
        </div>
      </aside>
    </div>
  );
}

function SideNav({
  sections,
  current,
  links,
  here,
}: {
  sections: DocSection[];
  current: string | null;
  links: { href: string; title: string }[];
  here: string;
}) {
  const row = (active: boolean) =>
    cn(
      "flex min-h-11 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors lg:min-h-0",
      active
        ? "bg-primary-soft font-semibold text-primary-ink"
        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
    );
  return (
    <nav aria-label="Topics" className="space-y-0.5 pb-8">
      {sections.map((s) => {
        const open = s.slug === current;
        return (
          <div key={s.slug}>
            <Link href={`/docs/${s.slug}`} className={row(open && here === `/docs/${s.slug}`)}>
              <SectionIcon slug={s.slug} className="size-4 shrink-0" />
              {s.title}
            </Link>
            {open && links.length > 0 && (
              <ul className="my-1 ml-[1.15rem] space-y-px border-l border-border pl-3">
                {links.map((l) => (
                  <li key={l.href}>
                    <Link
                      href={l.href}
                      aria-current={l.href === here ? "page" : undefined}
                      className={cn(
                        "flex min-h-11 items-center rounded-md px-2.5 py-1.5 text-sm leading-snug transition-colors lg:min-h-0 lg:text-[13px]",
                        l.href === here
                          ? "bg-surface-2 font-semibold text-foreground"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {l.title}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
      <div className="my-3 border-t border-border" />
      <Link href="/docs/api" className={row(current === "api")}>
        <Radio className="size-4 shrink-0" aria-hidden />
        API reference
      </Link>
      <Link href="/docs/glossary" className={row(here === "/docs/glossary")}>
        <BookOpen className="size-4 shrink-0" aria-hidden />
        Glossary
      </Link>
    </nav>
  );
}

function Home({ sections }: { sections: DocSection[] }) {
  const featured = [
    {
      href: "/docs/getting-started/create-your-workspace",
      title: "Create your workspace",
      icon: Rocket,
    },
    { href: "/docs/developers/quickstart", title: "API quickstart", icon: Braces },
    { href: "/docs/how-to", title: "How-to recipes", icon: Workflow },
    { href: "/docs/api", title: "API reference", icon: Radio },
  ];
  return (
    <>
      <section className="relative overflow-hidden border-b border-border">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_26rem_at_50%_-8rem,color-mix(in_srgb,var(--primary)_22%,transparent),transparent),linear-gradient(var(--border)_1px,transparent_1px),linear-gradient(90deg,var(--border)_1px,transparent_1px)] [mask-image:linear-gradient(to_bottom,black,transparent_92%)] bg-[size:auto,56px_56px,56px_56px]"
        />
        <div className="relative mx-auto max-w-4xl px-6 pt-16 pb-16 text-center sm:pt-24 sm:pb-24">
          <span className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary-soft px-3.5 py-1 text-xs font-semibold text-primary-ink">
            <Sparkles className="size-3.5" aria-hidden /> CeDesk documentation
          </span>
          <h1 className="mt-6 text-4xl font-semibold tracking-tight text-balance text-foreground sm:text-6xl sm:leading-[1.05]">
            How can we{" "}
            <span className="bg-gradient-to-r from-primary-ink via-primary to-[#2f7bff] bg-clip-text text-transparent">
              help you
            </span>
            ?
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
            Step-by-step guides for your team, short recipes for common jobs, and the complete API
            reference for developers.
          </p>
          <div className="mt-9">
            <DocsSearch variant="hero" />
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2 text-sm">
            {featured.map((f) => (
              <Link
                key={f.href}
                href={f.href}
                className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/80 px-3.5 py-1.5 font-medium text-foreground-2 shadow-sm transition hover:border-primary/40 hover:text-primary-ink"
              >
                <f.icon className="size-3.5 text-primary-ink" aria-hidden />
                {f.title}
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[80rem] px-6 py-16">
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
              Browse by topic
            </h2>
            <p className="mt-1.5 text-muted-foreground">
              Everything CeDesk does, organised the way you work.
            </p>
          </div>
        </div>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {sections.map((s) => {
            return (
              <Link
                key={s.slug}
                href={`/docs/${s.slug}`}
                className="group relative flex flex-col rounded-2xl border border-border bg-surface p-6 shadow-sm transition duration-200 hover:-translate-y-1 hover:border-primary/40 hover:shadow-lg"
              >
                <span className="mb-4 inline-flex size-11 items-center justify-center rounded-xl bg-primary-soft text-primary-ink ring-1 ring-primary/15 transition group-hover:bg-primary group-hover:text-primary-foreground">
                  <SectionIcon slug={s.slug} className="size-5" />
                </span>
                <span className="text-lg font-semibold text-foreground">{s.title}</span>
                <span className="mt-2 flex-1 text-sm leading-relaxed text-muted-foreground">
                  {s.description}
                </span>
                <span className="mt-5 flex items-center justify-between text-xs font-medium text-subtle-foreground">
                  {s.count}
                  <ArrowRight
                    className="size-4 text-primary-ink opacity-0 transition group-hover:translate-x-1 group-hover:opacity-100"
                    aria-hidden
                  />
                </span>
              </Link>
            );
          })}
        </div>

        <div className="mt-14 overflow-hidden rounded-3xl bg-[var(--nav)] p-8 text-white shadow-lg sm:p-12">
          <div className="flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-center">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                Building on CeDesk?
              </h2>
              <p className="mt-2 max-w-xl text-[#cfc8ee]">
                Authenticate with an API key, call every route over HTTP and receive webhooks and
                live updates as things happen.
              </p>
            </div>
            <div className="flex shrink-0 gap-3">
              <Link
                href="/docs/developers"
                className="inline-flex h-11 items-center rounded-full bg-white px-5 text-sm font-semibold text-[#1a1240] transition hover:bg-white/90"
              >
                Developer guides
              </Link>
              <Link
                href="/docs/api"
                className="inline-flex h-11 items-center rounded-full border border-white/25 px-5 text-sm font-semibold text-white transition hover:bg-white/10"
              >
                API reference
              </Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
