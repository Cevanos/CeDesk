/**
 * Public-facing site facts used by the landing page's SEO: metadata, sitemap, robots and
 * structured data. `SITE_URL` is a server-side setting; set it in production so canonical
 * and social-card URLs point at the real address.
 */
export const SITE_NAME = "CeDesk";

export const SITE_TITLE = "CeDesk: AI Help Desk Software & Customer Support Ticketing System";

export const SITE_DESCRIPTION =
  "CeDesk is help desk software with a shared inbox, SLA management, smart ticket routing, automation, a customer portal and help center, and AI-to-human handover with full context. Open source and self-hostable.";

export const SITE_KEYWORDS = [
  "help desk software",
  "customer support software",
  "ticketing system",
  "AI help desk",
  "AI customer support",
  "shared inbox",
  "SLA management",
  "ticket routing",
  "support automation",
  "customer portal",
  "help center software",
  "knowledge base software",
  "open source help desk",
  "self-hosted help desk",
  "AI to human handover",
  "customer service software",
  "CSAT surveys",
  "support ticket management",
];

export function siteUrl(): string {
  const raw = process.env.SITE_URL?.trim().replace(/\/+$/, "");
  return raw && /^https?:\/\//.test(raw) ? raw : "http://localhost:3000";
}
