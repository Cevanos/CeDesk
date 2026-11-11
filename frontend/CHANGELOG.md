# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/) and commits follow [Conventional Commits](https://www.conventionalcommits.org).

## [Unreleased]

### Added

- Phase 1 backend update: SLA pill on tickets and rows, "Time left" sort, Settings > SLA (targets editable by Admin); files on replies and notes with virus-scan states; saved replies (type `/` in the reply box); rules and macros (Settings screens, Macros menu on a ticket); support email addresses, send test, email delivery status under replies; failed emails / failed events with "Send again"; Settings > Cevanos and the Accept button on handover tickets; message kinds `customer`/`agent`/`ai`/`note`/`system`; `Idempotency-Key` on create, reply, upload, accept and macro actions; 503 `Unavailable` message; reset link now read from `#token=` and cleared from the address bar.
- Test suite: Vitest + Testing Library + MSW (proxy, client, permissions, hooks, components) and Playwright e2e with axe WCAG scans in light and dark.
- CI (format, lint, typecheck, tests with coverage, build, e2e, audit), Dependabot, Husky + lint-staged, Conventional Commit enforcement.
- Route-level `error.tsx`, `global-error.tsx`, `not-found.tsx`, `loading.tsx`; central error/telemetry seam (`src/lib/monitoring.ts`); Web Vitals hook.
- Optimistic updates with rollback for ticket edits; central `queryKeys`; Zod response checks at the API boundary.
- Command palette (Ctrl/⌘ K), offline banner, unsaved-changes guards, virtualised ticket list.
- Per-request CSP nonce, sign-in rate limiting, Zod-validated environment, `/api/health`.
- Dockerfile (standalone output), bundle analyser script, OpenAPI type generation script.
- next-intl scaffolding with `messages/en.json`; development-only component gallery at `/dev/ui`.
- ADRs, CONTRIBUTING, expanded README.

### Changed

- Strict TypeScript (`noUncheckedIndexedAccess`), stricter ESLint (jsx-a11y recommended, import order, no `console`, type imports).
- Split the ticket list view and workspace hooks into focused modules; agent drawer is lazy-loaded.
- Overlays trap and restore focus, close on Escape, and support arrow-key menu navigation.
- Design tokens adjusted for WCAG AA contrast (muted text and the primary button).

### Fixed

- A signed-in user landing on `/login?next=…` is sent to `next` instead of always `/tickets`.
- Message-type tablist contained a non-tab control, which broke the ARIA tab pattern.
- Removed leftover debug logging and all lint suppressions that hid real issues.
