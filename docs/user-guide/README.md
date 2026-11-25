# CeDesk user guide: how it is written

Status: **step 1 decisions made** (below). Article titles are still proposals; the mock's
"123 articles in 12 topics" is not backed by files yet and will be replaced by the real count.

## Decisions (step 1)

| Question | Decision | Why |
|----------|----------|-----|
| Where articles live | `docs/user-guide/en/<topic>/NN-<slug>.md` | Same repo as the code, so a change and its docs go in one PR. |
| Languages | English only at launch, but files sit under `en/` | Adding a language later is a new folder, not a move. The mock's language switcher stays in the design but shows one option. |
| Topics | The 15 below | Covers every route group in `openapi.json` (see the coverage map). |
| Article count | Not promised. Home page counts are computed from the files | The mock's 123 was a placeholder. |
| API reference | Generated from `docs/api/openapi.json`, never hand-written | One source of truth. |
| Developer guides | `docs/developers/*.md` (Quickstart, Authentication, Webhooks, SSE ...) | Short hand-written guides beside the generated reference. |
| Self-hosting | Keep `docs/SELF_HOSTING.md` and `docs/SETTINGS.md` where they are; the site links to them | They are already maintained and linked from the README. |

Change any row here before the build step is written; later changes cost more.

## Layout

```
docs/user-guide/
  README.md                 this file
  _TEMPLATE.md              copy this for every new article
  _img/                     screenshots, named <slug>-<n>.png
  en/
    glossary.md             Cevanos, segment, suppression list, SLA ...
    <topic-slug>/
      _topic.md             title, description, icon, order, who can see it
      01-<article-slug>.md  the number is the reading order inside the topic
```

The help center is built from these files. The developer docs' API reference is built from
`docs/api/openapi.json` and is not written by hand.

## Topics (proposal)

| # | Topic | Slug | Notes |
|---|-------|------|-------|
| 1 | Getting started | `getting-started` | What CeDesk is, workspace, first-day checklist |
| 2 | Working with tickets | `tickets` | Reply, notes, merge, transcripts, snooze, followers, views |
| 3 | Ticket tools (new) | `ticket-tools` | Tasks, time, approvals, side conversations, linked and child tickets, archive |
| 4 | Channels | `channels` | Email in/out, customer portal, web form, Cevanos chat, inbound API |
| 5 | Customers | `customers` | Profiles, notes, custom fields, merge, segments |
| 6 | Automation | `automation` | Rules, workflows, routing, SLA, CSAT, business hours, holidays, macros |
| 7 | Email campaigns | `campaigns` | The 7 articles already titled in the mock |
| 8 | Reports | `reports` | Backlog, team, agent, saved, dashboards, scheduled, live |
| 9 | Help center | `help-center` | Agent articles and the public help center |
| 10 | Imports | `imports` | Zendesk, Freshdesk, Intercom, Gorgias, Help Scout, HubSpot, Zoho, CSV |
| 11 | Integrations | `integrations` | Cevanos AI, Jira, Slack, Shopify, Stripe, Calendly, webhooks |
| 12 | Security, privacy and audit (widened) | `security` | Export/erase, retention, audit log, 2FA, SSO, IP list, secrets |
| 13 | Account and access | `account` | Members, built-in and custom roles, API keys, demo data, sandbox |
| 14 | Best practices (new) | `best-practices` | Round-robin, saved replies, tagging, keeping SLAs honest |
| 15 | How-to (new) | `how-to` | Short task recipes that link to the full articles |

Added over the mock: topics 3, 14 and 15, and audit/sandbox/archive/side conversations,
which the code has but the mock's descriptions do not mention.

## Coverage map: API route groups to topics

Every group in `openapi.json` is claimed by a topic, so nothing the product does is left
undocumented. A group marked (dev) is documented only in the developer docs.

| Topic | Route groups |
|-------|--------------|
| Getting started | `demo`, `usage`, `auth/signup*` |
| Working with tickets | `tickets`, `messages`, `saved-replies`, `macros`, `views`, `attachments`, `files`, `notifications`, `agents`, `teams`, `assist` |
| Ticket tools | `tasks`, `approvals`, `side-conversations`, `archive`, `time`, `ticket-types` |
| Channels | `email-addresses`, `email`, `inbound`, `public`, `portal`, `portal-settings`, `outbox` |
| Customers | `contacts`, `contact-fields`, `segments` |
| Automation | `rules`, `automation`, `flows`, `sla`, `holidays`, `routing-defaults`, `triage-settings`, `auto-close` |
| Email campaigns | `campaigns`, `suppressions` |
| Reports | `reports`, `dashboards`, `report-schedules` |
| Help center | `articles`, `article-categories` |
| Imports | `imports` |
| Integrations | `cevanos`, `apps`, `slack`, `webhooks` (dev too) |
| Security, privacy and audit | `privacy`, `retention`, `audit`, `security`, `security-settings`, `sso`, `sso-settings` |
| Account and access | `members`, `roles`, `api-keys` (dev too), `me`, `sandbox` |
| Best practices, How-to | no routes; link to the articles above |
| (dev) | `stream`, `healthz`, `readyz`, `auth/*`, `webhooks`, `api-keys` |

CI will later fail if a route group exists that no topic claims.

## Rules every article follows

1. **Written from the code, then checked.** Front matter lists the routes and settings the
   article depends on. CI fails if one no longer exists in `openapi.json` or `SETTINGS.md`.
2. **Use the label on screen**, never an internal value. Say "Unsubscribed", not
   `unsubscribed`. Raw values belong in the API reference only.
3. **Say who can do it** (role, e.g. Admins) and any plan limit, at the top.
4. **One task per article.** Steps are numbered, one action each, with the exact menu path
   (Settings › Campaigns › Suppression list).
5. **Say what refuses and why** in Troubleshooting, using the exact message the user sees.
6. **No defining a term twice.** Link to the glossary the first time.
7. **Screenshots** live in `docs/user-guide/_img/` and are named `<slug>-<n>.png`. An
   article with a placeholder cannot be marked `status: published`.
8. **Facts must not contradict.** If two places state the same rule (for example, removing
   a suppression needs consent and a written reason), keep it in one article and link to it.

## Review checklist (copy into the PR)

- [ ] Front matter complete, `routes`/`settings` verified against the code
- [ ] Role and plan stated; menu path matches the current frontend
- [ ] Steps tested on a fresh workspace
- [ ] Every error message quoted exactly
- [ ] No raw enum values or internal names outside code blocks
- [ ] Screenshot added, or `status: draft`
- [ ] Previous/next and related links resolve

## Developer docs additions to schedule

Webhooks (events, signature verification, retries and replay), live updates (SSE ticket
handshake), idempotency keys, API versioning and changelog (link `docs/api/CHANGELOG.md`),
and the sign-in page's missing `403` (address not on the workspace IP list).
