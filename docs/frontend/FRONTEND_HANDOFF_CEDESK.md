# CeDesk: frontend handoff (every route, by screen)

For the frontend team. Written from the API as served (`docs/api/openapi.json`, the
source of truth for every field) and `docs/api/CHANGELOG.md` (what changed and why).
Regenerate with `uv run python scripts/frontend_handoff.py`.

Every route is under `/api/v1`, shown here without it, except `/healthz` and `/readyz`.
Routes marked with a PR are built and tested but wait for review: they exist on `main`
once that PR is merged.

## 1. What every screen needs to know

**Signing in.** `POST /auth/sign-in` gives a `token`; send it on every call as
`Authorization: Bearer <token>`. Two flags on the answer change what to show next:
`two_factor_required` (ask for the authenticator code, send it to `POST /auth/2fa` with
the same token within 10 minutes) and `password_expired` (show Change password; until then
only `GET /me`, `POST /me/password` and sign-out work). A workspace with single sign-on
starts at `GET /sso/{workspace}/start` instead.

**What to show this person.** `GET /me` returns `access`: the role, the `actions` it may
take (`read_ticket`, `create_ticket`, `reply`, `private_note`, `change_fields`,
`assign_self`, `assign_others`, `approve`, `reports`, `settings`, `list_members`,
`manage_access`) and its `scope` (`all`, `team_edit`: other teams' tickets are view only,
`team_only`: other teams' tickets don't exist for them). Hide what `actions` doesn't
allow; the API refuses it anyway (403). Built-in roles: Admin (everything), Supervisor
(adds assign others, approve, reports), Agent, Light agent (reads and adds notes only).
`/me` also says `two_factor_setup_required` (the workspace requires 2FA: send them to set
it up) and `ai` (the workspace has Cevanos connected: show the AI buttons and the AI card
in Reports; false: hide them, their routes answer 409 `cevanos_not_connected`).

**Errors.** Every error is `{"error": {"code", "message", "details"?}}`. Show `message`:
it is written for the person using the screen. `details` comes only on 422 validation
errors, one entry per field (`field`, `location`, `message`), to put next to the right input.

| Status | Means | What to do |
|---|---|---|
| 401 | Not signed in, or the session ended | Back to sign-in |
| 402 | Over the cloud plan's limit | Show `message` (it names the limit) |
| 403 | Not allowed (role, scope, IP list, old password) | `message`; a ticket: `view_only_reason` |
| 404 | Doesn't exist, or another team's (team-only roles) | "Not found" |
| 409 | Changed by someone else, or already done | Reload, then show `message` |
| 422 | A field is wrong | Show `details` next to the fields |
| 429 | Too many tries | Wait `Retry-After` seconds |
| 503 | A provider (Slack, Shopify, Cevanos...) didn't answer | "Try again" |

**Lists.** Lists page with a cursor: the answer has `next_cursor`; send it back as
`cursor` (with the same filters) for the next page. `null` means the last page.

**Two people editing one ticket.** A ticket comes with an `ETag` header. Send it back as
`If-Match` on `PATCH /tickets/{id}` and resolve: if someone changed the ticket meanwhile
the answer is 409, so reload and let the agent try again instead of overwriting.

**Double clicks and retries.** Routes that create something take an optional
`Idempotency-Key` header (any unique string, a UUID is good). Send the same key when you
retry: the repeat returns what the first one made (header `Idempotent-Replayed: true`)
instead of sending a reply twice. Keys last 24 hours.

**Live updates.** `POST /stream/ticket` gives a one-use ticket (valid 60 s); open
`GET /stream?ticket=...` with `EventSource`. Events: `ticket.created`, `ticket.updated`,
`ticket.resolved`, `message.created`, `assignment.changed`, `chat.accepted`,
`chat.taken_over`, `chat.closed`, `chat.reopened`, `notification`. An event says what
changed (ids), never the text: fetch the ticket or list again through the normal routes.
On reconnect, send `Last-Event-ID` to get what you missed (kept 24 hours). The portal has
its own stream (`/portal/stream`) with only the customer's own messages and status.

**Placeholders.** Replies, saved replies and macros may contain
`{{customer.first_name}}` and the like; the API fills them when the reply is sent.
Show them as they are in the editor.

**234 routes**, all on `main`.

## 2. Screens

### 2.1 Sign-in and my account

- Sign-in page: workspace address, email, password. Then, if asked, the 2FA code (or a recovery code). "Forgot password" emails a link; the link's page calls `POST /auth/reset-password`.
- SSO: `GET /sso/{workspace}` says whether the workspace uses it; then send the browser to `/sso/{workspace}/start`.
- My account (avatar menu): change password, two-step sign-in (`POST /me/2fa/setup` gives an `otpauth_uri`: show it as a QR code; confirm with the first code, then show the recovery codes once), new recovery codes, sign out.

