import { NextResponse, type NextRequest } from "next/server";

import {
  ALLOWED_AREAS,
  AUTH_RATE_WINDOW_MS,
  authRateLimit,
  MAX_UPLOAD_BYTES,
  PORTAL_COOKIE,
  isPortalPath,
  isPublicPath,
  isRateLimitedPath,
  SESSION_COOKIE,
  SESSION_HINT_COOKIE,
  UPSTREAM_TIMEOUT_MS,
  trustedProxyHops,
  upstreamBase,
} from "@/lib/server/config";
import { clientKey, isAllowedTarget, isSameOrigin } from "@/lib/server/proxy-guards";
import { createLimiter } from "@/lib/server/rate-limit";

/**
 * BFF proxy: the browser hits /api/<route> here, we call ${CDESK_API_URL}/<route>.
 * The bearer token stays in an httpOnly cookie so page JS (and XSS) never sees
 * it, and the backend address is never sent to the browser, even in errors.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

let authLimiter: ReturnType<typeof createLimiter> | undefined;
/** Created on first use so the limit is read from the validated environment. */
const limiter = () => (authLimiter ??= createLimiter(authRateLimit(), AUTH_RATE_WINDOW_MS));

/** Request headers worth forwarding. Everything else (cookie, host,
 *  authorization, x-forwarded-*) is dropped on purpose. */
const FORWARD_REQUEST = [
  "content-type",
  "accept",
  "if-match",
  "accept-language",
  "idempotency-key",
];
/** Response headers the UI needs (ETag for edits, Retry-After for 429, ...). */
const FORWARD_RESPONSE = [
  "content-type",
  "etag",
  "retry-after",
  "x-request-id",
  "content-disposition",
  "idempotent-replayed",
];

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function errorBody(code: string, message: string, status: number) {
  return NextResponse.json({ error: { code, message } }, { status });
}

function clearSession(res: NextResponse) {
  for (const name of [SESSION_COOKIE, SESSION_HINT_COOKIE]) {
    res.cookies.set(name, "", { path: "/", maxAge: 0 });
  }
  return res;
}

async function handle(req: NextRequest, { params }: Ctx) {
  const { path: segments } = await params;

  if (!isAllowedTarget(segments, ALLOWED_AREAS)) {
    return errorBody("NotFound", "Not found.", 404);
  }
  const path = segments.join("/");

  // The browser's Origin is compared with the Host it actually requested. nextUrl.host can
  // be the server's bind address (e.g. localhost:6543) rather than the public host.
  const publicHost = req.headers.get("host") ?? req.nextUrl.host;
  if (MUTATING.has(req.method) && !isSameOrigin(req.headers, publicHost)) {
    return errorBody("Forbidden", "Cross-site request blocked.", 403);
  }

  if (isRateLimitedPath(path)) {
    const verdict = limiter().hit(`${path}:${clientKey(req.headers, trustedProxyHops())}`);
    if (!verdict.ok) {
      const res = errorBody("RateLimited", "Too many tries. Please wait a moment.", 429);
      res.headers.set("retry-after", String(verdict.retryAfter));
      return res;
    }
  }

  const base = upstreamBase();
  if (!base) {
    console.error("CDESK_API_URL is not configured");
    return errorBody("Unavailable", "The service is not configured.", 503);
  }

  const isPublic = isPublicPath(path);
  const portal = isPortalPath(path);
  // Each token only ever goes to its own area: portal calls carry the customer's
  // token, everything else the agent's.
  const token = req.cookies.get(portal ? PORTAL_COOKIE : SESSION_COOKIE)?.value;
  if (!isPublic && !token) {
    const res = errorBody("NotSignedIn", "Please sign in.", 401);
    return portal ? res : clearSession(res);
  }

  const headers = new Headers();
  for (const h of FORWARD_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (token && !isPublic) headers.set("authorization", `Bearer ${token}`);

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    const declared = Number(req.headers.get("content-length") ?? 0);
    if (declared > MAX_UPLOAD_BYTES) {
      return errorBody("TooLarge", "That file is larger than 25 MB.", 413);
    }
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_UPLOAD_BYTES) {
      return errorBody("TooLarge", "That file is larger than 25 MB.", 413);
    }
  }

  const url = `${base}/${path}${req.nextUrl.search}`;

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (err) {
    // Log server-side only; never echo the upstream address to the client.
    console.error("upstream request failed", path, (err as Error).name);
    return errorBody(
      "Unavailable",
      "We couldn't reach the service. Please try again in a moment.",
      502,
    );
  }

  const out = new Headers();
  for (const h of FORWARD_RESPONSE) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set("cache-control", "no-store");

  // Portal sign-in: same idea with the customer's own cookie.
  if (/^portal\/[^/]+\/session$/.test(path) && req.method === "POST" && upstream.ok) {
    const data = (await upstream.json().catch(() => null)) as {
      token?: string;
      expires_at?: string;
      [k: string]: unknown;
    } | null;
    if (!data?.token) {
      return errorBody("Internal", "Unexpected response from the service.", 502);
    }
    const { token: portalToken, ...rest } = data;
    const expires = data.expires_at ? new Date(data.expires_at) : undefined;
    const res = NextResponse.json(rest, {
      headers: upstream.headers.get("x-request-id")
        ? { "x-request-id": upstream.headers.get("x-request-id")! }
        : undefined,
    });
    res.cookies.set(PORTAL_COOKIE, portalToken, {
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production" && process.env.ALLOW_INSECURE_HTTP !== "1",
      httpOnly: true,
      ...(expires && !Number.isNaN(expires.valueOf()) ? { expires } : {}),
    });
    return res;
  }

  // Sign-in: move the token into an httpOnly cookie and drop it from the body.
  if (path === "auth/sign-in" && req.method === "POST" && upstream.ok) {
    const data = (await upstream.json().catch(() => null)) as {
      token?: string;
      expires_at?: string;
      member_id?: string;
      role?: string;
    } | null;
    if (!data?.token) {
      return errorBody("Internal", "Unexpected response from the service.", 502);
    }
    const expires = data.expires_at ? new Date(data.expires_at) : undefined;
    const valid = expires && !Number.isNaN(expires.valueOf()) ? expires : undefined;
    const res = NextResponse.json(
      { member_id: data.member_id, role: data.role, expires_at: data.expires_at },
      {
        headers: upstream.headers.get("x-request-id")
          ? { "x-request-id": upstream.headers.get("x-request-id")! }
          : undefined,
      },
    );
    const cookieOpts = {
      path: "/",
      sameSite: "lax" as const,
      secure: process.env.NODE_ENV === "production" && process.env.ALLOW_INSECURE_HTTP !== "1",
      httpOnly: true,
      ...(valid ? { expires: valid } : {}),
    };
    res.cookies.set(SESSION_COOKIE, data.token, cookieOpts);
    res.cookies.set(SESSION_HINT_COOKIE, "1", cookieOpts);
    return res;
  }

  const payload = [204, 205, 304].includes(upstream.status) ? null : await upstream.arrayBuffer();
  const res = new NextResponse(payload, { status: upstream.status, headers: out });

  // Sign-out always ends the local session; a 401 means the token is dead.
  if (portal) {
    if (path === "portal/sign-out" || (upstream.status === 401 && !isPublic)) {
      res.cookies.set(PORTAL_COOKIE, "", { path: "/", maxAge: 0 });
    }
  } else if (path === "auth/sign-out" || (upstream.status === 401 && !isPublic)) {
    clearSession(res);
  }
  return res;
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
