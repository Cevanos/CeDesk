import { NextResponse } from "next/server";

import { articleUrl, fetchHelpSitemap } from "@/lib/server/help-seo";

export const runtime = "nodejs";
// The data read below is cached for 5 minutes. Caching the finished file too would stack a second
// 5 minutes on top, so an "Allow search engines" switch-on could show an empty sitemap for ~10.
export const dynamic = "force-dynamic";

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/**
 * `/<workspace>/help/sitemap.xml`, built from the API's sitemap. While the Admin has not
 * allowed search engines (`indexable` is false) the sitemap is empty.
 */
export async function GET(_req: Request, ctx: { params: Promise<{ workspace: string }> }) {
  const { workspace } = await ctx.params;
  const map = await fetchHelpSitemap(workspace);
  const entries = map?.indexable ? map.entries : [];

  const urls = entries
    .filter((e) => e.id)
    .map((e) => {
      const loc = articleUrl(workspace, e.id!, e.slug, e.locale);
      const last = e.lastmod ?? e.updated_at;
      return `<url><loc>${esc(loc)}</loc>${last ? `<lastmod>${esc(last)}</lastmod>` : ""}</url>`;
    })
    .join("");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>\n`;
  return new NextResponse(xml, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
}
