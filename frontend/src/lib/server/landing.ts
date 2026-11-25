/**
 * Whether this deployment shows the CeDesk marketing page at `/`. Default on, so our cloud site
 * is unchanged. A company running its own desk sets `CEDESK_LANDING=off`: `/` then goes to the
 * sign-in page and robots.txt / the sitemap tell crawlers to stay away. Read per request.
 */
export function landingEnabled(source: Record<string, string | undefined> = process.env): boolean {
  const v = (source.CEDESK_LANDING ?? "").trim().toLowerCase();
  return !["off", "false", "0", "no"].includes(v);
}