| | Route | What it does |
|---|---|---|
| `POST` | `/auth/2fa` | Second step of signing in: the code from the app, or a recovery code |
| `POST` | `/auth/forgot-password` | Email me a link to choose a new password |
| `POST` | `/auth/reset-password` | Choose a new password with the emailed link (signs out everywhere) |
| `POST` | `/auth/sign-in` | Sign in with workspace address, email and password |
| `POST` | `/auth/sign-out` | End this session |
| `GET` | `/me` | The signed-in member and their workspace |
| `GET` | `/me/2fa` | My two-step sign-in |
| `POST` | `/me/2fa/confirm` | Confirm with the first code: 2FA is on; returns the recovery codes |
| `POST` | `/me/2fa/disable` | Turn my two-step sign-in off (403 when the workspace requires it) |
| `POST` | `/me/2fa/recovery-codes` | New recovery codes (the old ones stop working); needs a code from the app |
| `POST` | `/me/2fa/setup` | Start setting up the app (a new secret each time until confirmed) |
| `POST` | `/me/password` | Change my password (ends my other sessions) |
| `GET` | `/sso/{workspace}` | Does this workspace use SSO? |
| `POST` | `/sso/{workspace}/acs` | The identity provider's answer (ACS URL) |
| `GET` | `/sso/{workspace}/metadata` | Our SAML service provider metadata (for the IdP's setup screen) |
| `GET` | `/sso/{workspace}/start` | Start signing in: redirects to the identity provider |

### 2.2 Top bar: my status, notifications, my work

- Status switch (`available`, `on_break`, `transfers_only`, `offline`): `PUT /me/status`. New agents start Offline, so routing gives them nothing until they switch on.
- Bell: `GET /notifications` (with `unread` for the badge), mark one or all read. A `notification` event on the live stream means fetch again.
- "Waiting for my approval" and "My tasks" lists.
- Live stream: see section 1.

| | Route | What it does |
|---|---|---|
| `GET` | `/approvals/waiting` | Requests waiting for my decision, oldest first (up to 100) |
| `PUT` | `/me/status` | Set your own status |
| `GET` | `/notifications` | Your notifications, newest first |
| `POST` | `/notifications/read-all` | Mark all your notifications read |
| `POST` | `/notifications/{notification_id}/read` | Mark one notification read |
| `GET` | `/stream` | Live updates for an agent (Server-Sent Events) |
| `POST` | `/stream/ticket` | A one-use ticket to open the live stream |
| `GET` | `/tasks/mine` | My open tasks, the ones due soonest first |

### 2.3 Inbox: the ticket list

- Tabs Open, Pending, Resolved with counts (`counts` on the answer). Filters: scope (`everyone`, `mine`, `unassigned`), priority, team, `imported` (tickets brought from another help desk). Search box: `q` (number, subject, customer, field value, tag). Sort: `priority` (default), `sla`, `newest`, `oldest`, `updated`.
- New ticket: `POST /tickets` (type, its fields, team, assignee, tags). The ticket types and their fields come from `GET /ticket-types`.
- A team-only role only ever gets its team's tickets; nothing to filter on your side. Resolved tickets older than the archive age (default 120 days) leave the list: see Archive.

| | Route | What it does |
|---|---|---|
| `GET` | `/tickets` | List tickets with status tabs, filters, search and sort (cursor pages) |
| `POST` | `/tickets` | Create a ticket (gets the next T-number) |

### 2.4 Ticket page: the conversation

- `GET /tickets/{id}`: the ticket with its newest messages (load older ones with `older_messages_cursor`), the SLA clock (`sla`), the CSAT rating, the customer card, `can_edit` and `view_only_reason` (show it as a banner and disable the editing controls).
- Reply or private note: `POST /tickets/{id}/messages` with `kind` `reply` or `note`, `mention_ids` for @mentions in notes, `attachment_ids` for files uploaded first with `POST /tickets/{id}/attachments`. Send an `Idempotency-Key`.
- Sidebar fields: `PATCH /tickets/{id}` (status, priority, type, fields, team, assignee, subject, tags) with `If-Match`. Resolve: `POST /tickets/{id}/resolve`.
- Macros menu: `GET /macros`, run one with `POST /tickets/{id}/macros/{macro_id}`. Saved replies: type `/` in the box, `GET /saved-replies?q=`.
- Cevanos chat handovers: `accepted: false` means waiting; show **Accept** (`POST /tickets/{id}/accept`) before replies are allowed. `handover` holds the AI's summary and reason.
- Files: `GET /attachments/{id}` gives a short-lived download link.

| | Route | What it does |
|---|---|---|
| `GET` | `/attachments/{attachment_id}` | A short-lived link to download a file |
| `GET` | `/attachments/{attachment_id}/content` | Download a file, signed in |
| `GET` | `/files/{token}` | Download a file with a signed link (local storage only) |
| `POST` | `/tickets/from-conversation/{conversation_id}` | Turn a Cevanos chat into a ticket (then Accept it) |
| `GET` | `/tickets/{ticket_id}` | Open a ticket |
| `PATCH` | `/tickets/{ticket_id}` | Change status, priority, type, fields, team, assignee, subject or tags |
| `POST` | `/tickets/{ticket_id}/accept` | Accept a handed-over chat: it is yours, and replies are allowed |
| `GET` | `/tickets/{ticket_id}/attachments` | A ticket's files, newest first |
| `POST` | `/tickets/{ticket_id}/attachments` | Attach a file to a ticket |
| `POST` | `/tickets/{ticket_id}/macros/{macro_id}` | Run a macro on a ticket: all its steps in one go |
| `GET` | `/tickets/{ticket_id}/messages` | A ticket's messages, newest first (to load older ones) |
| `POST` | `/tickets/{ticket_id}/messages` | Reply to the customer or add a private note (with @mentions) |
| `POST` | `/tickets/{ticket_id}/resolve` | Resolve a ticket (already resolved: no change) |
| `POST` | `/tickets/{ticket_id}/simulate-customer-reply` | Test only (Admin, not in production): add a message as the customer |

### 2.5 Ticket page: related tickets, approvals, tasks and time

- Related card: `GET /tickets/{id}/related` (parent, children, links, merges). Merge into another open ticket, link two similar tickets, or make a child ticket for another team.
- Approvals: ask a named person; they decide from "Waiting for my approval". A Cevanos Governance hold (`cevanos_hold_id` set; no requester or approver) is decided by anyone who can approve, and the decision goes to Cevanos.
- Tasks: add with a quick due time (`1h`, `4h`, `tomorrow`) or a date; the owner is told. Time log: minutes, a note, billable or not; the total is on the answer.

| | Route | What it does |
|---|---|---|
| `POST` | `/approvals/{approval_id}/decide` | Approve or reject (the named approver, or an Admin) |
| `PATCH` | `/tasks/{task_id}` | Change a task, give it to someone else, or mark it done (done: true/false) |
| `GET` | `/tickets/{ticket_id}/approvals` | This ticket's approval requests, newest first |
| `POST` | `/tickets/{ticket_id}/approvals` | Ask someone to approve something on this ticket |
| `POST` | `/tickets/{ticket_id}/children` | Make a child ticket for another team; returns the new ticket |
| `POST` | `/tickets/{ticket_id}/links` | Link two similar tickets (both ways); returns the Related card |
| `DELETE` | `/tickets/{ticket_id}/links/{other_id}` | Remove a link |
| `POST` | `/tickets/{ticket_id}/merge` | Merge this ticket into another open ticket; returns the one merged into |
| `GET` | `/tickets/{ticket_id}/related` | The Related card: parent, children, links, and merges (up to 50 each) |
| `GET` | `/tickets/{ticket_id}/tasks` | This ticket's tasks, open ones first |
| `POST` | `/tickets/{ticket_id}/tasks` | Add a task to this ticket |
| `GET` | `/tickets/{ticket_id}/time` | This ticket's time log with the total and the billable total |
| `POST` | `/tickets/{ticket_id}/time` | Log time spent on this ticket |
| `DELETE` | `/time/{entry_id}` | Remove a time entry (your own; an Admin can remove any) |

### 2.6 Ticket page: side conversations, Jira, orders and payments

- Side conversations (agents only, never shown to the customer): by email (addresses), or in a Slack channel (needs the Slack side conversations app). Replies come back to the same side conversation, and the assignee gets a `side_reply` notification. `via` says `email` or `slack`.
- Jira: create an issue from the ticket or link an existing one.
- Orders and payments cards: `available: false` comes with a `message` to show (not installed, customer unknown, provider down); the ticket still works. With Cevanos connected they come from Cevanos's stores (`source: cevanos`); a store it couldn't read is named in `message` while the rest still show.

| | Route | What it does |
|---|---|---|
| `POST` | `/side-conversations/{side_id}/messages` | Write again in a side conversation |
| `GET` | `/tickets/{ticket_id}/jira` | Jira issues on a ticket |
| `POST` | `/tickets/{ticket_id}/jira` | Create a Jira issue from a ticket |
| `POST` | `/tickets/{ticket_id}/jira/link` | Link an existing Jira issue to a ticket |
| `DELETE` | `/tickets/{ticket_id}/jira/{key}` | Take a Jira issue off a ticket (it stays in Jira) |
| `GET` | `/tickets/{ticket_id}/orders` | The customer's latest orders (sidebar) |
| `GET` | `/tickets/{ticket_id}/payments` | The customer's latest payments (sidebar) |
| `GET` | `/tickets/{ticket_id}/side-conversations` | Side conversations on a ticket (agents only; never shown to the customer) |
| `POST` | `/tickets/{ticket_id}/side-conversations` | Start a side conversation by email |
| `POST` | `/tickets/{ticket_id}/side-conversations/slack` | Start a side conversation in a Slack channel |

### 2.7 Ticket page: AI help (Cevanos)

- Only when `GET /me` says `ai: true`; else hide the buttons. Nothing is saved: show the answer, and let the agent use it.
- Summary: shows a summary of the conversation (notes included).
- Suggested reply: puts `text` in the reply box; **never send it by itself**. Needs `reply` (not for light agents).
- Improve writing (reply box): `tone` professional, friendly, empathetic or concise; replace the box's text with the answer for the agent to edit.
- Translate: a customer message (`to`: the agent's language) or the reply (`to`: the ticket's `language`); `from` says the language found.
- 409 `cevanos_scope_missing`: show `message` (it names the scope an Admin must add in Cevanos). 503: Cevanos is busy, try again.

| | Route | What it does |
|---|---|---|
| `POST` | `/assist/improve` | Improve the writing of a reply or note (Cevanos AI) |
| `POST` | `/assist/translate` | Translate a message or a reply (Cevanos AI) |
| `POST` | `/tickets/{ticket_id}/draft` | Suggest a reply (Cevanos AI) |
| `POST` | `/tickets/{ticket_id}/summary` | Summarise the ticket (Cevanos AI) |

### 2.8 Customers

- Customer card: `GET /contacts/{id}/tickets` (all their tickets, newest first). Change the name or VIP flag with `PATCH /contacts/{id}/profile`.

| | Route | What it does |
|---|---|---|
| `PATCH` | `/contacts/{contact_id}/profile` | Change a customer's name or VIP flag |
| `GET` | `/contacts/{contact_id}/tickets` | A customer's card and all their tickets, newest first |

### 2.9 Archive

- Old resolved tickets live here: search by number (`1042` or `T-1042`) or by customer (`email`, `contact_id`); open one read only. No reply, change or reopen. A team-only role gets a 404 for another team's, as for a live ticket.
- The page: `type_name`, `team_name` and `assignee_name` (as they were when archived), tags, fields, messages (notes too, with an email's headers), files' names and the rating. The rest of its history is in `related`, one list per kind: `time_log`, `tasks`, `approvals`, `links` (the other ticket's `label` and `ticket_id`), `side_conversations` (with their `messages`), `jira_issues`, `emails` (sent from it) and `cevanos_chat` (a handover's chat, or null). Fields are named as on a live ticket, so the same components can show both.
- An email that carried a secret (a reset link) has `sensitive: true` and `body_text: null`: show "Not kept: it carried a secret".
- Tickets archived before `related` was kept have empty lists.

| | Route | What it does |
|---|---|---|
| `GET` | `/archive/tickets` | Find archived tickets by number or customer |
| `GET` | `/archive/tickets/{ticket_id}` | Open an archived ticket (read only) |

### 2.10 Reports

- Supervisors and Admins (`reports`). Team report, more metrics and help-article votes, for a period (`from`, `to`) and a team (`team_id`; not for articles). Each one downloads as CSV with `format=csv`.
- AI card (`GET /reports/ai`, only with `ai: true`): handover rate, resolved by the AI, CSAT of AI answers, from Cevanos for the whole workspace.

| | Route | What it does |
|---|---|---|
| `GET` | `/reports/ai` | The AI card: handover rate, AI-resolved, CSAT of AI answers (Cevanos) |
| `GET` | `/reports/articles` | Help-article votes (Supervisor, Admin) |
| `GET` | `/reports/metrics` | More metrics (Supervisor, Admin) |
| `GET` | `/reports/team` | Team report (Supervisor, Admin) |

### 2.11 Settings: members, roles, teams and routing

- Members: add (with a role and first password), change role or deactivate, set a new password, reset someone's 2FA (lost phone). Admins only (`manage_access`).
- Custom roles: name, the five rights, scope (all, team edit, team only) and its team.
- Teams: name, auto-assign, max open per agent, members. Agents: status, channels, capacity per channel, skills; workspace defaults in Routing defaults.

| | Route | What it does |
|---|---|---|
| `GET` | `/agents` | Everyone's status, load and skills |
| `PATCH` | `/agents/{member_id}/routing` | Status, channels, capacity per channel and skills (Admin) |
| `GET` | `/members` | Members, 50 per page by default |
| `POST` | `/members` | Add a member (Admin) |
| `PATCH` | `/members/{member_id}` | Change a member's name, role or active state (Admin) |
| `POST` | `/members/{member_id}/2fa/reset` | Reset a member's two-step sign-in (Admin; lost phone) |
| `POST` | `/members/{member_id}/password` | Give a member a new password (Admin; signs them out everywhere) |
| `GET` | `/roles` | Custom roles (Admin) |
| `POST` | `/roles` | Add a custom role (Admin) |
| `PUT` | `/roles/{role_id}` | Change a custom role (Admin; the whole role is sent; members get it at once) |
| `DELETE` | `/roles/{role_id}` | Delete a custom role nobody has (Admin; 409 while members have it) |
| `GET` | `/routing-defaults` | The workspace's default capacity per channel |
| `PATCH` | `/routing-defaults` | Change the default capacity per channel (Admin) |
| `GET` | `/teams` | All teams |
| `POST` | `/teams` | Add a team (Admin) |
| `PATCH` | `/teams/{team_id}` | Rename, change settings or replace members (Admin) |
| `DELETE` | `/teams/{team_id}` | Delete a team; its tickets become team-less (Admin) |

### 2.12 Settings: ticket types, SLA, rules, macros, saved replies, triage, sandbox

- Ticket types: fields (`text`, `number`, `select`), required skill, shown to customers or not; order matters (menus follow it).
- SLA: targets per priority, ordered policies, business hours and holidays.
- Rules (run in order) and macros (menu order): send the whole order to `/order`.
- Sandbox: copy the live rules, macros and saved replies, change them, try them on sample tickets, compare, then deploy.

| | Route | What it does |
|---|---|---|
| `POST` | `/holidays` | Admin: add a holiday (the SLA clock doesn't run that day) |
| `PATCH` | `/holidays/{holiday_id}` | Admin: rename a holiday or move it to another day |
| `DELETE` | `/holidays/{holiday_id}` | Admin: remove a holiday |
| `GET` | `/macros` | All macros in menu order |
| `POST` | `/macros` | Add a macro; it goes last in the menu (Admin) |
| `PUT` | `/macros/order` | Set the menu order: every macro id once (Admin) |
| `PATCH` | `/macros/{macro_id}` | Rename or change the steps (Admin) |
| `DELETE` | `/macros/{macro_id}` | Delete a macro (Admin) |
| `GET` | `/rules` | All rules in run order (Admin) |
| `POST` | `/rules` | Add a rule; it runs after the others (Admin) |
| `PUT` | `/rules/order` | Set the run order: every rule id once (Admin) |
| `PATCH` | `/rules/{rule_id}` | Rename, switch on or off, or change what it checks and does (Admin) |
| `DELETE` | `/rules/{rule_id}` | Delete a rule (Admin) |
| `GET` | `/sandbox` | The sandbox (404 when none) |
| `POST` | `/sandbox` | Make a sandbox: a copy of the live rules, macros and saved replies |
| `DELETE` | `/sandbox` | Delete the sandbox |
| `GET` | `/sandbox/compare` | What Deploy would change |
| `POST` | `/sandbox/deploy` | Deploy the sandbox over the live settings |
| `POST` | `/sandbox/try` | Try the sandbox rules on sample tickets |
| `PUT` | `/sandbox/{section}` | Replace one section of the sandbox |
| `GET` | `/saved-replies` | Saved replies by shortcut; `q` narrows to shortcuts starting with it |
| `POST` | `/saved-replies` | Add a saved reply (Admin) |
| `PATCH` | `/saved-replies/{reply_id}` | Change a saved reply's shortcut or text (Admin) |
| `DELETE` | `/saved-replies/{reply_id}` | Delete a saved reply (Admin) |
| `GET` | `/sla` | SLA targets, policies and hours |
| `PUT` | `/sla/calendar` | Admin: business hours (days, hours, time zone) or 24/7 |
| `PUT` | `/sla/policies` | Admin: save the ordered SLA policies (on/off, order, conditions, targets) |
| `PUT` | `/sla/targets` | Admin: change the first-reply and resolve targets per priority |
| `GET` | `/ticket-types` | In order |
| `POST` | `/ticket-types` | Add a ticket type with its fields (Admin) |
| `PUT` | `/ticket-types/order` | Reorder ticket types (Admin) |
| `PATCH` | `/ticket-types/{type_id}` | Rename, change fields or skill, show or hide for customers (Admin) |
| `DELETE` | `/ticket-types/{type_id}` | Delete a ticket type; its tickets keep everything else (Admin) |
| `GET` | `/triage-settings` | Topic keywords and card redaction (Admin) |
| `PUT` | `/triage-settings` | Save topic keywords and card redaction (Admin) |

### 2.13 Settings: email

- Support addresses: add, make default, signature, DKIM selector; DNS check (SPF, DKIM, DMARC), forwarding check, send yourself a test.
- Failed emails: the list and Retry.

| | Route | What it does |
|---|---|---|
| `GET` | `/email-addresses` | Support email addresses, the default first (Admin) |
| `POST` | `/email-addresses` | Add a support email address (Admin) |
| `PATCH` | `/email-addresses/{address_id}` | Change an address's name, signature or DKIM selector, or make it the default (Admin) |
| `DELETE` | `/email-addresses/{address_id}` | Delete a support email address (Admin) |
| `POST` | `/email-addresses/{address_id}/dns-check` | Check the address's domain for SPF, DKIM and DMARC now (Admin) |
| `POST` | `/email-addresses/{address_id}/forwarding-check` | Email the address a one-time code to check it forwards to CeDesk (Admin) |
| `POST` | `/email-addresses/{address_id}/test` | Email yourself a test from this address (Admin) |
| `GET` | `/email/failed` | Admin: emails that could not be sent after every retry |
| `POST` | `/email/{email_id}/retry` | Admin: send a failed email again |

### 2.14 Settings: customer portal and help center

- Portal settings: who may sign in (allowed email domains), the CSAT survey and the public web form on or off.
- Help center: categories and articles (draft or published).

| | Route | What it does |
|---|---|---|
| `GET` | `/article-categories` | Categories |
| `POST` | `/article-categories` | Add a category (Admin) |
| `PATCH` | `/article-categories/{category_id}` | Rename or move a category (Admin) |
| `DELETE` | `/article-categories/{category_id}` | Delete a category; its articles stay, with no category (Admin) |
| `GET` | `/articles` | Articles, drafts included |
| `POST` | `/articles` | Write an article (Admin) |
| `GET` | `/articles/{article_id}` | One article |
| `PATCH` | `/articles/{article_id}` | Edit, publish or unpublish an article (Admin) |
| `DELETE` | `/articles/{article_id}` | Delete an article and its votes (Admin) |
| `GET` | `/portal-settings` | Portal settings (Admin) |
| `PUT` | `/portal-settings` | Save the portal settings (Admin) |

### 2.15 Settings: security, privacy and audit

- Security: minimum password length, password expiry, session length, 2FA required, IP list (test my IP first, so nobody is locked out), files need sign-in, admin alerts; the security score. SSO settings.
- Privacy: export everything about one customer (a JSON file kept 7 days) or erase them (type their email again to confirm; can't be undone).
- Audit log: filter by area, download CSV, verify the chain.

| | Route | What it does |
|---|---|---|
| `GET` | `/audit` | Audit log, newest first |
| `GET` | `/audit/export.csv` | Audit log as CSV (oldest first) |
| `POST` | `/audit/verify` | Verify chain |
| `POST` | `/privacy/erase` | Erase one customer (Admin; can't be undone) |
| `POST` | `/privacy/export` | Export everything about one customer (Admin) |
| `GET` | `/privacy/requests` | Recent exports and erasures (Admin), newest first |
| `GET` | `/privacy/requests/{request_id}/file` | Download an export (Admin, signed in) |
| `GET` | `/security-settings` | Security settings (Admin) |
| `PUT` | `/security-settings` | Save security settings (Admin; applies at once) |
| `GET` | `/security/score` | Security score (Admin) |
| `POST` | `/security/test-my-ip` | Test my IP against a list, or the saved one (Admin) |
| `GET` | `/sso-settings` | Single sign-on settings (Admin) |
| `PUT` | `/sso-settings` | Save single sign-on (Admin) |

### 2.16 Settings: apps, API keys, webhooks, Cevanos

- Apps: `GET /apps` lists each app with what to ask for. Build the install form from `fields`, in order: each has `label` (above the box), `hint` (under it), `send_as` (`settings`: send as `settings[key]`; `secret` / `signing_secret`: that field of the install body) and `secret` (a password box). `settings_fields`, `credential` (null when the app needs none) and `second_credential` say the same without labels. Credentials are never shown again, only `secret_hint`. `blocked` explains why an app can't be installed here.
- API keys and webhook secrets are shown **once**, on creation: say so.
- Webhooks: events, delivery log, retry, test ping, new secret. In the log, show `state` (`delivered`, `pending` = will try again at `next_try_at`, `failed`) and `tries` (a number); `attempts` is the list of tries (`at`, `status`, `ms`, `error`). Offer Retry only when `state` is `failed`. Retry answers 409 while the webhook is turned off: show its message. The test ping answers the delivery: show `state` and `last_status` / `last_error`, not just "sent".
- Cevanos: connect with a key, check, disconnect. Failed events list (outbox). Under "Webhook address" show the full address and list `webhook_events` (the four events to subscribe the webhook to in Cevanos), so the Admin knows exactly what to choose there.

| | Route | What it does |
|---|---|---|
| `GET` | `/api-keys` | API keys (Admin) |
| `POST` | `/api-keys` | Make an API key (Admin; the key is shown once) |
| `DELETE` | `/api-keys/{key_id}` | Revoke an API key (Admin) |
| `GET` | `/apps` | Apps and what is installed (Settings) |
| `PUT` | `/apps/{app}` | Install an app, or change its settings and credential (Settings) |
| `DELETE` | `/apps/{app}` | Uninstall an app (Settings; its credential is deleted) |
| `GET` | `/cevanos/connection` | The Cevanos connection (Admin) |
| `PUT` | `/cevanos/connection` | Connect Cevanos, or change the connection (Admin) |
| `DELETE` | `/cevanos/connection` | Disconnect Cevanos (Admin). Tickets stay; their chats stop syncing. |
| `POST` | `/cevanos/connection/check` | Check the saved key with Cevanos (Admin) |
| `GET` | `/outbox/failed` | Admin: events that could not be delivered after every retry |
| `POST` | `/outbox/{event_id}/retry` | Admin: try a failed event again now |
| `GET` | `/webhooks` | Webhooks (Admin) |
| `POST` | `/webhooks` | Add a webhook (Admin; the secret is shown once) |
| `GET` | `/webhooks/deliveries` | Delivery log (Admin) |
| `POST` | `/webhooks/deliveries/{delivery_id}/retry` | Send a failed delivery again (Admin) |
| `PATCH` | `/webhooks/{webhook_id}` | Change a webhook (Admin; only what is sent changes) |
| `DELETE` | `/webhooks/{webhook_id}` | Delete a webhook (Admin; its queue and log go too) |
| `POST` | `/webhooks/{webhook_id}/secret` | A new secret (Admin; shown once, the old one stops at once) |
| `POST` | `/webhooks/{webhook_id}/test` | Send a test ping now (Admin) |

### 2.17 Settings: imports

- Connect a help desk (or upload a CSV), see what it holds and the team each group maps to, dry run, run, watch progress, sync now, go live (stop syncing), history.

| | Route | What it does |
|---|---|---|
| `POST` | `/imports/connections` | Connect a help desk to import from (Admin) |
| `POST` | `/imports/connections/{connection_id}/close` | Delete the stored credentials or file now (Admin) |
| `GET` | `/imports/connections/{connection_id}/discover` | What the source holds, and the team each group probably maps to (Admin) |
| `POST` | `/imports/connections/{connection_id}/dry-run` | What a run would do, saving nothing (Admin) |
| `POST` | `/imports/connections/{connection_id}/run` | Start importing (Admin) |
| `POST` | `/imports/csv` | Upload a CSV file to import (Admin) |
| `GET` | `/imports/history` | Every import: when, from where, by whom, how it went (Admin) |
| `GET` | `/imports/runs/{run_id}` | A run's progress (Admin) |
| `POST` | `/imports/runs/{run_id}/sync-now` | Ask the source for changes now instead of in 5 minutes (Admin) |
| `POST` | `/imports/runs/{run_id}/sync-stop` | Go-live: stop syncing and delete the stored credentials (Admin) |

### 2.18 Settings: plan, retention, archive and demo

- Usage and plan limits (cloud). Retention: delete resolved tickets after N days, and `export_before_delete` (write them to a file first, kept 30 days: Admins list and download them under Retention exports). Archive age (default 120 days).
- Demo (not on production servers): load the demo, load sample tickets (for an empty inbox), reset the demo.

| | Route | What it does |
|---|---|---|
| `GET` | `/archive/settings` | The archive age (Settings) |
| `PUT` | `/archive/settings` | Set the archive age (Settings; audited) |
| `POST` | `/demo/reset` | Reset the demo |
| `POST` | `/demo/samples` | Load sample tickets |
| `POST` | `/demo/seed` | Load the demo |
| `GET` | `/retention` | Retention (Settings) |
| `PUT` | `/retention` | Set retention (Settings; audited) |
| `GET` | `/retention/exports` | What retention wrote before deleting (Admin) |
| `GET` | `/retention/exports/{export_id}/file` | Download a retention export (Admin; audited) |
| `GET` | `/usage` | Usage and plan limits (Settings) |

### 2.19 Customer portal (customers, signed in with an emailed code)

- Sign in: email, then the 6-digit code from the email. The customer's own tickets only; new ticket (types shown to customers), reply, rate a resolved ticket. Their own live stream.
- The emailed CSAT survey link opens `/public/csat/{token}` (no sign-in).

| | Route | What it does |
|---|---|---|
| `GET` | `/portal/attachments/{attachment_id}` | A short-lived link to a file on my ticket (customer) |
| `GET` | `/portal/attachments/{attachment_id}/content` | Download a file on my ticket, signed in (customer) |
| `GET` | `/portal/me` | The signed-in customer |
| `POST` | `/portal/sign-out` | Sign out (customer) |
| `GET` | `/portal/stream` | Live updates for a portal customer (Server-Sent Events) |
| `POST` | `/portal/stream/ticket` | A one-use ticket to open the customer's live stream |
| `GET` | `/portal/ticket-types` | Ticket types to pick from on New ticket (customer) |
| `GET` | `/portal/tickets` | My tickets (customer) |
| `POST` | `/portal/tickets` | Open a ticket (customer) |
| `GET` | `/portal/tickets/{ticket_id}` | One of my tickets (customer) |
| `POST` | `/portal/tickets/{ticket_id}/csat` | Rate my resolved ticket (customer) |
| `POST` | `/portal/tickets/{ticket_id}/messages` | Reply on my ticket (customer) |
| `POST` | `/portal/{workspace}/code` | Email me a sign-in code (customer) |
| `POST` | `/portal/{workspace}/session` | Sign in with the emailed code (customer) |
| `GET` | `/public/csat/{token}` | The rating page behind an emailed survey link (no sign-in) |
| `POST` | `/public/csat/{token}` | Rate a ticket from the emailed survey link (no sign-in) |

### 2.20 Public pages (no sign-in): help center and web form

- Help center: categories, search, articles, "Was this helpful?".
- Web form: the ticket types and fields to show, then send it (rate limited).

| | Route | What it does |
|---|---|---|
| `GET` | `/public/{workspace}/articles` | The help center: categories, articles, search (no sign-in) |
| `GET` | `/public/{workspace}/articles/{article_id}` | One published article (no sign-in) |
| `POST` | `/public/{workspace}/articles/{article_id}/vote` | "Was this helpful?" (no sign-in) |
| `GET` | `/public/{workspace}/form` | The web form: ticket types and their fields (no sign-in) |
| `POST` | `/public/{workspace}/form` | Send the web form: makes a ticket (no sign-in) |

### 2.21 Mobile app

- `GET /me/queue`: mine and unassigned, not resolved; mine first, then chats waiting to be accepted, then by SLA. Each item has the last message's first 140 characters.
- `POST /tickets/{id}/mobile-reply`: one tap; accepts a waiting chat first; a light agent's text is saved as a note (`saved_as`). Everything else uses the web routes.
- `POST /me/devices` registers the phone for push banners after sign-in; `DELETE /me/devices/{id}` on sign-out; `GET /me/devices` lists them and says whether push is on for this server.

| | Route | What it does |
|---|---|---|
| `GET` | `/me/devices` | My phones that get banners |
| `POST` | `/me/devices` | Register this phone for banners |
| `DELETE` | `/me/devices/{device_id}` | Stop banners on this phone (call it on sign-out) |
| `GET` | `/me/queue` | Your mobile queue |
| `POST` | `/tickets/{ticket_id}/mobile-reply` | Reply from the mobile app |

### 2.22 Not for the frontend: server to server

- Called by other systems with their own keys or signatures: mail services, Cevanos, Slack, other systems through the inbound API, and health checks.

| | Route | What it does |
|---|---|---|
| `POST` | `/cevanos/events/{workspace_id}` | Cevanos delivers a signed webhook (no sign-in; CXOps-Signature required) |
| `GET` | `/healthz` | The process is up |
| `POST` | `/inbound/email` | A mail service delivers one incoming email (token required) |
| `POST` | `/inbound/tickets` | A customer's new request from another system (API key) |
| `POST` | `/inbound/tickets/{ticket_id}/messages` | The customer's next message from another system (API key) |
| `GET` | `/readyz` | The database answers |
| `POST` | `/slack/events/{workspace_id}` | Slack's Events API (the Slack side conversations app's Request URL) |
