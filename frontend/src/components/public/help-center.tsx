"use client";

import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  FileSearch,
  Languages,
  Search,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { publicErrorMessage } from "@/components/public/errors";
import { Markdown } from "@/components/public/markdown";
import { prettySlug } from "@/components/public/public-shell";
import { Badge, Button, Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import {
  useContactFormOff,
  useHelpLocale,
  usePublicArticle,
  usePublicHelp,
  useVoteArticle,
  useWorkspaceSlug,
} from "@/hooks/use-portal";
import type { Article } from "@/lib/api/types";
import { isRtl, languageName, toBcp47 } from "@/lib/locale";
import { cn } from "@/lib/utils";

/** Article address. The id decides the article; the slug is only for people and search engines. */
const articleHref = (ws: string, a: { id: string; slug?: string | null }, locale = "") => {
  const base = `/${encodeURIComponent(ws)}/help/${encodeURIComponent(a.id)}`;
  const path = a.slug ? `${base}/${encodeURIComponent(a.slug)}` : base;
  return locale ? `${path}?locale=${encodeURIComponent(locale)}` : path;
};

/** `lang` and `dir` for a block of text in this language. */
const textProps = (locale: string | undefined) =>
  locale ? { lang: toBcp47(locale), dir: isRtl(locale) ? ("rtl" as const) : ("ltr" as const) } : {};

/** First readable line of an article body, without Markdown marks, for the card preview. */
function excerpt(body: string, max = 120) {
  const text = body
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`]/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

function ArticleCard({ ws, a, locale }: { ws: string; a: Article; locale: string }) {
  return (
    <Link
      href={articleHref(ws, a, locale)}
      className="group flex h-full flex-col rounded-2xl border border-border bg-surface p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop focus-visible:ring-4 focus-visible:ring-primary/25 focus-visible:outline-none"
    >
      {a.category_name && (
        <Badge tone="primary" className="mb-3 self-start">
          {a.category_name}
        </Badge>
      )}
      <h3 className="text-[15px] font-semibold text-balance" {...textProps(locale)}>
        {a.title}
      </h3>
      {a.body && (
        <p className="mt-1.5 line-clamp-2 text-sm text-muted-foreground" {...textProps(locale)}>
          {excerpt(a.body)}
        </p>
      )}
      <span className="mt-auto inline-flex items-center gap-1 pt-4 text-[13px] font-medium text-primary-ink">
        Read article{" "}
        <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5 rtl:rotate-180 rtl:group-hover:-translate-x-0.5" aria-hidden />
      </span>
    </Link>
  );
}

/** `/<workspace>/help`: search, category chips and article cards. */
export function HelpHome() {
  const ws = useWorkspaceSlug();
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const cat = params.get("category") ?? "";
  const { locale: asked } = useHelpLocale();
  const help = usePublicHelp(ws, q, asked);
  const served = help.data?.locale ?? asked;
  const formOff = useContactFormOff(ws);
  const [text, setText] = useState(q);

  const go = (next: { q?: string; category?: string }) => {
    const sp = new URLSearchParams();
    const nq = next.q ?? q;
    const nc = next.category ?? cat;
    if (nq) sp.set("q", nq);
    if (nc) sp.set("category", nc);
    if (params.get("locale")) sp.set("locale", params.get("locale")!);
    const s = sp.toString();
    router.push(`/${encodeURIComponent(ws)}/help${s ? `?${s}` : ""}`);
  };

  const shown = (help.data?.articles ?? []).filter((a) => !cat || a.category_id === cat);

  return (
    <div className="animate-rise" {...textProps(served)}>
      <section className="mx-auto max-w-2xl text-center">
        <p className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface/80 px-3 py-1 text-xs font-medium text-muted-foreground backdrop-blur">
          <BookOpen className="size-3.5 text-primary-ink" aria-hidden />
          {help.data?.workspace_name ?? prettySlug(ws)} help center
        </p>
        <h1 className="mt-5 text-4xl font-semibold tracking-[-0.02em] text-balance sm:text-5xl">
          How can we{" "}
          <span className="bg-gradient-to-r from-primary to-[#2f7bff] bg-clip-text text-transparent">
            help you?
          </span>
        </h1>
        <form
          role="search"
          className="relative mt-8"
          onSubmit={(e) => {
            e.preventDefault();
            go({ q: text.trim() });
          }}
        >
          <label htmlFor="help-q" className="sr-only">
            Search the help center
          </label>
          <Search
            className="pointer-events-none absolute top-1/2 start-4 size-5 -translate-y-1/2 text-subtle-foreground"
            aria-hidden
          />
          <input
            id="help-q"
            type="search"
            value={text}
            placeholder="Search for answers…"
            onChange={(e) => setText(e.target.value)}
            className="h-14 w-full rounded-2xl border border-input bg-surface pe-28 ps-12 text-base shadow-pop transition-colors placeholder:text-subtle-foreground hover:border-border-strong focus:border-primary focus:ring-4 focus:ring-primary/15 focus:outline-none"
          />
          <Button type="submit" className="absolute top-2 end-2 h-10">
            Search
          </Button>
        </form>
      </section>

      {(help.data?.categories.length ?? 0) > 0 && (
        <div role="group" aria-label="Categories" className="mt-8 flex flex-wrap justify-center gap-2">
          {[{ id: "", name: "All topics" }, ...help.data!.categories].map((c) => (
            <button
              key={c.id || "all"}
              type="button"
              aria-pressed={cat === c.id}
              onClick={() => go({ category: c.id })}
              className={cn(
                "h-9 rounded-full border px-4 text-[13px] font-medium transition-colors focus-visible:ring-4 focus-visible:ring-primary/25 focus-visible:outline-none",
                cat === c.id
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-surface text-muted-foreground hover:border-border-strong hover:text-foreground",
              )}
            >
              {c.name}
            </button>
          ))}
        </div>
      )}

      <section aria-live="polite" className="mt-10">
        {help.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-36 rounded-2xl" />
            ))}
          </div>
        ) : help.isError ? (
          <p role="alert" className="mx-auto max-w-xl rounded-xl bg-danger-soft p-4 text-sm text-danger">
            {publicErrorMessage(help.error)}
          </p>
        ) : shown.length === 0 ? (
          <Card className="mx-auto max-w-xl rounded-2xl">
            <EmptyState
              icon={<FileSearch className="size-6" />}
              title={q ? `No results for “${q}”` : "No articles yet"}
            >
              {q ? "Try different words, or contact our team." : "Please check back soon."}
            </EmptyState>
          </Card>
        ) : (
          <>
            <p className="mb-4 text-sm text-muted-foreground">
              {q ? `${shown.length} result${shown.length === 1 ? "" : "s"} for “${q}”` : "Popular articles"}
            </p>
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {shown.map((a) => (
                <li key={a.id}>
                  <ArticleCard ws={ws} a={a} locale={asked} />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {!formOff && (
        <p className="mt-12 text-center text-sm text-muted-foreground">
          Can&apos;t find it?{" "}
          <Link
            href={`/${encodeURIComponent(ws)}/contact`}
            className="font-medium text-primary-ink hover:underline"
          >
            Contact us
          </Link>
        </p>
      )}
    </div>
  );
}

/** `/<workspace>/help/<id>[/<slug>]`: the article (Markdown, never raw HTML) and a helpful vote. */
export function HelpArticle() {
  const ws = useWorkspaceSlug();
  const { id } = useParams<{ id: string }>();
  const { locale: asked, choose } = useHelpLocale();
  const q = usePublicArticle(ws, id, asked);

  if (q.isPending) {
    return (
      <div className="mx-auto max-w-3xl space-y-3">
        <Skeleton className="h-9 w-3/4" />
        <Skeleton className="h-64 rounded-2xl" />
      </div>
    );
  }
  if (q.isError) {
    return (
      <Card className="mx-auto max-w-xl rounded-2xl">
        <EmptyState icon={<FileSearch className="size-6" />} title="We can't find that article">
          {publicErrorMessage(q.error)}
        </EmptyState>
      </Card>
    );
  }

  const { article, related, fallback, available, defaultLocale } = q.data;
  const served = q.data.locale ?? defaultLocale ?? asked;
  const switchTo = available.length > 1 ? available : [];
  return (
    <article className="mx-auto max-w-3xl animate-rise">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href={`/${encodeURIComponent(ws)}/help${asked ? `?locale=${encodeURIComponent(asked)}` : ""}`}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4 rtl:rotate-180" /> Help center
        </Link>
        {switchTo.length > 0 && (
          <LanguageSwitcher locales={switchTo} current={served ?? ""} onChoose={choose} />
        )}
      </div>
      {fallback && asked && served && (
        <p
          role="status"
          className="mt-4 rounded-xl border border-border bg-surface-2 px-4 py-2.5 text-sm text-muted-foreground"
        >
          This article is not available in {languageName(asked)} yet. Showing{" "}
          {languageName(served)}.
        </p>
      )}
      {article.category_name && (
        <Badge tone="primary" className="mt-5">
          {article.category_name}
        </Badge>
      )}
      {/* Only the article's own text takes its language's direction; the interface text around it
          (the vote, links, notices) stays in the interface's direction, so English isn't mirrored. */}
      <h1
        className="mt-3 text-3xl font-semibold tracking-tight text-balance sm:text-4xl"
        {...textProps(served)}
      >
        {article.title}
      </h1>
      <Card className="mt-6 rounded-2xl p-5 shadow-pop sm:p-8" {...textProps(served)}>
        <Markdown source={article.body ?? ""} />
      </Card>

      <Vote ws={ws} id={article.id ?? id} locale={served ?? ""} />

      {related.length > 0 && (
        <section aria-labelledby="related" className="mt-12">
          <h2 id="related" className="text-lg font-semibold tracking-tight">
            Related articles
          </h2>
          <ul className="mt-4 grid gap-4 sm:grid-cols-2">
            {related.slice(0, 4).map((a) => (
              <li key={a.id}>
                <ArticleCard ws={ws} a={a} locale={asked} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

/** Shown only when the article exists in more than one published language. */
function LanguageSwitcher({
  locales,
  current,
  onChoose,
}: {
  locales: string[];
  current: string;
  onChoose: (l: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <Languages className="size-4" aria-hidden />
      <span className="sr-only">Language</span>
      <select
        value={current}
        onChange={(e) => onChoose(e.target.value)}
        className="h-9 rounded-lg border border-input bg-surface px-2.5 text-sm text-foreground focus:border-primary focus:ring-4 focus:ring-primary/15 focus:outline-none"
      >
        {locales.map((l) => (
          <option key={l} value={l} lang={toBcp47(l)}>
            {languageName(l, l)}
          </option>
        ))}
      </select>
    </label>
  );
}

function Vote({ ws, id, locale }: { ws: string; id: string; locale: string }) {
  const vote = useVoteArticle(ws, id, locale);
  const [voted, setVoted] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl p-5">
      <p className="text-sm font-medium" id="helpful-q">
        Was this helpful?
      </p>
      {voted === null ? (
        <div role="group" aria-labelledby="helpful-q" className="flex gap-2">
          {[
            { v: true, label: "Yes", icon: ThumbsUp },
            { v: false, label: "No", icon: ThumbsDown },
          ].map(({ v, label, icon: Icon }) => (
            <Button
              key={label}
              variant="outline"
              disabled={vote.isPending}
              onClick={() => {
                setError(null);
                vote.mutate(v, {
                  onSuccess: () => setVoted(v),
                  onError: (e) => setError(publicErrorMessage(e)),
                });
              }}
            >
              <Icon className="size-4" /> {label}
            </Button>
          ))}
        </div>
      ) : (
        <p role="status" className="inline-flex items-center gap-1.5 text-sm text-success">
          <Check className="size-4" aria-hidden /> Thanks for your feedback
        </p>
      )}
      {error && (
        <p role="alert" className="w-full text-sm text-danger">
          {error}
        </p>
      )}
    </Card>
  );
}
