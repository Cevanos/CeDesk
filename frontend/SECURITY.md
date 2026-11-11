# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub: **Security → Report a vulnerability** on this
repository (private vulnerability reporting). Include the affected area, steps to
reproduce, and the impact you observed.

We aim to acknowledge reports within 3 business days and to give a remediation
plan within 10 business days. Please allow us time to fix the issue before any
public disclosure.

## Supported versions

Only the latest release on `main` receives security fixes.

## Security model (summary)

- The browser never talks to the backend directly. All calls go through the
  server-side proxy at `/api/*`; the backend address is never sent to clients.
- The session token lives in an `httpOnly`, `SameSite=Lax`, `Secure` (production)
  cookie, so page JavaScript (and therefore XSS) cannot read it.
- Mutating proxy calls require a same-origin request (CSRF defence in depth).
- The proxy only reaches an allow-listed set of backend areas and rejects path
  traversal, so it cannot be used as an open relay.
- Credential endpoints are rate limited per client IP. Set `TRUSTED_PROXY_HOPS`
  to match your deployment so the client IP cannot be forged.
- A per-request nonce CSP (no `unsafe-eval`, no inline scripts), HSTS, COOP/CORP,
  `frame-ancestors 'none'` and a restrictive Permissions-Policy are sent on every response.
- The container runs as a non-root user from a minimal standalone build.

## Automated checks

CodeQL (SAST), gitleaks (secrets), dependency review, Trivy (container) and
`npm audit` run in CI; Dependabot keeps npm, Docker and GitHub Actions current.
All GitHub Actions are pinned to commit SHAs.

## Hardening checklist for deployers

- Terminate TLS in front of the app and set `TRUSTED_PROXY_HOPS` correctly.
- Keep the backend's own rate limiting as the authority; the proxy limiter is per instance.
- Never prefix secrets with `NEXT_PUBLIC_`; they are embedded in the browser bundle.
- Enable branch protection on `main`: required reviews, required status checks, signed commits.
