import { NextResponse, type NextRequest } from "next/server";

import {
  ALLOWED_AREAS,
  AUTH_RATE_WINDOW_MS,
  authRateLimit,
  RATE_LIMITED_PATHS,
  MAX_UPLOAD_BYTES,
  PUBLIC_PATHS,
  SESSION_COOKIE,
  SESSION_HINT_COOKIE,
  UPSTREAM_TIMEOUT_MS,
  trustedProxyHops,
  upstreamBase,
} from "@/lib/server/config";
import { clientKey, isAllowedTarget, isSameOrigin } from "@/lib/server/proxy-guards";
import { createLimiter } from "@/lib/server/rate-limit";

/**
 * Backend-for-frontend proxy.
 *
 *  - The browser calls  /api/<route>  on THIS origin.
 *  - This handler calls  ${CDESK_API_URL}/<route>  server to server.
 *  - The bearer token lives in an httpOnly cookie. It is attached here and is
 *    never returned to (or readable by) page JavaScript, so XSS cannot steal it.
 *  - The backend address is never sent to the browser, not even in errors.
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

  // ---- 1. validate the target ------------------------------------------
  if (!isAllowedTarget(segments, ALLOWED_AREAS)) {
    return errorBody("NotFound", "Not found.", 404);
  }
  const path = segments.join("/");

  if (MUTATING.has(req.method) && !isSameOrigin(req.headers, req.nextUrl.host)) {
    return errorBody("Forbidden", "Cross-site request blocked.", 403);
  }

  if (RATE_LIMITED_PATHS.has(path)) {
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

  const isPublic = PUBLIC_PATHS.has(path);
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!isPublic && !token) {
    return clearSession(errorBody("NotSignedIn", "Please sign in.", 401));
  }

  // ---- 2. build the upstream request -----------------------------------
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

  // ---- 3. shape the response -------------------------------------------
  const out = new Headers();
  for (const h of FORWARD_RESPONSE) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set("cache-control", "no-store");

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
      secure: process.env.NODE_ENV === "production",
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
  if (path === "auth/sign-out" || (upstream.status === 401 && !isPublic)) {
    clearSession(res);
  }
  return res;
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
