# 0002. Content-Security-Policy with a per-request nonce

- Status: accepted
- Context area: security headers

## Context

The first CSP allowed `script-src 'self' 'unsafe-inline'` because Next.js emits inline bootstrap scripts and so does the theme initialiser (`next-themes`). `unsafe-inline` for scripts removes most of the value of a CSP: an injected `<script>` would run.

## Decision

- `src/proxy.ts` generates a fresh nonce for every request and sets the CSP header (`src/lib/csp.ts`) with `script-src 'self' 'nonce-…' 'strict-dynamic'`.
- Next.js reads the nonce from the request's CSP header and stamps its own scripts. The root layout reads `x-nonce` and passes it to the theme provider.
- `'unsafe-eval'` is allowed in development only (React debugging).
- `style-src` keeps `'unsafe-inline'`: React `style` props and the emotion styles from `react-select` need it, and style injection is a much smaller risk than script injection.
- `connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'` are unchanged.

## Consequences

- Pages are rendered per request (they already are: they sit behind authentication), so static optimisation and CDN caching of HTML are not available. The API and static assets are unaffected.
- Any new inline script must receive the nonce, or it will be blocked. This is intended.
- Prefetch requests skip the proxy (matcher `missing`), which is the pattern recommended by Next.js; the full navigation still gets a CSP.
- Covered by `src/lib/csp.test.ts` and `src/proxy.test.ts`; e2e runs against a production build so a broken CSP fails CI.
