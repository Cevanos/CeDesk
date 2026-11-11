# Security Policy

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting instead: Security > Report a vulnerability on this repository. Include the affected area, steps to reproduce and the impact you saw.

We aim to acknowledge reports within 3 business days and give a remediation plan within 10 business days. Please give us time to fix the issue before disclosing it publicly.

## Supported versions

Only the latest release on `main` gets security fixes.

## How the app is protected

- The browser never talks to the backend directly. Calls go through the server-side proxy at `/api/*`, and the backend address is not sent to clients.
- The session token is kept in an `httpOnly`, `SameSite=Lax` cookie (`Secure` in production), so page JavaScript can't read it.
- Mutating proxy calls must be same-origin.
- The proxy only reaches an allow-listed set of backend areas and rejects path traversal.
- Credential endpoints are rate limited per client IP. Set `TRUSTED_PROXY_HOPS` to match your deployment so the IP can't be forged.
- Every response carries a per-request nonce CSP (no `unsafe-eval`, no inline scripts), HSTS, COOP/CORP, `frame-ancestors 'none'` and a restrictive Permissions-Policy.
- The container runs as a non-root user from a minimal standalone build.

## Automated checks

CI runs CodeQL, gitleaks, dependency review, Trivy (container) and `npm audit`. Dependabot updates npm, Docker and GitHub Actions. Actions are pinned to commit SHAs.

## For deployers

- Terminate TLS in front of the app and set `TRUSTED_PROXY_HOPS` correctly.
- Keep the backend's own rate limiting in place. The proxy limiter is per instance.
- Never prefix secrets with `NEXT_PUBLIC_`; those values end up in the browser bundle.
- Turn on branch protection for `main`: required reviews, required status checks, signed commits.
