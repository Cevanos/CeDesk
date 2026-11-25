import type { MetadataRoute } from "next";

import { landingEnabled } from "@/lib/server/landing";
import { siteUrl } from "@/lib/site";

// Read CEDESK_LANDING when the file is requested, not when the image is built.
export const dynamic = "force-dynamic";

/**
 * The landing page at `/` is the only indexable page. Everything else is behind a sign-in or
 * a per-customer link, and the root layout sets `noindex` on it. Say the same in robots.txt so
 * well-behaved crawlers skip it. Revisit this if you make the public help center indexable.
 * With `CEDESK_LANDING=off` there is no landing page, so nothing is indexable.
 */
export default function robots(): MetadataRoute.Robots {
  if (!landingEnabled()) return { rules: { userAgent: "*", disallow: "/" } };
  return {
    rules: { userAgent: "*", allow: "/$", disallow: "/" },
    sitemap: `${siteUrl()}/sitemap.xml`,
    host: siteUrl(),
  };
}
