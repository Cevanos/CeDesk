import { NextResponse, type NextRequest } from "next/server";

import {
  ALLOWED_AREAS,
  AUTH_RATE_WINDOW_MS,
  authRateLimit,
  MAX_UPLOAD_BYTES,
  PENDING_2FA_COOKIE,
  PENDING_2FA_MAX_AGE_S,
  PENDING_2FA_PATH,
  PORTAL_COOKIE,
  isPortalPath,
  isPublicPath,
  isRateLimitedPath,
  isSsoStart,
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
  // Lets a live-update stream resume where it left off after a reconnect.
  "last-event-id",
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

const secureCookies = () =>
  process.env.NODE_ENV === "production" && process.env.ALLOW_INSECURE_HTTP !== "1";

/** The one place that sets the signed-in cookies, so every way of signing in matches. */
function setSessionCookies(res: NextResponse, token: string, expiresAt?: string) {
  const expires = expiresAt ? new Date(expiresAt) : undefined;
  const cookieOpts = {
    path: "/",
    sameSite: "lax" as const,
    secure: secureCookies(),
    httpOnly: true,
    ...(expires && !Number.isNaN(expires.valueOf()) ? { expires } : {}),
  };
  res.cookies.set(SESSION_COOKIE, token, cookieOpts);
  res.cookies.set(SESSION_HINT_COOKIE, "1", cookieOpts);
}

/** What an SSO session token looks like: opaque, URL-safe, bounded. */
const SSO_TOKEN = /^[A-Za-z0-9._~+/=-]{16,2048}$/;

/**
 * SSO hand-off: the API redirected the browser to /sso/callback#token=… and the page posts the
 * token here. It is checked with the API before it becomes a cookie. It is never logged or echoed.
 */
async function ssoSession(req: NextRequest, base: string) {
  const bad = () => errorBody("NotSignedIn", "That sign-in is not valid. Start again.", 401);
  const body = (await req.json().catch(() => null)) as {
    token?: unknown;
    expires_at?: unknown;
  } | null;
  const token = body?.token;
  if (typeof token !== "string" || !SSO_TOKEN.test(token)) {
    return errorBody("BadRequest", "That sign-in is not valid. Start again.", 400);
  }
  const expiresAt = typeof body?.expires_at === "string" ? body.expires_at : undefined;

  let me: Response;
  try {
    me = await fetch(`${base}/me`, {
      headers: { authorization: `Bearer ${token}`, accept: "application/json" },
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return errorBody("Unavailable", "We couldn't reach the service. Please try again.", 502);
  }
  if (me.status === 401 || me.status === 403) return bad();
  if (!me.ok)
    return errorBody("Unavailable", "We couldn't reach the service. Please try again.", 502);
  const data = (await me.json().catch(() => null)) as {
    member_id?: string;
    id?: string;
    role?: string;
  } | null;
  if (!data) return bad();

  const res = NextResponse.json({ member_id: data.member_id ?? data.id, role: data.role });
  setSessionCookies(res, token, expiresAt);
  return res;
}

/** Pending 2FA cookie value: "<password_expired 0|1>.<token>". Never readable by page JS. */
function packPending(token: string, passwordExpired: boolean) {
  return `${passwordExpired ? 1 : 0}.${token}`;
}
function unpackPending(v: string | undefined) {
  if (!v || v.length < 3 || v[1] !== ".") return null;
  return { passwordExpired: v[0] === "1", token: v.slice(2) };
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

  if (path === "auth/sso-session" && req.method === "POST") return ssoSession(req, base);

  const isPublic = isPublicPath(path);
  const portal = isPortalPath(path);
  // Each token only ever goes to its own area: portal calls carry the customer's
  // token, everything else the agent's.
  // The second sign-in step is the one call that carries the half-signed-in token: it comes
  // from its own cookie (only sent to this path), never from the session cookie.
  const twoFactor = path === "auth/2fa" && req.method === "POST";
  const pending = twoFactor ? unpackPending(req.cookies.get(PENDING_2FA_COOKIE)?.value) : null;
  if (twoFactor && !pending) {
    return errorBody("NotSignedIn", "That sign-in has expired. Start again.", 401);
  }
  const token = twoFactor
    ? pending?.token
    : req.cookies.get(portal ? PORTAL_COOKIE : SESSION_COOKIE)?.value;
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
  // Live-update streams stay open for as long as the tab does: no 30 s cut-off.
  const isStream = req.method === "GET" && (path === "stream" || path === "portal/stream");

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: isStream ? req.signal : AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
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

  // Single sign-on start: the answer is a redirect to the identity provider. Only the
  // Location is passed on, and only for this one route.
  if (isSsoStart(path) && req.method === "GET" && upstream.status >= 300 && upstream.status < 400) {
    const location = upstream.headers.get("location");
    if (location) {
      out.set("location", location);
      return new NextResponse(null, { status: 302, headers: out });
    }
  }

  // Server-Sent Events: hand the body through as it arrives. Never buffer it, never
  // compress it, and don't treat a refused ticket as an ended session.
  if (isStream && upstream.ok && upstream.body) {
    out.set("content-type", "text/event-stream");
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
    out.set("content-encoding", "identity");
    return new NextResponse(upstream.body, { status: upstream.status, headers: out });
  }

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

  // Sign-in (and its 2FA second step): move the token into an httpOnly cookie and drop it
  // from the body.
  if ((path === "auth/sign-in" || twoFactor) && req.method === "POST" && upstream.ok) {
    let data = (await upstream.json().catch(() => null)) as {
      token?: string;
      expires_at?: string;
      member_id?: string;
      role?: string;
      two_factor_required?: boolean;
      password_expired?: boolean;
    } | null;
    // The second step may answer without a token: the same one is now fully signed in.
    if (twoFactor && !data) data = {};
    if (twoFactor && data && !data.token) data.token = pending?.token;
    if (twoFactor && data && pending?.passwordExpired) data.password_expired = true;
    if (!data?.token) {
      return errorBody("Internal", "Unexpected response from the service.", 502);
    }
    // Password was right but a code is still needed: this token is NOT a session. Park it in
    // the path-limited pending cookie (10 minutes) and tell the page to ask for the code.
    if (path === "auth/sign-in" && data.two_factor_required) {
      const res = NextResponse.json(
        { two_factor_required: true, password_expired: data.password_expired === true },
        {
          headers: upstream.headers.get("x-request-id")
            ? { "x-request-id": upstream.headers.get("x-request-id")! }
            : undefined,
        },
      );
      res.cookies.set(PENDING_2FA_COOKIE, packPending(data.token, data.password_expired === true), {
        path: PENDING_2FA_PATH,
        sameSite: "strict",
        secure: secureCookies(),
        httpOnly: true,
        maxAge: PENDING_2FA_MAX_AGE_S,
      });
      return res;
    }
    const res = NextResponse.json(
      {
        member_id: data.member_id,
        role: data.role,
        expires_at: data.expires_at,
        ...(data.password_expired ? { password_expired: true } : {}),
      },
      {
        headers: upstream.headers.get("x-request-id")
          ? { "x-request-id": upstream.headers.get("x-request-id")! }
          : undefined,
      },
    );
    setSessionCookies(res, data.token, data.expires_at);
    if (twoFactor) {
      res.cookies.set(PENDING_2FA_COOKIE, "", { path: PENDING_2FA_PATH, maxAge: 0 });
    }
    return res;
  }

  const payload = [204, 205, 304].includes(upstream.status) ? null : await upstream.arrayBuffer();
  const res = new NextResponse(payload, { status: upstream.status, headers: out });

  // Sign-out always ends the local session; a 401 means the token is dead.
  if (isStream) {
    // A refused stream ticket (bad, used or expired) must not sign the person out.
  } else if (portal) {
    if (path === "portal/sign-out" || (upstream.status === 401 && !isPublic)) {
      res.cookies.set(PORTAL_COOKIE, "", { path: "/", maxAge: 0 });
    }
  } else if (path === "auth/sign-out" || (upstream.status === 401 && !isPublic)) {
    clearSession(res);
  }
  return res;
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
