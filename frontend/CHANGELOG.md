# Changelog

Notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/), and commits follow [Conventional Commits](https://www.conventionalcommits.org).

## [Unreleased]

### Fixed

- Settings > Roles, Apps, API keys, Webhooks, Security, Privacy, Audit log and Plan & retention (and Demo data) no longer show "Not found": their API areas were missing from the proxy allow-list.
- Customer portal: a new ticket now sends `message`, `type_id` and the type's own `fields` (the subject is made by the server), and shows the fields the chosen type asks for (for example order ID and amount for a refund). The contact form does the same and no longer sends a subject.
- Customer portal: a resolved ticket keeps its reply box (a reply reopens it) and always offers the 1 to 5 rating.
- Help center: the "Was this helpful?" vote sends a visitor id kept in the browser, which the server requires.
- Settings > Topics: "Reset to built-in lists" sends `topics: null` and keeps the card-redaction switch.
- Ticket list and ticket page show the topic, the language by name and a "Waiting to accept" chip for unaccepted handovers.
- Agents: a light agent shows no availability (and is left out of status filters); channels show their open-ticket counts.
- Customer page: the name can be edited.
- Settings > SLA shows each target's "reads as" sentence.
- Reports: "Open breaching" tile, the share of happy ratings, the API's first-response and SLA-met values, and agent role and status.
- Settings > Cevanos lists the webhook events to subscribe to and explains that the webhook is added in Cevanos.

### Added

- README screenshots (from the e2e mock backend) and more unit tests: rules, ticket types, members, agents, Cevanos and notification hooks.
- MIT `LICENSE` and `license` field in `package.json`.
- Code of Conduct (Contributor Covenant 2.1), GitHub issue forms (bug, feature) and a security contact link.
- `robots.txt` (disallow all, matching the app-wide `noindex`).
- Unit tests for `queryKeys` and the teams, macros and saved-replies hooks.
- README: architecture overview, troubleshooting, roadmap, license and acknowledgements sections; `ALLOW_INSECURE_HTTP` documented.
- Phase 1 backend update: SLA pill on tickets and rows, "Time left" sort, Settings > SLA (targets editable by Admin); files on replies and notes with virus-scan states; saved replies (type `/` in the reply box); rules and macros (Settings screens, Macros menu on a ticket); support email addresses, send test, email delivery status under replies; failed emails / failed events with "Send again"; Settings > Cevanos and the Accept button on handover tickets; message kinds `customer`/`agent`/`ai`/`note`/`system`; `Idempotency-Key` on create, reply, upload, accept and macro actions; 503 `Unavailable` message; reset link now read from `#token=` and cleared from the address bar.
- Test suite: Vitest + Testing Library + MSW (proxy, client, permissions, hooks, components) and Playwright e2e with axe WCAG scans in light and dark.
- CI (format, lint, typecheck, tests with coverage, build, e2e, audit), Dependabot, Husky + lint-staged, Conventional Commit enforcement.
- Route-level `error.tsx`, `global-error.tsx`, `not-found.tsx`, `loading.tsx`; central error/telemetry seam (`src/lib/monitoring.ts`); Web Vitals hook.
- Optimistic updates with rollback for ticket edits; central `queryKeys`; Zod response checks at the API boundary.
- Command palette (Ctrl/⌘ K), offline banner, unsaved-changes guards, virtualised ticket list.
- Per-request CSP nonce, sign-in rate limiting, Zod-validated environment, `/api/health`.
- Dockerfile (standalone output), bundle analyser script, OpenAPI type generation script.
- next-intl scaffolding with `messages/en.json`; development-only component gallery at `/dev/ui`.
- ADRs, CONTRIBUTING, and a longer README.

### Changed

- Internal reference notes (`refDoc/`) are no longer tracked; the ADR and a code comment no longer point at them.
- Removed the `deploy.yml` workflow (auto-deploy to a private server). Deployment is documented in the README (Docker).
- Minor/patch dependency updates in `package-lock.json` (`@hookform/resolvers`, `react-hook-form`, `next-intl`, `lucide-react`, `msw`, `jsdom`, `prettier`, `@testing-library/user-event`).
- Dockerfile uses `node:22-alpine` to match `.nvmrc` and CI; removed a stale `.next-sec` path from `tsconfig.json`.
- Strict TypeScript (`noUncheckedIndexedAccess`), stricter ESLint (jsx-a11y recommended, import order, no `console`, type imports).
- Split the ticket list view and workspace hooks into focused modules; agent drawer is lazy-loaded.
- Overlays trap and restore focus, close on Escape, and support arrow-key menu navigation.
- Design tokens adjusted for WCAG AA contrast (muted text and the primary button).

### Fixed

- e2e: the new-ticket spec used old field labels and an ambiguous "Create ticket" button, so two tests failed against the current form.
- A signed-in user landing on `/login?next=…` is sent to `next` instead of always `/tickets`.
- Message-type tablist contained a non-tab control, which broke the ARIA tab pattern.
- Removed leftover debug logging and all lint suppressions that hid real issues.
