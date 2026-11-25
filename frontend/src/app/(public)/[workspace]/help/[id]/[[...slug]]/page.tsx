import type { Metadata } from "next";

import { HelpArticle } from "@/components/public/help-center";
import type { Article } from "@/lib/api/types";
import { normalizeLocale, isValidLocale, toBcp47 } from "@/lib/locale";
import { articleUrl, fetchHelpSitemap, fetchPublicArticle } from "@/lib/server/help-seo";

type Props = {
  params: Promise<{ workspace: string; id: string; slug?: string[] }>;
  searchParams: Promise<{ locale?: string | string[] }>;
};

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

/** Plain start of a Markdown body, for a description when none was written. */
const bodyStart = (body: string) =>
  clip(
    body
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/[#>*_`]/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\s+/g, " ")
      .trim(),
    160,
  );

/**
 * Title, description, canonical and `hreflang` tags come from the API's data. The page stays
 * `noindex` (the root layout's default) until the Admin turns on "Allow search engines".
 */
export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const { workspace, id } = await params;
  const sp = await searchParams;
  const raw = Array.isArray(sp.locale) ? sp.locale[0] : sp.locale;
  const asked = raw && isValidLocale(normalizeLocale(raw)) ? normalizeLocale(raw) : "";

  const [data, sitemap] = await Promise.all([
    fetchPublicArticle(workspace, id, asked),
    fetchHelpSitemap(workspace),
  ]);
  if (!data) return { title: "Help article" };

  const article = (data.article ?? data) as Article;
  const pick = <T,>(k: keyof Article) => (data[k] ?? article[k]) as T | undefined;
  const locale = pick<string>("locale") ?? asked;
  const defaultLocale = pick<string>("default_locale");
  const alternates = pick<{ locale: string; slug?: string | null }[]>("alternates") ?? [];

  const own = articleUrl(
    workspace,
    id,
    article.slug,
    locale && locale !== defaultLocale ? locale : "",
  );
  const languages: Record<string, string> = {};
  for (const a of alternates) {
    languages[toBcp47(a.locale)] = articleUrl(
      workspace,
      id,
      a.slug,
      a.locale === defaultLocale ? "" : a.locale,
    );
  }
  if (alternates.length > 0) {
    const def = alternates.find((a) => a.locale === defaultLocale);
    languages["x-default"] = articleUrl(workspace, id, def?.slug ?? article.slug, "");
  }

  return {
    title: article.meta_title || article.title,
    description: article.meta_description || bodyStart(article.body ?? ""),
    alternates: {
      canonical: article.canonical_url || own,
      ...(Object.keys(languages).length > 0 ? { languages } : {}),
    },
    robots: sitemap?.indexable ? { index: true, follow: true } : { index: false, follow: false },
  };
}

export default function Page() {
  return <HelpArticle />;
}
