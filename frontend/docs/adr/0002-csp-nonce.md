# 0002. Content-Security-Policy with a per-request nonce

- Status: accepted
- Area: security headers

## Context

The first CSP used `script-src 'self' 'unsafe-inline'` because Next.js emits inline bootstrap scripts, and so does the theme initialiser (`next-themes`). With `unsafe-inline` an injected `<script>` would run, which defeats most of the point of a CSP.

## Decision

- `src/proxy.ts` generates a new nonce for every request and sets the CSP header (`src/lib/csp.ts`) with `script-src 'self' 'nonce-...' 'strict-dynamic'`.
- Next.js reads the nonce from the request's CSP header and applies it to its own scripts. The root layout reads `x-nonce` and passes it to the theme provider.
- `'unsafe-eval'` is allowed in development only (React debugging).
- `style-src` keeps `'unsafe-inline'`. React `style` props and the emotion styles from `react-select` need it, and style injection is a much smaller risk than script injection.
- `connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'` and `form-action 'self'` are unchanged.

## Consequences

- Pages are rendered per request. They already were, since they sit behind authentication, but it rules out static optimisation and CDN caching of HTML. The API and static assets are unaffected.
- Any new inline script needs the nonce or it will be blocked. That is intended.
- Prefetch requests skip the proxy (matcher `missing`), as Next.js recommends. The full navigation still gets a CSP.
- Covered by `src/lib/csp.test.ts` and `src/proxy.test.ts`. E2E runs against a production build, so a broken CSP fails CI.
