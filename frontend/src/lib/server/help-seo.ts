import "server-only";

import { upstreamBase } from "@/lib/server/config";
import { siteUrl } from "@/lib/site";

/**
 * Server-side reads for the public help center's SEO tags and sitemap. The API only gives the
 * data; the tags are ours. Both calls are public (no cookie) and cached for 5 minutes, the same
 * as the API's own sitemap cache.
 */

export interface SitemapEntry {
  id?: string;
  slug?: string | null;
  locale?: string | null;
  lastmod?: string | null;
  updated_at?: string | null;
  alternates?: { locale: string; slug?: string | null }[];
}
export interface HelpSitemap {
  indexable: boolean;
  entries: SitemapEntry[];
  truncated?: boolean;
}

const SAFE_SEG = /^[A-Za-z0-9._~-]{1,200}$/;

async function getJson<T>(path: string): Promise<T | null> {
  const base = upstreamBase();
  if (!base) return null;
  try {
    const res = await fetch(`${base}/${path}`, {
      headers: { Accept: "application/json" },
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(5000),
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export const isSafeSegment = (s: string) => SAFE_SEG.test(s);

export function fetchHelpSitemap(ws: string): Promise<HelpSitemap | null> {
  if (!isSafeSegment(ws)) return Promise.resolve(null);
  return getJson<HelpSitemap>(`public/${encodeURIComponent(ws)}/sitemap`);
}

export function fetchPublicArticle(ws: string, id: string, locale: string) {
  if (!isSafeSegment(ws) || !isSafeSegment(id)) return Promise.resolve(null);
  const q = locale ? `?locale=${encodeURIComponent(locale)}` : "";
  return getJson<Record<string, unknown>>(
    `public/${encodeURIComponent(ws)}/articles/${encodeURIComponent(id)}${q}`,
  );
}

/** Own address of an article page; the `locale` query parameter picks the language. */
export function articleUrl(ws: string, id: string, slug?: string | null, locale?: string | null) {
  const base = `${siteUrl()}/${encodeURIComponent(ws)}/help/${encodeURIComponent(id)}`;
  const path = slug ? `${base}/${encodeURIComponent(slug)}` : base;
  return locale ? `${path}?locale=${encodeURIComponent(locale)}` : path;
}
