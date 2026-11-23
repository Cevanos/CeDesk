# 0003. Cloudflare Turnstile on the sign-up page only

- Status: accepted
- Area: security headers, public routes

## Context

Self-service sign-up (`/signup`) lets anyone create a workspace, so a hosted server protects it with a CAPTCHA. The backend names the provider through `GET /auth/signup-config` (`captcha_site_key`, or `null` for self-hosted setups with no CAPTCHA). The provider is Cloudflare Turnstile. It loads a script from `https://challenges.cloudflare.com` and renders its challenge in an iframe from the same origin. Our CSP (ADR 0002) allows neither: `frame-src 'none'`, and `script-src` is limited to `'self'` plus the request nonce.

## Decision

- `buildCsp(nonce, isDev, { turnstile: true })` adds `https://challenges.cloudflare.com` to `script-src` and replaces `frame-src 'none'` with `frame-src https://challenges.cloudflare.com`.
- `src/proxy.ts` sets that option only when `pathname === "/signup"`. Every other page keeps the strict policy from ADR 0002.
- The Turnstile loader is added by `signup-form.tsx` with the request nonce (`x-nonce`, read by the server page). `'strict-dynamic'` lets that trusted loader add its own scripts.
- The widget is only loaded when the server returns a `captcha_site_key`. With `null`, no third-party script or frame is requested at all.
- `connect-src` is unchanged: the token goes to our own `/api/auth/signup`, never directly to a third party.
- Other sign-up proxy changes: the four `auth/signup*` calls are public (no session) and all but `signup-config` are rate limited like the other credential calls. `signup` is a reserved first path segment, so `/signup` is never read as a workspace's portal.

## Consequences

- One page trusts one extra origin. A compromise of that origin could run script on `/signup`, which has a password field. That is the trade for bot protection on a public form; the signed-in app is not affected.
- `connect-src`, `form-action` and `frame-ancestors` do not widen, so the token and form data cannot be sent elsewhere by the policy's extra allowance.
- Covered by `src/lib/csp.test.ts` (widened on `/signup`, unchanged elsewhere) and `src/proxy.test.ts`.
