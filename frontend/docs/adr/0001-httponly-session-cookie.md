# 0001. Keep the session token in an httpOnly cookie behind a server-side proxy

- Status: accepted
- Context area: authentication, data flow

## Context

`refDoc/CDESK_FRONTEND_GUIDE.md` §3.1 suggests keeping the bearer token "in memory or sessionStorage" and sending it as `Authorization: Bearer …` from the browser. Both are readable by any script running on the page, so a single XSS bug would let an attacker steal a working session. The backend is also on a different origin, which would expose its address and need CORS.

## Decision

- The browser only talks to this app's own origin under `/api/*`.
- A route handler (`src/app/api/[...path]/route.ts`) forwards to `CDESK_API_URL` server to server.
- On sign-in, the token returned by the backend is stored in an **httpOnly, SameSite=Lax cookie** (`Secure` in production) and removed from the response body. The proxy adds `Authorization: Bearer …` itself.
- `src/proxy.ts` only checks that the session cookie exists (an optimistic guard so signed-out users never see an app screen). The backend stays the authority, and a 401 clears the cookie.
- CSRF is covered by SameSite plus a same-origin check on mutating calls.

## Consequences

- JavaScript can never read the token, so XSS cannot exfiltrate it (it can still make requests as the user while the page is open, which is why the CSP also matters; see ADR 0002).
- The backend does not need CORS for this app and its URL never reaches the browser.
- The backend still sees an ordinary Bearer header, so no API change is needed.
- One extra hop per call and a small server footprint to operate.
- Sign-in attempts reach the backend from the frontend server's IP, so the proxy applies its own per-client rate limit and forwards no client IP; the backend must not rely on source IP for its own lockout.

## Deviation from the guide

This intentionally deviates from §3.1 for security. Confirm with the API owner if the guide's wording is a hard requirement.
