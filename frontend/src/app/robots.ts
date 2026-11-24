import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/site";

/**
 * The landing page at `/` is the only indexable page. Everything else is behind a sign-in or
 * a per-customer link, and the root layout sets `noindex` on it. Say the same in robots.txt so
 * well-behaved crawlers skip it. Revisit this if you make the public help center indexable.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/$", disallow: "/" },
    sitemap: `${siteUrl()}/sitemap.xml`,
    host: siteUrl(),
  };
}
