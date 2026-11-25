import type { MetadataRoute } from "next";

import { landingEnabled } from "@/lib/server/landing";
import { siteUrl } from "@/lib/site";

// Read CEDESK_LANDING when the file is requested, not when the image is built.
export const dynamic = "force-dynamic";

/** Only the landing page is public and indexable; everything else is behind a sign-in. */
export default function sitemap(): MetadataRoute.Sitemap {
  if (!landingEnabled()) return [];
  return [{ url: `${siteUrl()}/`, changeFrequency: "monthly", priority: 1 }];
}
