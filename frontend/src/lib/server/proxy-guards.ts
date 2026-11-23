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

/**
 * CSRF defence in depth on top of SameSite=Lax: same-origin requests only.
 * Without an Origin header, Fetch Metadata decides: `same-site` (a sibling subdomain) and
 * `cross-site` are refused. Only browsers send Sec-Fetch-Site, so server-to-server callers
 * without either header still pass; they carry no ambient cookies to abuse.
 */
export function isSameOrigin(headers: Pick<Headers, "get">, host: string): boolean {
  const origin = headers.get("origin");
  if (!origin) {
    const site = headers.get("sec-fetch-site");
    return site === null || site === "same-origin" || site === "none";
  }
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

/** Raised when a request body grows past the allowed size. */
export class BodyTooLargeError extends Error {
  constructor() {
    super("Request body too large");
    this.name = "BodyTooLargeError";
  }
}

/**
 * Reads a request body but stops, and throws, as soon as it passes `maxBytes`.
 * `Content-Length` can be absent (chunked uploads) or wrong, so the real byte count is
 * what is enforced; the body is never fully buffered past the limit.
 */
export async function readBodyCapped(
  body: ReadableStream<Uint8Array> | null,
  maxBytes: number,
): Promise<Uint8Array<ArrayBuffer>> {
  if (!body) return new Uint8Array(0);
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => undefined);
        throw new BodyTooLargeError();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

/**
 * An identity-provider redirect is only passed to the browser when it is an absolute
 * https URL (http only when `allowHttp`, for local test servers). Anything else, such as
 * `javascript:` or a protocol-relative address, is dropped.
 */
export function safeRedirectLocation(location: string | null, allowHttp = false): string | null {
  if (!location) return null;
  try {
    const url = new URL(location);
    const ok = url.protocol === "https:" || (allowHttp && url.protocol === "http:");
    return ok && url.host ? url.toString() : null;
  } catch {
    return null;
  }
}
