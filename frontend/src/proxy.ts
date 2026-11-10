import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, makeNonce } from "@/lib/csp";
import { safeNext } from "@/lib/safe-next";

/**
 * Route guard + CSP (Next 16 `proxy`, formerly middleware).
 *
 * Guard: optimistic check only. It looks for the session cookie so signed-out
 * users never see an app screen flash. The real authority is the backend, which
 * rejects a bad token with 401 and the API proxy then clears the cookie.
 *
 * CSP: a fresh nonce per request. Next.js reads it from the request's CSP
 * header and stamps its own scripts; `x-nonce` hands it to our layout.
 */
const SESSION_COOKIE = "cdesk_session";
const AUTH_PAGES = ["/login", "/forgot-password", "/reset-password"];
const isDev = process.env.NODE_ENV !== "production";

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const nonce = makeNonce();
  const csp = buildCsp(nonce, isDev);

  const withCsp = (res: NextResponse) => {
    res.headers.set("Content-Security-Policy", csp);
    return res;
  };

  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const isAuthPage = AUTH_PAGES.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  // Reset links are opened from email and must work even while signed in.
  const isReset = pathname.startsWith("/reset-password");

  if (!hasSession && !isAuthPage) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    if (pathname !== "/") url.searchParams.set("next", pathname + req.nextUrl.search);
    return withCsp(NextResponse.redirect(url));
  }

  if (hasSession && isAuthPage && !isReset) {
    // A just-signed-in user may still hit /login?next=...; honour a safe `next`.
    const target = safeNext(req.nextUrl.searchParams.get("next"));
    return withCsp(NextResponse.redirect(new URL(target, req.url)));
  }

  const headers = new Headers(req.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  return withCsp(NextResponse.next({ request: { headers } }));
}

export const config = {
  // Skip the API proxy (it has its own checks), Next internals and static files.
  matcher: [
    {
      source:
        "/((?!api|_next/static|_next/image|favicon.ico|.*\.(?:svg|png|jpg|jpeg|webp|ico|txt|xml)$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
