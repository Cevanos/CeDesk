/** Pure request checks for the API proxy, kept separate so they can be unit tested. */

import { isIP } from "node:net";

const SEGMENT = /^[A-Za-z0-9._~-]+$/;

/** True when every segment is a plain name and the first is an allowed area. */
export function isAllowedTarget(segments: string[], allowed: ReadonlySet<string>): boolean {
  const [first] = segments;
  if (first === undefined) return false;
  if (segments.some((s) => s === "." || s === ".." || !SEGMENT.test(s))) return false;
  return allowed.has(first);
}

/** CSRF defence in depth on top of SameSite=Lax: same-origin requests only. */
export function isSameOrigin(headers: Pick<Headers, "get">, host: string): boolean {
  const origin = headers.get("origin");
  if (!origin) return headers.get("sec-fetch-site") !== "cross-site";
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/**
 * The visitor's real IP to hand to the API as X-Forwarded-For, or null.
 * The API trusts whatever Next.js sends, so this only returns an address we really worked
 * out (a valid IP picked by `clientKey`); never "direct"/"unknown" and never a raw header.
 */
export function visitorIp(headers: Pick<Headers, "get">, trustedHops: number): string | null {
  if (trustedHops <= 0) return null;
  const key = clientKey(headers, trustedHops);
  return isIP(key) ? key : null;
}

/**
 * Identify the client for rate limiting.
 *
 * Each trusted proxy APPENDS the address it saw to X-Forwarded-For, so only the
 * entries added by our own proxies can be believed. The left-most entry is
 * whatever the client chose to send, so reading it would let an attacker dodge
 * the limit by rotating a fake header. We therefore count `trustedHops` entries
 * from the right. With 0 hops no forwarded header is trusted at all.
 */
export function clientKey(headers: Pick<Headers, "get">, trustedHops = 1): string {
  if (trustedHops <= 0) return "direct";
  const hops = (headers.get("x-forwarded-for") ?? "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);
  const picked = hops[Math.max(0, hops.length - trustedHops)];
  return picked || headers.get("x-real-ip")?.trim() || "unknown";
}
