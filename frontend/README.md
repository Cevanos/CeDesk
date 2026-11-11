# CDesk Frontend

CDesk is a customer-support desk for agents, supervisors and admins: a shared ticket inbox, conversation threads, customer profiles, agent routing, service automation (SLA, rules, macros, saved replies), email and AI-chat (Cevanos) channels, and workspace administration.

Built with Next.js 16 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS 4 · TanStack Query · React Hook Form + Zod · next-intl.
Implements `refDoc/CDESK_FRONTEND_GUIDE.md`.

## Contents

[Features](#features) · [Quick start](#quick-start) · [Scripts](#scripts) · [Configuration](#configuration) · [Security model](#security-model) · [Project structure](#project-structure) · [Quality bar](#quality-bar) · [Deploying](#deploying)

## Features

### Authentication and account

- **Sign in** with workspace, email and password; one generic error for every failure, optional workspace pre-fill.
- **Forgot password** and **reset password** screens with a strength meter and show/hide password input.
- **Session handling**: httpOnly cookie session, automatic return to sign-in on any 401, hard navigation on sign-out so every cache is dropped, safe `?next=` return paths.
- **User menu**: signed-in identity and role, **My status** (Available, On break, Transfers only, Offline), **Change password** dialog and **Sign out**.

### Ticket inbox (`/tickets`)

- **Status tabs** (Open, Pending, Resolved) shown as summary cards with live counts.
- **Search** by ticket number, subject, customer or tag (debounced).
- **Filters**: scope (Everyone, Mine, Unassigned), priority and team, shown as removable chips with a clear-all action.
- **Sorting**: Priority, Newest, Oldest, Recently updated.
- **URL-driven state**: every filter lives in the query string, so views can be shared and survive a refresh.
- **Virtualised list** that stays fast on large inboxes, with skeleton loading and an error state with retry.
- **Rich rows**: ticket label, subject, priority, status, assignee (or Unassigned), VIP and AI-origin badges.

### Ticket detail (`/tickets/[id]`)

- **Conversation thread** with customer, agent, private-note and system messages, and **Load older messages** pagination.
- **Composer** with **Reply** and **Private note** modes, `Ctrl/⌘ + Enter` to send, an expandable editor and a warning before discarding an unsent draft.
- **@mentions** in private notes, picked from a searchable list of teammates.
- **AI handover card** showing topic, sentiment, language and the hand-off summary when a ticket came from the AI assistant.
- **Saved replies**: type `/` in the reply box to search and insert one; placeholders are filled in when the reply is sent.
- **Macros** menu in the header: run a multi-step action (reply, note, resolve and more) in one click; if any step is refused nothing is saved.
- **SLA pill** showing first-reply and resolution targets for the ticket.
- **Resolve** action in the header and a breadcrumb back to the list.
- **Properties panel**: status, priority, type, team, assignee and tags, edited inline with **optimistic updates and rollback**; server field errors (422) are shown as messages.
- **Ticket-type custom fields** rendered from the type definition (text, number, select; required flags).
- **Customer tab** with contact details, VIP badge and other tickets from the same customer.
- **Files tab** to list, download and upload attachments.

### Creating tickets (`/tickets/new`)

- Form with customer name and email, subject, channel, priority, type, team, assignee, tags and an optional first message.
- **Start from a Cevanos chat** to turn an AI chat into a ticket.
- Zod validation, inline field errors, unsaved-changes guard, and role-aware assignee choices.

### Customers (`/customers/[id]`)

- Customer profile with total, active and resolved ticket counts and customer-since date.
- **Mark as VIP** toggle and the full ticket history for that customer.

### Agents (`/agents`)

- Live roster with **search** (name, skill, channel, role), **status filter** and manual refresh.
- Per-agent **workload and capacity** bars.
- **Agent drawer**: change availability, enable channels (Email, Chat, Messaging), add or remove skills, and see workload.
- **Default capacity per channel** settings that control when auto-assignment skips an agent.

### Workspace settings (`/settings`, admins only)

Grouped in a side navigation; non-admins see an "Admins only" notice and the API enforces the same rule.

**Workspace**

- **Members**: add a member with a temporary password, change role, deactivate or reactivate, and reset a password.
- **Teams**: create, edit and delete teams, choose members and set the maximum open tickets per agent.
- **Ticket types**: create, edit, delete and **reorder** types, define custom fields (text, number, select with options, required) and an optional required skill.

**Service**

- **SLA**: edit first-reply and resolution targets per priority (counted in business minutes). Policies and business hours or holidays are shown read-only.
- **Rules**: automations that run top to bottom when a ticket is created or the customer replies, built from conditions (all must match) and ordered actions. Agent edits never trigger rules.
- **Macros**: named multi-step actions agents run on a ticket from the ticket header.
- **Saved replies**: reusable reply text with placeholders, inserted with `/` in the composer.

**Channels**

- **Email**: manage the support addresses customers write to; inbound mail becomes tickets and customer answers are threaded onto the same ticket.
- **Cevanos**: connect the Cevanos AI assistant so chats that need a person arrive as tickets. Shows the webhook address to paste into Cevanos, saves the API key and has a connection check.
- **Failed events**: undelivered email replies and outbound events (for example a reply to a Cevanos chat while it was unreachable).

### Roles and permissions

Four roles (Light agent, Agent, Supervisor, Admin) drive what the UI shows or disables; the API still enforces every rule with 403.

| Capability                                         | Light agent | Agent | Supervisor | Admin |
| -------------------------------------------------- | :---------: | :---: | :--------: | :---: |
| Read tickets, add private notes                    |     yes     |  yes  |    yes     |  yes  |
| Reply, resolve, create and edit tickets            |             |  yes  |    yes     |  yes  |
| Assign to self, set own status                     |             |  yes  |    yes     |  yes  |
| Assign to anyone                                   |             |       |    yes     |  yes  |
| Manage members, teams, types, SLA, rules, channels |             |       |            |  yes  |

### Everywhere in the app

- **Command palette** (`Ctrl/⌘ K`) to jump to any page, create a ticket or toggle the theme.
- **Notification bell** with unread badge, per-item read and **Mark all read**.
- **Light, dark and system themes**.
- **Responsive shell** with a mobile menu and accessible landmarks.
- **Offline banner** with a restored notice, **toast** feedback, and unsaved-changes guards.
- **Resilience**: route-level error, loading and not-found pages plus a global error boundary, all reported through one monitoring hook.
- **Accessibility**: focus trapping and Escape handling in overlays, labelled controls, strict `jsx-a11y` linting and axe WCAG A/AA scans in e2e.
- **Internationalisation**: `next-intl` with strings in `messages/en.json`.
- **Web vitals** collection and a development-only **component gallery** at `/dev/ui`.
- **Health endpoint** `/api/health` for liveness and readiness probes.

## Quick start

```bash
cp .env.example .env.local   # set CDESK_API_URL (server-only)
npm install                  # also installs the git hooks
npm run dev                  # http://localhost:3000  (the API only allows :3000 / :5173)
```

Node 20.19+ (see `.nvmrc`; CI and Docker use 22).

## Scripts

| Script                                      | What it does                                                            |
| ------------------------------------------- | ----------------------------------------------------------------------- |
| `npm run dev` / `build` / `start`           | Next.js                                                                 |
| `npm run lint` / `lint:fix`                 | ESLint (Next, strict jsx-a11y, import order, no stray `console`)        |
| `npm run typecheck`                         | `tsc --noEmit` (includes `noUncheckedIndexedAccess`)                    |
| `npm run format` / `format:check`           | Prettier (+ Tailwind class sorting)                                     |
| `npm test` / `test:watch` / `test:coverage` | Vitest + Testing Library + MSW                                          |
| `npm run test:e2e`                          | Playwright against a production build and `e2e/mock-backend.mjs`        |
| `npm run validate`                          | typecheck, lint, format check, tests and build: what CI runs            |
| `npm run analyze`                           | Bundle analyser (webpack build)                                         |
| `npm run gen:api`                           | Generate API types: `OPENAPI_SPEC=path/to/openapi.json npm run gen:api` |
| `npm run audit`                             | `npm audit` for production dependencies                                 |

First time running e2e: `npx playwright install chromium`.

## Configuration

| Variable                        | Where       | Notes                                                                                                                                                                                          |
| ------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CDESK_API_URL`                 | server only | Required. Validated at boot (`src/instrumentation.ts`).                                                                                                                                        |
| `AUTH_RATE_LIMIT_PER_MINUTE`    | server only | Optional, default 10 sign-in attempts per client per minute.                                                                                                                                   |
| `TRUSTED_PROXY_HOPS`            | server only | Optional, default 1. Number of reverse proxies in front of the app that append to `X-Forwarded-For`; use 0 if exposed directly. Too high lets clients forge their IP and dodge the rate limit. |
| `NEXT_PUBLIC_DEFAULT_WORKSPACE` | public      | Optional pre-fill for the sign-in screen.                                                                                                                                                      |
| `NEXT_PUBLIC_API_PUBLIC_URL`    | public      | Optional. The backend's public address, used only to show the full Cevanos webhook URL in Settings.                                                                                            |

## Security model

| Concern                  | How                                                                                                                                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend URL hidden       | The browser only calls `/api/*` on this origin. `src/app/api/[...path]/route.ts` forwards server to server using `CDESK_API_URL` (never `NEXT_PUBLIC_`). CSP is `connect-src 'self'`.    |
| Token not readable by JS | The sign-in token is moved into an **httpOnly** cookie (`SameSite=Lax`, `Secure` in prod) and stripped from the response body. See [ADR 0001](docs/adr/0001-httponly-session-cookie.md). |
| No inline-script XSS     | **Per-request CSP nonce** with `strict-dynamic`; no `unsafe-inline` for scripts. See [ADR 0002](docs/adr/0002-csp-nonce.md).                                                             |
| No open relay            | The proxy allow-lists top-level areas and rejects `.`/`..`/odd path segments.                                                                                                            |
| CSRF                     | SameSite cookie plus a same-origin check on every mutating proxy call.                                                                                                                   |
| Brute force              | In-memory per-client limiter on sign-in and password-reset calls (defence in depth; the backend remains the authority).                                                                  |
| Route guard              | `src/proxy.ts` redirects signed-out users to `/login`; a 401 anywhere clears the cookie and returns to sign-in.                                                                          |
| Headers                  | CSP, HSTS (prod), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP, `no-store` on `/api`.                                                                             |
| Open redirect            | `?next=` is validated to same-origin relative paths (`src/lib/safe-next.ts`), also when the proxy bounces a signed-in user off `/login`.                                                 |
| Enumeration              | Sign-in shows one message for every failure; forgot-password gives one answer for every account.                                                                                         |
| Config mistakes          | Environment is validated with Zod at boot; production refuses to start with a bad `CDESK_API_URL`.                                                                                       |

## Project structure

```
.
├── .github/                       CI and security (CodeQL) workflows, Dependabot, PR template
├── docs/adr/                      Architecture decision records (httpOnly cookie, CSP nonce)
├── e2e/                           Playwright specs (auth, tickets, a11y), helpers, mock backend
├── messages/en.json               next-intl strings
├── public/                        Logos and static assets
├── refDoc/                        CDESK_FRONTEND_GUIDE.md (product and API guide)
├── Dockerfile                     Production image
├── next.config.ts                 Next.js configuration
├── playwright.config.ts           E2E configuration
├── vitest.config.mts              Unit-test configuration
├── eslint.config.mjs              ESLint flat config
├── .env.example                   Documented environment variables
├── CHANGELOG.md · CONTRIBUTING.md · SECURITY.md
└── src/
    ├── proxy.ts                   Route guard, per-request CSP nonce, security headers
    ├── instrumentation.ts         Boot-time environment validation
    ├── instrumentation-client.ts  Client error-reporter hook-up
    ├── app/
    │   ├── layout.tsx, page.tsx, globals.css
    │   ├── error.tsx, global-error.tsx, not-found.tsx
    │   ├── (auth)/                login, forgot-password, reset-password
    │   ├── (app)/                 Signed-in area (shell layout, error, loading)
    │   │   ├── tickets/           list, [id] detail, new
    │   │   ├── customers/[id]/    Customer profile
    │   │   ├── agents/            Agent roster
    │   │   └── settings/          members, teams, ticket-types, sla, rules, macros, saved-replies, email, cevanos, failed
    │   ├── api/[...path]/         Backend-for-frontend proxy to the CDesk API
    │   ├── api/health/            Health probe
    │   └── dev/ui/                Component gallery (development only)
    ├── components/
    │   ├── auth/                  Auth shell, login/forgot/reset forms, password input
    │   ├── feedback/              Error state, offline banner, skeletons
    │   ├── layout/                App shell, command palette, notification bell, user menu
    │   ├── providers/             Query, theme and toast providers, web vitals
    │   ├── tickets/               Thread, composer, sidebar, badges, SLA pill, macros menu, customer view, new-ticket form
    │   │   └── list/              Filter bar, status tabs, rows, virtualised list
    │   ├── workspace/             Agents, agent drawer, settings shell and one view per settings page
    │   ├── ui/                    Primitives, select, overlays (dialog, popover, menu), brand
    │   └── dev/                   UI gallery
    ├── hooks/
    │   ├── use-auth, use-tickets, use-ticket-filters, use-workspace
    │   ├── use-escape, use-focus-trap, use-online, use-unsaved-changes
    │   └── workspace/             agents, members, teams, ticket-types, sla, rules, macros, saved-replies, email, cevanos, notifications, routing-defaults
    ├── lib/
    │   ├── api/                   Fetch client, ApiError, query keys, Zod schemas, types, ticket filters
    │   ├── server/                Env validation, config, rate limiter, proxy guards (server-only)
    │   ├── csp.ts                 CSP builder
    │   ├── permissions.ts         Role matrix
    │   ├── monitoring.ts          Error reporting hook
    │   ├── navigation.ts          Hard-navigation helper
    │   ├── safe-next.ts           Open-redirect protection
    │   └── utils.ts               Formatting and class helpers
    ├── i18n/request.ts            next-intl request config
    └── test/                      Fixtures, render helper, MSW server, setup
```

Unit and component tests sit next to the code they cover (`*.test.ts(x)`).

## Quality bar

- **Types**: strict TS with `noUncheckedIndexedAccess`; API responses are checked at the boundary (`src/lib/api/schemas.ts`) and drift is reported, not silently ignored.
- **Tests**: unit and component tests (Vitest, Testing Library, MSW) for the proxy, client, permissions, hooks (including optimistic rollback) and key components; Playwright e2e covers sign-in, the ticket flow, the command palette and **axe WCAG A/AA scans in light and dark**.
- **Resilience**: route-level `error.tsx`, `global-error.tsx`, `not-found.tsx` and `loading.tsx`; all errors flow through `src/lib/monitoring.ts`; offline banner; unsaved-changes guards.
- **UX**: optimistic ticket edits with rollback, virtualised ticket list, command palette (Ctrl/⌘ K), full focus management in overlays.
- **CI**: GitHub Actions runs format, lint, typecheck, unit tests with coverage, build, e2e and a dependency audit; a separate Security workflow runs CodeQL; Dependabot keeps dependencies current.

## Deploying

```bash
docker build -t cdesk-frontend .
docker run -p 3000:3000 -e CDESK_API_URL=https://api.example.com/api/v1 cdesk-frontend
```

`/api/health` is the liveness/readiness probe (200 when configured, 503 when not). Behind a reverse proxy, make sure it sets `X-Forwarded-For` and set `TRUSTED_PROXY_HOPS` to match (used by the sign-in rate limiter).

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
