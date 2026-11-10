import "server-only";

import { parseEnv } from "@/lib/server/env";

/** Name of the httpOnly cookie that carries the backend bearer token. */
export const SESSION_COOKIE = "cdesk_session";
/**
 * Non-secret companion cookie (readable by the proxy only) so route guards
 * can tell "has a session" without ever exposing the token to JavaScript.
 */
export const SESSION_HINT_COOKIE = "cdesk_auth";

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
]);

/** Calls that are made without a session. */
export const PUBLIC_PATHS = new Set([
  "auth/sign-in",
  "auth/forgot-password",
  "auth/reset-password",
]);
