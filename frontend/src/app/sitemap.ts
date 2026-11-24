import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/site";

/** Only the landing page is public and indexable; everything else is behind a sign-in. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: `${siteUrl()}/`, changeFrequency: "monthly", priority: 1 }];
}
