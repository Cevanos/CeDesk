import type { MetadataRoute } from "next";

/**
 * Everything in this app is behind a sign-in or a per-customer link, and the root layout
 * already sets `noindex`. Say the same in robots.txt so well-behaved crawlers skip it.
 * Revisit this (and add a sitemap) if you decide to make the public help center indexable.
 */
export default function robots(): MetadataRoute.Robots {
  return { rules: { userAgent: "*", disallow: "/" } };
}
