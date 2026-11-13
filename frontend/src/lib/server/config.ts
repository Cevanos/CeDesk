import "server-only";

import { parseEnv } from "@/lib/server/env";

/** Name of the httpOnly cookie that carries the backend bearer token. */
export const SESSION_COOKIE = "cdesk_session";
/**
 * Non-secret companion cookie (readable by the proxy only) so route guards
 * can tell "has a session" without ever exposing the token to JavaScript.
 */
export const SESSION_HINT_COOKIE = "cdesk_auth";
/**
 * The customer-portal token. A separate cookie because agent tokens don't work on
 * /portal routes and portal tokens don't work on agent routes: each only ever
 * goes to its own area.
 */
export const PORTAL_COOKIE = "cdesk_portal";

/** Largest upload the backend accepts (25 MB). Reject early, not upstream. */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/** Upstream call budget. */
export const UPSTREAM_TIMEOUT_MS = 30_000;

/**
 * The real backend address. Server-only by construction: `server-only` makes
 * any client import a build error, and the variable is not NEXT_PUBLIC_.
 */
export function upstreamBase(): string | null {
  const r = parseEnv();
  return r.ok ? r.env.CDESK_API_URL : null;
}

/** Credential endpoints are rate limited per client (default 10 tries a minute). */
export const RATE_LIMITED_PATHS = new Set([
  "auth/sign-in",
  "auth/forgot-password",
  "auth/reset-password",
]);
export const AUTH_RATE_WINDOW_MS = 60_000;
export function authRateLimit(): number {
  const r = parseEnv();
  return r.ok ? r.env.AUTH_RATE_LIMIT_PER_MINUTE : 10;
}

export function trustedProxyHops(): number {
  const r = parseEnv();
  return r.ok ? r.env.TRUSTED_PROXY_HOPS : 1;
}

/**
 * Only these top-level API areas are reachable through the proxy. Anything
 * else is a 404 before a request is ever made, so the proxy cannot be used as
 * an open relay to arbitrary backend routes.
 */
export const ALLOWED_AREAS = new Set([
  "auth",
  "me",
  "notifications",
  "tickets",
  "attachments",
  "contacts",
  "agents",
  "routing-defaults",
  "members",
  "teams",
  "ticket-types",
  "sla",
  "saved-replies",
  "rules",
  "macros",
  "email-addresses",
  "email",
  "outbox",
  "cevanos",
  "reports",
  "holidays",
  "triage-settings",
  "portal-settings",
  "article-categories",
  "articles",
  "imports",
  "stream",
  // Customer-facing areas (see isPublicPath / the portal cookie in the proxy).
  "portal",
  "public",
]);

/** Calls that are made without a session. */
export const PUBLIC_PATHS = new Set([
  "auth/sign-in",
  "auth/forgot-password",
  "auth/reset-password",
]);

const PORTAL_SIGN_IN = /^portal\/[^/]+\/(code|session)$/;

/** No token at all: the whole `public/` area plus the portal's code request and sign-in. */
export function isPublicPath(path: string): boolean {
  return PUBLIC_PATHS.has(path) || path.startsWith("public/") || PORTAL_SIGN_IN.test(path);
}

/** The portal's own area: it gets the portal cookie, never the agent one. */
export function isPortalPath(path: string): boolean {
  return path.startsWith("portal/");
}

/** Anyone can ask for a code, so it is rate limited like the other credential calls. */
export function isRateLimitedPath(path: string): boolean {
  return RATE_LIMITED_PATHS.has(path) || PORTAL_SIGN_IN.test(path);
}
