/**
 * First path segments that belong to the agent app. A workspace slug can never be one of
 * these, which keeps `/settings/portal` (an Admin screen) from looking like the
 * customer portal of a workspace called "settings".
 */
const AGENT_AREAS = new Set([
  "account",
  "agents",
  "api",
  "archive",
  "customers",
  "dev",
  "forgot-password",
  "login",
  "rate",
  "reports",
  "reset-password",
  "settings",
  "signup",
  "sso",
  "tickets",
]);

/**
 * Pages customers open without an agent login. The backend's emails link to
 * `/rate` and `/<workspace>/tickets/<id>`, so these must never redirect to /login.
 *
 * Agent pages are all one or two segments (`/tickets/T`, `/settings/sla`), so the
 * three-segment `/<workspace>/tickets/<id>` cannot clash with them.
 */
export function isPublicPage(pathname: string): boolean {
  if (pathname === "/rate" || pathname.startsWith("/rate/")) return true;
  // Where the API sends the browser after single sign-on; the page itself reads the token.
  if (pathname === "/sso/callback" || pathname === "/sso/callback/") return true;
  const [, first = ""] = pathname.split("/");
  if (AGENT_AREAS.has(first)) return false;
  if (/^\/[^/]+\/tickets\/[^/]+\/?$/.test(pathname)) return true;
  return /^\/[^/]+\/(portal|contact|help)(\/.*)?$/.test(pathname);
}
