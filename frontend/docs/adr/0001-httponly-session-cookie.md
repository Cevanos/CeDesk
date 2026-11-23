# 0001. Keep the session token in an httpOnly cookie behind a server-side proxy

- Status: accepted
- Area: authentication, data flow

## Context

The original (internal) frontend integration guide, section 3.1, suggested keeping the bearer token in memory or sessionStorage and sending it as `Authorization: Bearer ...` from the browser. Any script on the page can read both, so one XSS bug would let an attacker steal a working session. The backend is also on a different origin, which exposes its address and needs CORS.

## Decision

- The browser only talks to this app's own origin under `/api/*`.
- A route handler (`src/app/api/[...path]/route.ts`) forwards to `CEDESK_API_URL` server to server.
- On sign-in, the token from the backend is stored in an httpOnly, `SameSite=Lax` cookie (`Secure` in production) and removed from the response body. The proxy adds the `Authorization: Bearer ...` header itself.
- `src/proxy.ts` only checks that the session cookie exists, so signed-out users never see an app screen. The backend stays the authority, and a 401 clears the cookie.
- CSRF is covered by SameSite plus a same-origin check on mutating calls.

## Consequences

- JavaScript can't read the token, so XSS can't exfiltrate it. It can still make requests as the user while the page is open, which is why the CSP matters too (see ADR 0002).
- The backend needs no CORS for this app, and its URL never reaches the browser.
- The backend still sees a normal Bearer header, so no API change is needed.
- Each call takes one extra hop, and there is a small server to run.
- Sign-in attempts reach the backend from the frontend server's IP. The proxy applies its own per-client rate limit and forwards no client IP, so the backend must not rely on source IP for its own lockout.

## Deviation from the guide

This differs from section 3.1 on purpose, for security. Check with the API owner if the guide's wording is a hard requirement.
