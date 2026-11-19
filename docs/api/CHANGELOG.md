# CeDesk API changelog

`openapi.json` next to this file is the API as served (`uv run cedesk openapi`). Every
route is under `/api/v1`. Breaking changes only come in v2 (md 2B). Newest first.

## 2026-11-19: Jira: a clickable link back to the ticket, and clear "not found" messages; Slack alerts and side conversations read better

No route or field changed. No migration. Found testing the Jira app against a real Jira
Cloud site, and both Slack apps against a real Slack workspace.

- **Slack side conversations, replies read better** (a reply's `body` in
  `GET /tickets/{id}/side-conversations`):
  - **A pasted link showed twice.** Slack labels it with the address minus `https://`, and
    it read "example.com/logs (https://example.com/logs)". Now it's just the address. A
    real label still reads "label (address)".
  - **Mentioned colleagues get their names:** `@Meena`, was `@U07ABC123`. Only the
    replier's name was asked for before. Up to 5 people per reply, asked together within
    the same 1.5 s.
  - **A shared file without its name in Slack's event** (`file_access: check_file_info`)
    read "[file: unnamed]". Its name is now asked for with `files.info`, so the Slack app
    needs the **`files:read`** bot scope; the file itself is still never fetched. Without
    the scope, or if Slack doesn't answer in time, the line is "[file]".
  - A reply that is only a file, sent without `text` and without a subtype (Slack's
    current form), is kept.
- **Slack side conversations, clearer channel errors** (422 on
  `POST /tickets/{id}/side-conversations/slack`): `channel_not_found` adds "If it's a
  private channel, invite the bot there first: type /invite @<your bot> in it." (a
  private channel the bot isn't in answers the same). An archived channel says so.
- The Slack app's setup text (`GET /apps`, `credential_help`) lists `files:read`.

- **Slack alerts, clearer messages** when Slack refuses the webhook. They show in a
  failed event's `last_error` (`GET /outbox/failed`, Settings > Channels > Failed events),
  and as the 422 when installing with such a webhook (`PUT /apps/slack_alerts`). Both used
  to say only "Slack has no such thing.":
  - 404 (removed in Slack, the Slack app uninstalled, or the channel deleted): "Slack no
    longer has this webhook: ... Add a new webhook in Slack (Incoming Webhooks) and
    install Slack alerts with its URL.";
  - 410 (channel archived): "The webhook's Slack channel is archived. ...";
  - 401/403 (revoked, or the channel doesn't let apps post): "Slack refused this
    webhook: ...".

- **Fixed:** the description of an issue made from a ticket ends with "From CeDesk ticket
  T-1042: <link>", but the link was plain text in Jira (Jira doesn't turn text into
  links), so it had to be copied. Every http(s) address in the description is now sent
  as a link (Atlassian Document Format link marks), the ticket's and any the agent
  wrote; punctuation right after an address stays text.
- **Clearer messages** (422) where Jira answers 404:
  - `POST /tickets/{id}/jira/link` with a key Jira doesn't have: "Jira has no issue
    KAN-777, or the Jira account in Settings > Apps can't see it." (was "Jira has no such
    thing.");
  - installing Jira with a project it doesn't have: "Jira has no project NOPE, or this
    account can't see it."; with a site that doesn't exist: "There's no Jira site at
    acme.atlassian.net. Check the site name."
- Other apps' 404 now says "<App> couldn't find what was asked for (404)." instead of
  "<App> has no such thing."

## 2026-11-18: Self-service sign-up (create a workspace from the sign-in page)

All additive; nothing existing changed. Migration 0060 adds two tables. Off unless
`CEDESK_SIGNUP_ENABLED=true`: then these routes answer 404, except the config route.

- **`GET /auth/signup-config`** (no sign-in): `{enabled, captcha_site_key}`. Show the
  "Create a workspace" link only when `enabled`; `captcha_site_key` is the Cloudflare
  Turnstile key to show, or null for no CAPTCHA.
- **`POST /auth/signup`** `{workspace, workspace_name, name, email, password,
  captcha_token}`: 202 `{signup_id, code_expires_at, resend_after}`. Nothing is created
  yet: a 6-digit code (10 minutes, 5 tries) is emailed. 409 `slug_taken`, 422 (an address
  that is reserved or not 3-40 lowercase letters, digits and single dashes; a bad email;
  a password under 10 characters; a name or workspace name with a line break, a hidden
  character, or a web or email address in it), 403 `captcha_failed`, 429 (5 sign-ups an hour per IP,
  3 code emails an hour per email from one IP, 10 per email in all). A new sign-up for an
  email from the same IP replaces its waiting one; one from another IP doesn't.
  The code email holds only the code and CeDesk's own words: nothing typed on the form
  (it can name anyone's email) goes into it.
- **`POST /auth/signup/verify`** `{signup_id, code, workspace?}`: 200 `{workspace}`, then
  sign in as usual. Creates the workspace, its first Admin, the default teams and ticket
  types, the cloud plan (`CEDESK_SIGNUP_DEFAULT_PLAN`, cloud edition only) and the first
  audit entries ("Self-service sign-up", area `workspace`). Safe to repeat: the same code
  answers the same. 422 `bad_code`, 410 `code_expired` (resend) or `signup_expired`
  (start over), 409 `slug_taken` (send the same code again with a new `workspace`), 429
  (3 workspaces a day per IP).
- **`POST /auth/signup/resend`** `{signup_id}`: 202 as above; the old code stops working,
  and its email is not sent if it hadn't gone yet.
  429 before `resend_after` (60 s), or after 3 code emails an hour from this IP (10 per
  email in all).
- **Audit**: a new area `workspace`.

## 2026-11-18: Limiting the portal to some domains signs the others out

No route or field changed. No migration.

- **Fixed (S7):** when an Admin saved a domain list (`PUT /portal-settings`,
  `allowed_domains`), only new sign-ins were refused: a customer already signed in with
  another email kept using the portal until their session ended (found in the Phase 3
  retest). Now saving the list ends those sessions at once. Their next portal request
  answers 401 and their live stream ends (`signed_out`); they can't sign in again. Opening
  the list later doesn't bring an ended session back. The audit entry for the change says
  how many sessions ended (`portal_sessions_ended`).
- Every portal request also checks the email against the list, so a session made at the
  very moment the list was saved is refused on its next request: 401 with the list's
  message, e.g. "Only acme.com emails can open tickets here."

## 2026-11-18: App install forms with labels, webhook retry while off, portal names

All additive; no field was removed or renamed. No migration. Found in the Phase 3 retest
of the portal and Settings > Developers on the test server.

- **New `fields` on `GET /apps` (and every app answer).** The install form, in order:
  `{key, label, hint, send_as, secret}` for every setting and credential, e.g. Jira:
  "Jira site" (hint "acme for acme.atlassian.net."), "Atlassian account email", "Project
  key", "API token". `send_as` is `settings` (send as `settings[key]`), `secret` or
  `signing_secret` (that field of the install body); `secret` true means a password box.
  Before, only the bare keys came (`settings_fields: ["site", "email", "project"]`), so
  the Jira and Calendly boxes showed no label. `settings_fields`, `credential` and
  `second_credential` stay and say the same.
- **`POST /webhooks/deliveries/{id}/retry` answers 409** "The webhook is turned off. Turn
  it on, then send this again." when the delivery's webhook is turned off. Before, it
  answered 204 and queued the delivery, which the worker then refused ("The webhook is
  turned off."), so the page said "sent again" for something never sent. Nothing is
  queued now; once the webhook is on, the retry works as before.
- **A new customer's name** (portal sign-in, and a web form sent without a name) is made
  from the part of the email before the @: `priya.shah@` is "Priya Shah", `ravi_k+orders@`
  is "Ravi K". Before it was the bare "priya.shah", so a reply's
  `{{customer.first_name}}` said "Hi priya.shah". A known customer keeps their name.
- Tests: the DB tests connect to `127.0.0.1:5447` by default instead of `localhost`.
  Compose publishes the port on 127.0.0.1 only, and on Windows `localhost` tries IPv6
  first, so every connection waited about 2 s. `CEDESK_TEST_OWNER_URL` and
  `CEDESK_TEST_APP_URL` still override it.

## 2026-11-18: Imported help center articles are Markdown

No route or field changed. No migration.

- **Fixed (P6):** an article imported from **Zendesk or Intercom** kept the source's HTML,
  so the help center (which shows `body` as Markdown, without raw HTML) showed tags such
  as `<p class="no-margin">` and `<h2>` as text. Every article import now writes the body
  as Markdown: headings, paragraphs, line breaks, lists (nested too), bold, italics, links,
  images, quotes, code and rules keep their shape; a table becomes one line per row, cells
  split by ` | `; an embedded video becomes a link. Scripts and styles are dropped, and so
  is any other tag (its text stays). Only http(s) and mailto links and http(s) images are
  kept; a relative link keeps its text. Text is escaped so it reads as written
  (`order_id` stays `order_id`).
- **Freshdesk, Zoho Desk and Help Scout** articles, which were turned into plain text, now
  keep their formatting the same way (Freshdesk reads `description`, and
  `description_text` only when there is no HTML).
- **Articles imported before this:** re-importing doesn't change an article that is
  already here, so run **`cedesk articles-markdown`** once on each server. It rewrites
  imported articles whose body still holds HTML tags and leaves everything else alone: an
  Admin's own articles and bodies that are already text or Markdown. Each article keeps
  its dates and who last edited it. It prints how many it changed, and is safe to run
  again.

## 2026-11-17: Imports: the run report, new credentials, and what imports now keep

All additive; no field was removed or renamed. Migrations 0057, 0058, 0059.

**Run report (P0-3, P0-6).** `GET /imports/runs/{id}` (and every run in lists) gains:

- `outcome`: `in_progress`, `complete` (nothing left out, cut, linked or noted),
  `complete_with_exceptions` (it ended, but the report lists something) or `failed`.
  A run never says `complete` over incomplete data.
- `report`: `open_failures` (see `GET /imports/runs/{id}/failures`); `losses`, a list of
  `{key, count, meaning}` (for example `files_linked`, `file_gone`, `file_placeholder`,
  `history_capped`, `body_as_file`, `tags_over_limit`, `date_replaced`, `archive_capped`,
  `article_cut`); `warnings`, what the source can't give, said before the run **or found
  during it** (each line once); `reconciliation` per kind: `{in_source, accounted_for,
  difference}`.

**New `POST /imports/connections/{id}/credentials`** (Admin, 200, returns the connection).
For an import that stopped because the source refused the key (a revoked key, an expired
token): body `{"credentials": {...}}`, the same keys as when connecting. The new credentials
are checked with the source and must reach **the same account** (409 "another account"
otherwise); they replace the old ones, then `POST /imports/runs/{id}/resume` carries on.
422 when the source refuses them, 409 when the connection was closed or is a CSV file, 503
when the source can't be reached. The history records `credentials_updated` and the audit
log an entry; neither holds the credentials.

**Resume** (`POST /imports/runs/{id}/resume`) of a CSV run is now refused (409 "no longer
stored") when storage no longer has the file, instead of queuing a run that fails at once.

**Sync (P1-10).** A sync look no longer undoes a CeDesk edit: status, priority, owner, team,
tags, subject and fields change only where the source changed them since the last look;
the ticket's system line says what changed ("Updated from Zendesk: ..."), and "(replacing a
CeDesk change)" when it overwrote an edit made in CeDesk. A tag removed in the source is
removed in CeDesk; an owner removed in the source is unassigned.

**Tickets and contacts.**

- `GET /tickets/{id}` `contact` gains `phone` and `organization` (from an import, when the
  source had them; null otherwise). An import fills them only where blank; erasure clears
  them.
- Messages gain `recipients` (`{to, cc, bcc}`, imported messages only): who the old help
  desk says the message went to. Never used for replies (`email` is unchanged).
- Imported tickets carry more `fields`, the source's own data CeDesk has no column for:
  `source_type`, `source_url`, `more_tags` (tags past 20), `source_cc`,
  `source_followers`, `source_links` (e.g. `related 123; parent 456`), `source_sla_due_at`,
  `source_sla_breached`. A link to a ticket already imported is also a CeDesk link.
- Imported articles keep the source's published and updated dates; a translation is its own
  article titled with its locale, e.g. "Refunds (fr)".
- **Portal fix (imported tickets only):** on an imported ticket the customer now sees only
  the fields of the ticket's type. Before, `GET /portal/tickets/{id}` and the list showed
  every field an import brought (the old desk's custom fields, `source_url`, `source_cc`,
  ...). Tickets not imported show their fields exactly as before.
- **Erasure:** erasing a contact now also removes their address from `source_cc` and
  `source_followers` on other customers' imported tickets and from imported messages'
  `recipients` (the other addresses stay; the erase response counts
  `imported_addresses` and `imported_recipients`). The privacy export includes `phone` and
  `organization`.

**Connect (`POST /imports/connections`).**

| source | change |
|---|---|
| `zendesk` | credentials: `client_id` + `client_secret` (an OAuth client, recommended), or `access_token`, or `email` + `token` (API token; Zendesk retires these 30 Apr 2027) |
| `freshdesk` | archived tickets are now imported **by default**; `include_archived`: `"false"` turns it off |
| `zoho` | `include_archived`: `"true"` imports archived tickets (needs the `Desk.search.READ` scope; at most 5,000 per department) |
| `hubspot` | `token`: a private app's access token or a service key; optional scopes add calls, meetings and inbox threads |

`credentials_hint` comes from `client_id` or `access_token` when those are used, never from
a secret.

## 2026-11-17: Imports: resume a failed import where it stopped

- New `POST /imports/runs/{run_id}/resume` (Admin, 202, returns the run): queues a `failed`
  run again; the worker carries on from its last saved page (the failed page is read again,
  the ones before it are not). The same run keeps its id, counts, options, team mapping, sync
  setting, start time and failures; `retry_attempts` starts from 0 and `last_error` is
  cleared. 409 when the run isn't failed (in progress, finished or syncing), when its
  connection was closed or its stored credentials can't be read, or when another run of the
  connection is in progress or syncing; nothing changes then. 404 for an unknown run (or one
  in another workspace). Two resumes at once queue it once; accepts `Idempotency-Key`.
- The import history records a `resumed` event (who, and where it carries on from: kinds
  done, whether a page was saved, what was made so far, why it had stopped; never the
  source's raw cursor). The audit log has a `resumed` entry under imports.
- Starting a run, resuming one and closing a connection now take turns on the connection,
  so they can't interleave. Existing responses are unchanged.
- Fix: `POST /imports/runs/{id}/sync-stop` no longer clears the run's lease. Stopping the
  sync while the first import (or a sync look) was running let a second worker take the same
  run. And a stop at the very moment the import ended could leave the run finished, not
  syncing, but with its credentials kept; the stop now waits for the worker and deletes them.
  Responses are unchanged (200 with the run; 409 when the run isn't syncing).
- Workers: each run now carries a lease token (migration 0056). A worker that stalled
  past its lease, after another worker took the run, can no longer save a page, move the
  cursor, pause, retry, fail or finish the run; its page is rolled back and its copied files
  deleted. No API change.

## 2026-11-17: Imports: one record that can't be read never stops a run; retries end (P0-2)

- New `GET /imports/runs/{run_id}/failures` (Admin, cursor pages, last failed first): the
  records a run left out because the source could not give them, each with `kind`,
  `external_id`, `category` (`not_found`: deleted in the source after it was listed;
  `temporary`: it kept failing after every retry; `incomplete`: imported without all its
  history), `attempts`, `last_error` (our words, never a credential or customer text),
  `first_failed_at`, `last_failed_at`, `status` (`open`; `resolved`: read in full later;
  `dismissed`: an Admin accepted the loss), `resolved_at` and `resolved_by_id`. They stay after
  the run ends. A failure is resolved only when the record is read in full again, never just
  because the ticket exists.
- New `POST /imports/runs/{run_id}/failures/{failure_id}/dismiss` (Admin): ends an open
  failure as `dismissed`, recording who decided. 409 when it is not open; 404 when unknown.
- Run objects (`GET /imports/runs/{id}` and the other routes that return a run) carry
  `retry_attempts`: failed tries of the page the run is on. `counts.<kind>.failed` counts the
  records left out and `counts.<kind>.incomplete` those imported without all their history.
  New fields; nothing else changes.
- Behaviour: a ticket deleted after it was listed no longer fails the import (Freshdesk, Help
  Scout, Zoho, Gorgias; an Intercom conversation is now noted instead of dropped silently). A
  Zendesk ticket whose comments answer 404 is still imported with its description, but is now
  listed as `incomplete` instead of passing as complete (likewise a Zoho thread gone mid-read).
  A temporary error is retried with backoff for a bounded number of tries (settings
  `import_retry_*`); after that the failing record is set aside, or, if the page itself can't
  be read, the run fails with a message that says so. Refused credentials still stop a run.

## 2026-11-17: The ticket list shows each ticket's Jira issues (Phase 3, T10)

- `GET /tickets` rows (and `GET /tickets/{id}`) carry `jira_keys`: the keys of the Jira
  issues linked to the ticket (`["SUP-7", "SUP-12"]`), by key, or `[]`. It is read in the
  same query as the row, so the list makes one call. A new field; nothing else changes.
## 2026-11-16: UI test fixes: a bell on manual assign, a VIP line, the customer's rating, the Cevanos events

- Assigning a ticket to someone else by hand (`PATCH /tickets/{id}` with `assignee_id`, or
  `POST /tickets` with one) now sends them an `assigned` notification ("<who> assigned you
  T-123: subject"). Taking a ticket yourself, unassigning, and an unchanged assignee send
  nothing. Auto-assignment keeps its own text.
- Turning a customer's VIP flag on or off (`PATCH /contacts/{id}/profile`) now writes a system
  line ("Customer marked VIP by <who>", "VIP removed by <who>") on each of the customer's open
  tickets, the same set the SLA refresh walks. Resolved tickets and other customers get none.
- `GET /contacts/{id}/tickets` and `PATCH /contacts/{id}/profile`: `contact` now has
  `csat_average` (1-5, null: none yet) and `csat_count`, the same two fields the ticket's
  customer card already had.
- `GET/PUT /cevanos/connection`: new `webhook_events`, the four events (`conversation.escalated`,
  `.classified`, `.reopened`, `.closed`) to subscribe the Cevanos webhook to. `webhook_path`
  is unchanged.

## 2026-11-16: Imports: the dry run says when "to create" is only a guess (Phase 2, M4)

- `POST /imports/connections/{id}/dry-run`: `will_create` is the source's total minus what
  this help desk brought before (totals, not ids). When something from it is here already,
  `warnings` now says the count is a guess, and when more is here than the account has
  (another account of the same help desk, or deleted there) it says why the count shows 0.
  The run itself checks each one by its id. No field changes.

## 2026-11-16: Imports: an import run again updates tickets, and sync brings owner, team and tags (Phase 2, M9)

- An import run again on an account already imported no longer only skips the tickets that
  are here: each one gets its new messages, status, owner, team and new tags, the same as a
  sync look. In `counts.tickets`, such a ticket counts as `updated`; one with nothing new
  stays `skipped`.
- A sync look (and a re-import) now also sets the ticket's assignee when the source's
  assignee is an active CeDesk member (matched by email), its team when the source's group is
  mapped in `team_map`, and adds the source's new tags. An owner the source doesn't name, or
  names but CeDesk doesn't know, is left as it is; tags are never removed. Each such change
  adds a system line "Updated from <source>: ...". No route or field changes.

## 2026-11-16: Outbound calls: IPv4 first, and the next address when one can't be reached

Imports, webhooks and apps connect through the pinned transport. It used the first checked
address, and IPv6 addresses sort first, so from a server or container with no IPv6 route a
host with both (e.g. Intercom's `intercom-attachments-*.com`, where email files live) failed
with "can't be reached: ConnectError" and the run retried forever. It now tries IPv4
addresses first, then the next checked address when one can't be connected to (still only
addresses that passed the public check). No route or field changes.
## 2026-11-16: Cevanos AI features, orders from Cevanos, Governance holds, retention export (Phase 3, AI1-AI7, X7, X11, C12, O2)

Every AI feature works **only when the workspace has a Cevanos connection** (the gate is
the connection, not `CEDESK_EDITION`; CeDesk itself has no AI). The Cevanos routes these
call are in `docs/api/CEVANOS_V1_FOR_CEDESK.md`.

- `GET /me` has `ai`: true with a Cevanos connection. Show the AI buttons and the AI card
  only then.
- Without a connection every AI route answers **409 `cevanos_not_connected`** ("Connect
  Cevanos in Settings to use this."). Cevanos down or slow: **503**. The workspace's key
  without the scope a route needs: **409 `cevanos_scope_missing`**, naming the scope.
  Cevanos refusing the key: 409 `cevanos_refused`. Errors may now carry such a code
  instead of the class name.
- Nothing an AI answer says is stored or audited; card numbers are taken out of every
  text before it leaves CeDesk. API keys can't use the AI help (403: it spends the
  Cevanos workspace's AI credits).
- `POST /tickets/{id}/summary` -> `{summary}` (AI1, X11): the conversation, internal notes
  too (it is for agents), never system lines. Right to read the ticket; team scope.
- `POST /tickets/{id}/draft` -> `{text}` (AI4): a suggested reply for the reply box, never
  sent by itself. Written without internal notes, by the chat's Cevanos AI agent for a
  ticket from a Cevanos chat. Needs `reply` (light agents: 403).
- `POST /assist/improve {text, tone?}` -> `{text}` (AI2). `tone`: professional (default),
  friendly, empathetic, concise. At most 8000 characters.
- `POST /assist/translate {text, to}` -> `{text, from}` (AI3). `to` is a language code
  (en, hi, pt-BR); `from` is what Cevanos found. A ticket's `language` (triage) says what
  the customer writes in.
- `GET /reports/ai?from&to` -> `{from, to, ai: {conversations, handed_over,
  handover_rate, ai_resolved, ai_resolved_rate, ai_csat, ai_csat_responses}}` (AI7):
  rates 0-1, CSAT 1-5, null when there is nothing to count. Needs `reports`; the period
  as the other reports (default the last 30 days).
- `POST /cevanos/connection/check` also lists the AI scopes a key lacks (`ai:assist`,
  `contacts:read`, `analytics:read`) in `missing_scopes`; `ok` stays true (handovers work).
- **Sidebar orders and payments from Cevanos** (AI5, C12): with a connection,
  `GET /tickets/{id}/orders` and `/payments` read from the stores connected in Cevanos
  (`source: "cevanos"`), for the chat's contact (a ticket from a Cevanos chat) or the
  customer's email. A store Cevanos couldn't read is named in `message`
  ("Not read from woocommerce just now."); Cevanos down, a missing `contacts:read` or an
  unknown customer makes the card `available: false` with the reason, never the ticket.
  `PaymentOut.url` may now be null.
- **The chat's real customer** (AI6): after a handover (or a convert) is saved, CeDesk asks
  Cevanos who the chat's customer is. Their email already a customer here: the ticket
  joins them (their name kept) and the "Chat customer" placeholder is deleted. Else the
  placeholder takes the real name and email. Best effort: anything failing keeps the
  placeholder.
- **Governance holds** (X7): a `conversation.escalated` webhook may carry
  `data.hold {id, action, amount?, currency?}` when Cevanos's AI handed over because
  Governance holds an action. It becomes an approval request on the ticket ("Refund
  499.00 INR (Cevanos Governance hold)"), once per hold; a bad hold refuses the webhook
  (422). `ApprovalOut` has `cevanos_hold_id`; for a hold `requester_*` and `approver_*`
  are null: anyone with `approve` who sees the ticket decides it, and it is in their
  `GET /approvals/waiting`. The decision goes to Cevanos by the worker (retried, with an
  Idempotency-Key); the ticket says when Cevanos was told, or why not.
- **Export before retention deletes** (O2): `PUT /retention` takes
  `export_before_delete` (left out or null: unchanged; `GET /retention` shows it). When
  on, each batch retention deletes (live and archived tickets) is first written to a
  JSON file: the privacy export's ticket shape with each ticket's `customer`. Kept 30
  days. `GET /retention/exports` (Admin) lists them; `GET /retention/exports/{id}/file`
  downloads one (audited, area `retention`, action `downloaded`). Erasing a customer
  deletes every export holding their tickets (erase `counts.retention_exports`). With the
  switch on and no storage, or a file that can't be written, nothing is deleted. The
  run's audit detail gets `exports`.

Migration 0053 (after 0052): `hd_approval.cevanos_hold_id` (requester and approver
nullable for holds only), outbox event `approval.decided`,
`hd_retention.export_before_delete`, `hd_retention_export`. Downgrade refuses while hold
approvals or retention exports exist.

## 2026-11-16: Imports: a sync counts a status-only change as updated (Phase 2, M9)

`GET /imports/runs/{id}`: in `counts.sync`, `updated` now also counts a ticket whose status,
priority or resolved time changed in the source with no new message. Before, only tickets
with new messages were counted (the change itself was always saved). A look that finds
nothing really changed adds nothing. No route or field changes.

## 2026-11-16: Archived ticket page: the type and the rest of its history (Phase 3, O3)

`GET /archive/tickets/{id}` now returns everything the archive keeps, typed:

- `type_name`: the ticket type as it was when archived (the type may be gone).
- `related`: one list per kind, oldest first, named as on a live ticket so the same
  components show both:
  - `time_log` (minutes, billable, note, member_name, logged_at)
  - `tasks` (text, owner_name, due_at, done_at, created_at)
  - `approvals` (what, amount, currency, state, decision_note, requester_name,
    approver_name, decided_by_name, decided_at, created_at)
  - `links` (the other ticket's `ticket_id`, `number`, `label`; kind, created_at)
  - `side_conversations` (subject, via, `to`, from_address, created_at,
    last_message_at, and `messages`: direction, author_name, from_address, body,
    created_at)
  - `jira_issues` (`key`, url, made_here, created_at)
  - `emails` sent from it (from_address, to_addresses, cc_addresses, subject,
    body_text, `sensitive`, status, sent_at, created_at)
  - `cevanos_chat`: for a handover, the Cevanos chat it came from (conversation_id,
    agent_id, channel_kind, `handover`: the handover card, taken_over_at, closed_at,
    created_at); else null. Its messages are the ticket's messages. Never its polling
    state.
- An email that carried a secret (a reset link) has `sensitive: true` and `body_text`
  null: it is archived without its body, and never shown even if one were kept.
- Tickets archived before migration 0052 have empty lists and a null `cevanos_chat`.
- Same rights and team scope as before: a team-only role gets 404 for another team's.

Changed from the first version on `main` (2026-11-16, never used by the frontend): the
lists were at the top level as untyped objects; they are now under `related`, and
`side_conversations[].to_addresses` is `to` and `jira_issues[].issue_key` is `key`, as on
live tickets. No migration.

## 2026-11-13: Mobile agent app: queue and one-tap reply (Phase 3, P8)

Two routes for the mobile app. Everything else on a phone (the ticket screen, notes, status,
notifications) uses the ordinary routes. Same rights and team scope as the web app.

- `GET /me/queue?limit=` (1-100, default 50): tickets not resolved and not merged that are
  yours or nobody's. Yours first, then chats waiting to be accepted, then by SLA time left,
  then oldest. Each has `mine`, `waiting_to_accept`, the contact's name and the last message
  a customer can see (`last_kind` customer, agent or ai; first 140 characters; never a
  note). A `team_only` role sees its team's only.
- `POST /tickets/{id}/mobile-reply` `{"body"}` -> 201 `{message, saved_as, accepted}`. On a
  chat handover nobody has accepted, the reply accepts it first (line "Neha joined the
  chat (mobile)", so the customer hears a person joined). `accepted` is the ticket's state
  after the call (true once accepted, by this reply or before), so a replay or a second tap
  answers the same as the first. A second tap that finds the chat just accepted by the first
  replies all the same (no 409). If the reply fails after the accept, the accept stands and
  the error message says so; send the reply again. A light agent can't reply: the text is
  saved as a private note, `saved_as` is `note` and a waiting chat stays waiting.
  `Idempotency-Key` is honoured, so a double tap with one key sends once. 403 on another
  team's ticket (view only), 404 on an unknown one.

Push notifications to phones came with this release: see "Push notifications to the mobile
app" below.
## 2026-11-13: Demo and sample tickets (Phase 3, D1-D3)

Three routes for sales demos and for trying CeDesk out. Admin only (a custom role with
`settings` is not enough), audited (area `demo`), and off unless the server turns them on
with the new setting `CEDESK_DEMO_ENABLED=true` (default false). 403 "Demo data is off on
this server..." when it is not set, and 403 "Demo data is never on a production server
(CEDESK_ENV=production)." on production even when it is. Each answers
`{"created", "deleted"}`.

- `POST /demo/seed` -> 201: 16 tickets. The 6 samples; 5 past tickets resolved over the
  last week, each with a CSAT rating and a first reply and resolution that took hours; 4
  live tickets assigned to you (a reply, a note, a task, time logged, one Pending); and 1
  chat handover waiting to be accepted. 409 "Demo data is already there; use reset." when
  demo tickets are already there.
- `POST /demo/samples` -> 201: "Load sample tickets", the 6 samples only (new, from
  customers on email, chat and the web). 409 when the samples are already there.
- `POST /demo/reset` -> 200: loads the demo again, then deletes the old demo tickets
  (with their messages, notes, tasks, time, approvals, ratings and files) and the demo
  customers left with no ticket. Only demo data is touched: tickets made by hand,
  imported or from a channel stay, and so do members and settings. If a step fails, what
  was made is deleted again and the old demo stays as it was. Audited once, as `reset`
  with `{"deleted", "created"}`. Other workspaces are never touched.

Only one demo call runs at a time per workspace (a second one waits, then gets its 409).
Customers write in through a channel, so the rules, triage, routing and SLA run on every
ticket (the "refund" sample goes to Billing at High with the default rules). Demo tickets
have `source: "demo"`, and nothing about them leaves CeDesk: no webhook deliveries, no
Slack alerts, no CSAT survey, and agent replies to them are not emailed. The demo
customers have `.invalid` addresses, which are never delivered to. The seed stores no
files. Demo ratings show `source: "portal"` (the rating's ticket is the demo one).
`cedesk seed-demo` still makes a whole demo workspace with members.
## 2026-11-13: Push notifications to the mobile app (Phase 3, P8)

Banners on agents' phones, through Firebase Cloud Messaging (it reaches iPhones too, with
the app's Apple key uploaded to Firebase).

- `POST /me/devices {"platform": "ios"|"android", "token"}` -> 201: after sign-in, with
  the app's FCM registration token; the phone is tied to this sign-in. The same token
  again refreshes it (under your current sign-in). A token registered to someone else
  who is still signed in on it -> 409; once their sign-in has ended it moves to you. Not
  with an API key (403). You keep your 10 newest phones.
- `GET /me/devices` -> `{"push_on", "devices": [{id, platform, token_hint, created_at,
  last_seen_at}]}`: your phones, never the token itself (`token_hint` is its last 6
  characters). `push_on: true` only when both Firebase settings are set.
- `DELETE /me/devices/{id}` -> 204. Someone else's device: 404. Ending a sign-in removes
  its phones anyway: sign-out, a password change (your other sign-ins), an Admin's
  password reset, deactivation; a phone whose sign-in expired gets nothing.
- Banners for four notifications: a ticket assigned to you ("New ticket for you"), a
  mention ("You were mentioned"), a customer reply ("The customer replied") and a
  Cevanos chat waiting for Accept ("A chat is waiting for you": new notification kind
  `handover`, to the assignee, else the team's Available agents; for such a chat the
  "assigned" one gets no banner, and none once it is accepted). The body
  is the ticket's number (`T-1042`), never its subject or a message; `data` has
  `ticket_id`, `notification_id` and `kind` so the app can open the ticket. Everything
  else stays in the bell.
- The worker sends them after the emails: claimed with SKIP LOCKED under a lease (other
  workers skip them), 10 at a time, at most 20 s a pass (the rest wait for the next),
  5 s a request. One older than 10 minutes is dropped (it is in the bell). A network
  error or a busy Firebase (429, 5xx) is retried a minute later, at most 5 tries. A
  token Firebase says is gone (UNREGISTERED, NOT_FOUND, 404) is deleted; INVALID_ARGUMENT
  keeps the device. A refused key stops sending for that pass (logged once) and the
  banners wait. A deactivated member gets none.
- Settings (worker): `CEDESK_PUSH_FCM_PROJECT_ID` and `CEDESK_PUSH_FCM_SERVICE_ACCOUNT`
  (the service account key's JSON). Both empty: push is off and nothing else changes.
  One without the other, or a key that can't be read, stops the worker with the reason.
  Tokens and the key are never logged; the database engine hides statement values from
  logs and errors (`hide_parameters`).

Migration 0051 (after 0050): `hd_push_device` (with the registering `session_id`), and on
`hd_notification` `push_handled_at`, `push_claimed_until`, `push_attempts` and kind
`handover`.

## 2026-11-13: Calendly app and the saved reply /book (Phase 3, PL7, W19)

- New app `calendly` in `GET /apps`: `card: "book"`, `settings_fields: ["link"]`,
  `credential: null` (a booking link is public, so nothing secret is kept).
- `PUT /apps/calendly {"settings": {"link": "https://calendly.com/acme/30min"}}`: only
  `https://calendly.com/<you>` or `https://calendly.com/<you>/<event>`; a credential or
  any other setting is a 422. Installing adds the saved reply `/book` ("Hi
  {{customer.first_name}}, you can pick a time that suits you here: <link>", `source:
  calendly`); when the workspace already has its own `/book`, Calendly's is `/book1`.
  Audited (area `apps`, with `saved_reply`; and area `saved_replies` whenever Calendly
  adds, changes or deletes the reply).
- Changing the link: if `/book` is still Calendly's text, or the Admin's text still has
  the old link, only the link is swapped for the new one. If the Admin edited it beyond
  that (the old link is gone), it is left unchanged and the answer's new field
  `AppOut.note` says "/book was edited by hand; update its link yourself." (also in the
  audit entry). `note` is null otherwise, and always null in `GET /apps`.
- If the Admin deleted `/book` by hand, saving Calendly's settings again adds it back
  (with the current link). Renaming it keeps it Calendly's (relinked under its new name).
- `DELETE /apps/calendly` deletes Calendly's reply only if its text is exactly what
  Calendly wrote (a rename alone doesn't count as an edit); an edited one is kept and
  becomes an ordinary reply (`source: manual`), with a note in the audit
  (`saved_reply_kept`). Never touches a `/book` of your own; the apps audit lists
  `saved_replies_removed`.
- `InstallIn.secret` is now optional in the schema: every app except Calendly still
  needs it (422 "Paste the ..." without it).
- `AppOut.credential` may now be null (Calendly takes no credential): clients that
  showed a credential field for every app must hide it when it is null.

Migration 0050 (after 0049).

## 2026-11-13: Archive tier for old resolved tickets (Phase 3, O3)

Resolved tickets older than the workspace's archive age (default 120 days) move out of
the ticket list to the archive once a day, so the ticket list, views, search and reports
stay fast. They are still found by number or customer and open read only.

- `GET /archive/tickets?number=1042` (or `T-1042`), `?email=`, `?contact_id=`: at least
  one, else 422. Newest first, at most 50. Same rights as reading tickets; a team-only role
  sees its team's only.
- `GET /archive/tickets/{id}`: read only, with everything that was on the ticket: every
  message (internal notes too; `email`: an email's headers), the files' names, sizes and
  types (`files`, and per message), the tags, fields, the CSAT rating, and its history
  (time logged, tasks, approvals, links, side conversations, Jira issues, emails sent and
  the Cevanos chat: in `related` since 2026-11-16, see "Archived ticket page" above).
  `team_name` and `assignee_name` are as
  they were when it was archived (the team or member may be gone). 404 when it isn't
  archived or is another team's. An archived ticket can't be changed, replied to or
  reopened; `GET /tickets/{id}` answers 404 for it.
- `GET /archive/settings`, `PUT /archive/settings {"after_days": 30-3650 | null}`
  (null: 120). Settings right, audited (area `archive`).
- The worker moves them in batches with `FOR UPDATE SKIP LOCKED`, so several workers never
  move a ticket twice. Each run is audited (area `archive`, action `archived`). An
  imported ticket counts from when it was imported, as for retention.
- A ticket a live one still needs waits, however old: one a ticket still in the list
  names as its parent (`parent_id`) or as what it was merged into (`merged_into_id`), one
  linked to a live ticket that isn't resolved, or one with an email still waiting to be
  sent. Children and merged tickets (named by nobody) move first; the ticket they named
  moves on a later run, once they are archived. So no live ticket ever loses its parent,
  merge target or open link.
- A customer's late email answer to an archived ticket (its `In-Reply-To`/`References`, or
  `[T-1234]` in the subject from that ticket's customer) becomes a new ticket, tagged
  `archived-follow-up`, with the line "Follows archived ticket T-1234". The same email
  delivered twice is still a duplicate.
- Retention deletes from the archive too, with the files, events (`hd_outbox`) and
  webhook deliveries, as for live tickets; its audit detail gets `archived` (how many came
  from the archive).
- Privacy: a customer's export lists their archived tickets (`"archived": true`; the
  export's `counts` include them), and erasing a customer blanks those too (messages and
  their email headers, emails sent, tasks, approvals, time notes), deletes their files and
  erases their events and webhook deliveries (erase `counts` get `archived_tickets`).
- Usage: `storage_bytes` counts archived tickets' files too (until erased or deleted).
- Imports: a ticket already imported and since archived is skipped, never imported again.

What leaves with a ticket and is not kept: its notifications and mentions (agents'
inboxes) and rating links (long expired). Reports count live tickets only, so periods
older than the archive age show fewer tickets and ratings than before.

Migrations 0049 (after 0048) and 0052 (after 0051): 0052 adds the archive's `related`,
`file_bytes`, `source_id` and `email_ids` and their indexes, for servers that already ran
0049 (tickets archived before it get `source_id` and `file_bytes` from what they kept,
and an empty history). Both downgrades refuse while any ticket is archived.

## 2026-11-13: Side conversations in Slack (Phase 3, X5, the Slack part)

A side conversation can now be held in a Slack channel (#warehouse) as well as by email.
The customer never sees it, and a reply in the thread comes back to the ticket.

- New app `slack` ("Slack side conversations") in `GET /apps`, with `card: "side"` and
  `second_credential: "signing_secret"`. `PUT /apps/slack`
  `{"secret": "xoxb-...", "signing_secret": "..."}`: the bot token is checked with Slack
  (`auth.test`) and both are kept encrypted together; only the token's last 4 characters
  are shown, and neither is ever audited. `InstallIn` has a new optional
  `signing_secret` (any other app with it: 422). The Slack app needs the bot scopes
  `chat:write`, `channels:history`, `groups:history`, `users:read`, the events
  `message.channels` and `message.groups`, and the Request URL below.
- `POST /tickets/{id}/side-conversations/slack` `{"channel": "#warehouse", "body"}` ->
  201: the bot posts the agent's message in the channel with the ticket's number and
  subject (never the customer's messages). 409 without the app; 422 when the channel
  doesn't exist or the bot isn't in it; 503 when Slack doesn't answer. Same rights and
  team scope as by email.
- `POST /side-conversations/{id}/messages` on a Slack one posts in the same thread.
- `POST /slack/events/{workspace_id}`: public, for Slack's Events API. Every request must
  carry Slack's signature made with this workspace's signing secret, at most 5 minutes
  old, or it is a 401 before anything is read. It answers Slack's URL check (challenge).
  A person's reply in a side conversation's thread is saved on it (once, even if Slack
  sends it again), a line goes on the ticket ("Ravi replied in the side conversation in
  Slack #warehouse") and the assignee gets a `side_reply` notification. A bot's message,
  an edit, a delete, or any other thread is ignored. A reply with a photo or file keeps
  its text plus a `[file: name]` line per file (the file isn't downloaded). Slack's
  markup is made readable: `<@U123>` -> `@name` (or `@U123`), `<https://x|label>` ->
  `label (https://x)`, `&lt; &gt; &amp;` unescaped. The reply, the ticket's line and the
  notification are saved first, together, under "Slack user U123"; the person's name is
  then asked of Slack for at most 1.5 s and, if Slack answers in time, replaces it in the
  reply, its mentions and the ticket's line (a slow or failed lookup is not an error).
  A malformed timestamp header (e.g. `²`) or an event after the app is uninstalled is
  a 401, nothing stored.
- Side conversations have a new field `via`: `email` or `slack`. For Slack, `to` is the
  channel and `from_address` is empty.
- An email can never attach itself to a Slack thread, whatever its reply headers say.

Migration 0048. Not yet tried against a real Slack workspace: the shapes follow Slack's
API docs.

## 2026-11-13: Imports: HubSpot and Zoho Desk (Phase 2, M1)

New `source` values for `POST /imports/connections`: all 7 help desks of md M1 can now be
connected (plus a CSV file). Everything else (dry run, run, team map, files, sync, history)
works as for the other sources.

- `hubspot`: `credentials: {"token": ...}`, a private app token with the scopes `tickets`,
  `crm.objects.contacts.read`, `crm.objects.owners.read`, `sales-email-read` and `files`.
  No settings. Groups are ticket pipelines. A stage marked closed -> resolved, a "waiting on
  contact" stage -> pending, any other -> open. Emails and notes on a ticket become messages,
  their files are copied. HubSpot has no knowledge base or saved replies in its API: the dry
  run says so in `warnings`.
- `zoho`: `settings: {"region": "us"|"eu"|"in"|"au"|"jp", "org_id": optional}`,
  `credentials: {"client_id", "client_secret", "refresh_token"}` from a Zoho Self Client with
  `Desk.tickets.READ`, `Desk.contacts.READ`, `Desk.basic.READ` and `Desk.articles.READ`.
  `org_id` is needed only when the account has several organisations (the error lists them).
  Groups are departments. Status by Zoho's status type: Open -> open, On Hold -> pending,
  Closed -> resolved. Threads and comments become messages; help center articles are
  imported; Zoho's email templates are not (the dry run says so).
- Both read only (GET; the one POST is the token exchange or a HubSpot search), and only
  from their own hosts. Credentials are never shown back.

Not yet run against a real HubSpot or Zoho account: field shapes follow their API docs.

## 2026-11-12: Fixes from the review of webhooks, apps and retention (PL3-PL7, C12, O2)

- Erasing a customer (S11) now also clears their webhook deliveries: the body becomes
  `{"erased": true}`, the receiver's saved answers go, and one not sent yet is stopped
  (`failed`, "Stopped: the customer was erased."). The erase's `counts` add
  `webhook_deliveries` and `webhook_responses`.
- The webhook log keeps 30 days: delivered and failed deliveries (and their tries) older
  than that are deleted by the worker. One still being tried stays.
- Every call a workspace can point (webhooks, imports and their files, Shopify, Stripe,
  Jira, Slack) now connects to the exact address it checked (public only), with the host
  name kept for HTTPS. A host whose address changes between the check and the connection
  (DNS rebinding) can no longer reach the server's own network.
- The Shopify orders card looks the customer up first (`customers/search.json`, the exact
  email only) and reads that customer's orders; an order whose email isn't the
  customer's is never shown. Shopify's order list can't be filtered by email, so before
  this the card could show the shop's latest orders, other customers' included.
- Retention counts an imported ticket from when it was imported (the latest run of its
  import), not from its old resolved date: importing years of history no longer has it
  deleted the next day. A deleted ticket's events and webhook deliveries go with it.

## 2026-11-12: Usage, cloud plan limits and retention (Phase 3, O2, O4, O5)

- New (Settings): `GET /usage` → `{plan, agents, tickets_this_month, storage_bytes,
  limits: {agents, tickets_month, storage_bytes}, retention_days}`. Agents are active
  members who take tickets (light agents are free seats); tickets are this calendar month
  (UTC), not imported; storage is the files on tickets.
- Cloud plan limits (O4): only on the cloud edition (`CEDESK_EDITION=cloud`) and only for
  a workspace given a plan with `cedesk set-plan --workspace <slug> --plan
  free|growth|enterprise|none` (audited, area `plan`). A self-hosted install, or a
  workspace with no plan, has no limits. Over a limit, these answer 402 with a clear
  message: adding a member who takes tickets (or reactivating one, or making a light agent
  an agent), uploading a file, and an agent or API key opening a ticket. A customer's own
  email, chat, portal or inbound-API request is never refused. Plans come from
  `CEDESK_PLANS` (JSON); empty = the md D6 proposal (Free: 3 agents, 1,000 tickets a
  month, 1 GB, 1 year; Growth: 50 GB, 3 years; Enterprise: none).
- New (Settings): `GET /retention`, `PUT /retention` `{closed_ticket_days}` (30 to 3650,
  or null to keep for good; audited, area `retention`) → `{closed_ticket_days,
  plan_history_days, effective_days, ...}`. The worker deletes resolved tickets older
  than `effective_days` (the setting, capped by the plan's history), with their messages,
  files (from storage too) and the rest, once a day; each run is audited as a server
  change. Open and Pending tickets are never deleted.
- Not in this change (md cut line): the archive tier (O3) and exporting before deleting.
- Migration 0047: `hd_workspace_plan`, `hd_retention` (forced RLS).

## 2026-11-12: Side conversations by email (Phase 3, X5)

- New: `POST /tickets/{id}/side-conversations` `{to: [emails], subject?, body}` → 201: an
  email to another team or a supplier (warehouse@, the courier), from the ticket's support
  address (the one the customer wrote to, else the default). Subject: the agent's, or
  "About <ticket subject>", with the ticket's label `[T-1042]`. Needs the right to reply
  and to change the ticket (team scope); light agents can't (403). Not to the ticket's
  customer nor to one of the desk's own addresses (422); 409 without a support address,
  or with 20 already. The worker sends it with the usual retries; if it can't, the ticket
  gets a line.
- New: `POST /side-conversations/{id}/messages` `{body}` → 201: writes again, to the same
  addresses, threaded under the earlier emails (In-Reply-To, References).
- New: `GET /tickets/{id}/side-conversations` (anyone who can read the ticket): each with
  `messages` (`direction` out or in, `body`, `author_name`, `from_address`,
  `created_at`).
- Their reply: email in recognises it by its reply headers before it looks for a ticket,
  saves it on the side conversation (once, however often it arrives), writes a line on
  the ticket ("warehouse@acme.com replied in a side conversation") and tells the
  assignee (notification kind `side_reply`, new). It is never a customer message: the
  portal and the customer's emails never show any of it.
- Slack side conversations (#warehouse) need a Slack bot to bring replies back; they
  are a follow-up (md X5's other half).
- Migration 0046: `hd_side_conversation`, `hd_side_message` (forced RLS); notification
  kind `side_reply`.

## 2026-11-12: Apps: Jira issues from tickets and Slack alerts (Phase 3, X10, W20, PL7)

- `GET /apps` now also lists `jira` and `slack_alerts`; install them with `PUT /apps/{app}`
  like the others (Settings, audited, credential sealed):
  - Jira: `settings: {site: "acme", email, project: "SUP"}`, `secret`: an Atlassian API
    token. Checked with Jira (the token works and sees the project) before it is saved.
  - Slack alerts: no settings, `secret`: the Slack Incoming Webhook URL
    (`https://hooks.slack.com/services/...`). Installing posts a test line to the channel.
- New: `GET /tickets/{id}/jira` (anyone who can read the ticket); `POST /tickets/{id}/jira`
  `{summary?, description?}` → 201 `{key, url, made_here, created_at, created_by_name}`:
  an issue of type Task in the app's project, summary = the ticket's subject unless given,
  description = what the agent writes plus a link back to the ticket (never the
  customer's messages unless the agent puts them in); `POST /tickets/{id}/jira/link
  {key}` links an issue that exists (checked with Jira); `DELETE /tickets/{id}/jira/{key}`
  takes it off the ticket (it stays in Jira). Needs the right to reply and to change the
  ticket (team scope); light agents can't. Each writes a system line ("Jira issue SUP-12
  created by Neha"). At most 10 per ticket. 409 when Jira isn't installed or the issue is
  already there; 422 when Jira refuses; 503 when it doesn't answer.
- Slack alerts (W20): when a ticket is created Urgent or High, or a change makes it Urgent
  or High, the worker posts one line: priority, number (linked to
  `<CEDESK_APP_URL>/tickets/<id>`), subject and team, escaped so a subject can't mention
  anyone or make a link. Sent after the change commits (so after the rules ran), retried
  by the outbox; a webhook Slack refuses fails at once and is listed in
  `GET /outbox/failed`.
- Frontend: the alert's link assumes the agent app opens a ticket at `/tickets/<id>`.
- Migration 0045: `hd_app_install.app` also takes `jira` and `slack_alerts`;
  `hd_jira_issue` (forced RLS).

## 2026-11-12: Apps: Shopify and Stripe, and the sidebar's orders and payments (Phase 3, PL7, C12)

- New (Settings): `GET /apps` lists the apps (`shopify`, `stripe`) with what each needs
  (`settings_fields`, `credential`, `credential_help`), whether it is installed, and
  `blocked` when it can't be (Cevanos connected). `PUT /apps/{app}` `{settings, secret}`
  installs it, or changes its settings and credential: the credential is checked with the
  provider first (422 when refused, 503 when it doesn't answer), then kept encrypted; only
  `secret_hint` (`…` and the last 4 characters) is shown again. Shopify: `settings: {shop:
  "acme"}` (a full `acme.myshopify.com` address works too) and an Admin API access token
  with `read_orders` and `read_customers`. Stripe: no settings, a restricted key (`rk_...`)
  with read access to Customers and Charges (an `sk_...` key works too). `DELETE
  /apps/{app}` uninstalls it and deletes the credential (404 when not installed). Audited:
  area `apps`, actions `installed`, `changed`, `uninstalled` (never the credential).
- New (anyone who can read the ticket): `GET /tickets/{id}/orders` and `GET
  /tickets/{id}/payments`: the ticket customer's latest 5 Shopify orders (`number`,
  `payment`, `fulfillment`, `total`, `currency`, `items`, `url` into the shop's admin) and
  Stripe charges (`amount`, `currency`, `status`, `refunded`, `url` into the dashboard),
  found by email, read live (10 s at most) and never stored. They never fail because of
  the provider: `{source, available, message}` says why there is nothing (no app, no
  customer email yet, the provider refused or didn't answer).
- With Cevanos connected (md D16), Shopify and Stripe can't be installed (409) and the
  cards answer `source: "cevanos", available: false`: Cevanos's integrations fill them
  once Cevanos serves them over `/v1` (with the Cevanos AI features).
- Calls go out GET only, through the egress guard (public addresses only, no redirects):
  `<shop>.myshopify.com` is built from the shop name, Stripe is `api.stripe.com`.
- Migration 0044: `hd_app_install` (forced RLS). Jira and Slack alerts come next;
  Calendly is on the cut line.

## 2026-11-12: Outgoing webhooks (Phase 3, PL3-PL6)

Admin only, signed in (not with an API key): a webhook sends ticket data out of CeDesk.

- New: `POST /webhooks` `{url, events, enabled?}` → 201 with `secret` (`whsec_...`, shown
  this once; kept encrypted, `secret_hint` afterwards). The URL must be https with a public
  host name (no IP address, no user or password, nothing that resolves to a private,
  loopback or link-local address: checked when saved and again before every send).
  `events`: any of `ticket.created`, `ticket.updated`, `ticket.resolved`,
  `message.created`. At most 20 per workspace (409). `GET /webhooks`;
  `PATCH /webhooks/{id}` `{url?, events?, enabled?}`; `POST /webhooks/{id}/secret` (a new
  secret, shown once; the old one stops at once); `DELETE /webhooks/{id}` (204; its queue
  and log go too). Audited: area `webhooks`, actions `created`, `changed`,
  `secret_changed`, `deleted` (never the secret).
- Each delivery is a POST of the event as JSON (PL5): `{event, ticket: {id, number,
  subject, status, priority, team, topic}, id, occurred_at}`; `ticket.updated` adds
  `changes` (the fields that changed); `message.created` adds `message: {id, kind,
  author_member_id}`, only for customer and agent replies (never notes), and never the
  text: read it with `GET /tickets/{id}`. This is how a system using the inbound API
  (`/inbound/tickets`) hears about agents' replies.
- Headers: `X-CeDesk-Event`, `X-CeDesk-Delivery` (the same on every retry: drop repeats),
  `X-CeDesk-Timestamp` (unix seconds), `X-CeDesk-Signature: sha256=<hex HMAC-SHA256(secret,
  "<timestamp>.<body>")>`. Receivers check the signature and that the timestamp is within 5
  minutes (the timestamp is signed, so an old delivery can't be replayed as new).
- A 2xx answer within 10 s is delivered; redirects are not followed. Otherwise it is tried
  again after 1 min, 5 min, 30 min, 2 h and 12 h; after the 6th try it has failed. Each URL
  has its own queue: one that is down never holds up the others. A webhook turned off
  stops getting new events; what was queued for it is marked failed, not sent.
- New: `POST /webhooks/{id}/test`: a signed `ping` (`{event: "ping", id, occurred_at,
  webhook: {id}}`) sent now, one try, the answer returned and logged.
- New (PL6): `GET /webhooks/deliveries?state&webhook_id&cursor&limit`, newest first, each
  with `body` (exactly what is sent), `state` (pending, delivered, failed), `tries`,
  `next_try_at`, the last status, time and error, and `attempts` (every try: when,
  status, ms, error, the first 300 characters of the answer). `POST
  /webhooks/deliveries/{id}/retry` sends a failed one again with a full set of tries (404
  for any other).
- The worker (`cedesk worker`) sends them: no new process or setting.
- Migration 0043: `hd_webhook`, `hd_webhook_delivery`, `hd_webhook_attempt` (forced RLS).
## 2026-11-12: Imports: keep syncing until go-live, import history, attachments (Phase 2, M7, M9, M10)

New (Admin only, Settings): the three things that were left of the imports.

**Keep syncing until go-live (M9).**
- `POST /imports/connections/{id}/run` takes `sync: true`. After the first import the run stays
  `done` with `sync_on: true` and the credentials are kept. Every 5 minutes the worker asks
  the source what changed since the last look (a little before it, so nothing is missed): a new
  ticket is imported; a ticket already imported gets only its new messages (matched by the
  source's message ids, so nothing is made twice) and the source's current status. Intercom,
  Zendesk, Freshdesk, Gorgias and Help Scout; 422 for a CSV file. A look the source can't
  answer (a revoked key) does not end the sync: `last_error` says why, the history shows it
  once, and it is tried again in an hour. 409 when starting a run on a connection that is
  syncing: stop the sync first.
- `POST /imports/runs/{id}/sync-now` -> 202: the next look is due at once. 409 when the run
  isn't syncing or its first import is still running.
- `POST /imports/runs/{id}/sync-stop` -> the run, with `sync_on: false`: go-live. Syncing stops
  and the stored credentials are deleted, so CeDesk keeps no access to the old help desk (while
  the first import is still running, only the sync is turned off and the credentials go when
  it ends). 409 when the run isn't syncing.
- `GET /imports/runs/{id}` and the run in the other answers have `last_sync_at`.

**Import history (M10).**
- `GET /imports/history?limit=&cursor=` -> `{runs, next_cursor}`, newest first. A run:
  `id`, `connection_id`, `source`, `account` (or the file's name), `result` (`running`,
  `finished`, `syncing` or `failed`), `started_by_id`, `started_by`, `started_at`,
  `finished_at`, `kinds`, `counts`, `sync_on`, `last_sync_at`, `last_error`.
- `GET /tickets` has `imported`: `any` for every imported ticket, or one source (`intercom`,
  `zendesk`, `freshdesk`, `gorgias`, `helpscout`, `csv`). 422 for anything else. Imported
  tickets also carry the tag `imported-<source>`.
- Finish, failure and sync stop are now in the audit log too (area `imports`), as well as
  connect and start.

**Attachments (M7).** A message's files are copied into storage and shown on the message
like any other attachment (scanned for viruses the same way). A file over 25 MB, or one the
source no longer serves, is kept as a link in a system line under its message ("Attachment
kept as a link (over 25 MB): ..."). At most 20 files are copied per message. A message that
is only files is kept (its text is "(attachment)"). `counts.tickets` has `files` and
`files_linked`; during a sync, `counts.sync` has `created`, `updated`, `new_messages`, `files`
and `files_linked`. Files are read from the source over https only, every address checked
(public only), a redirect followed at most 3 times and the account's key sent only to the
account's own host.

No migration: nothing in the database changes.

## 2026-11-12: API keys, the public REST API and the inbound API channel (Phase 3, PL1, PL2, C3)

Other systems call the same `/api/v1` routes as the app, with an API key instead of a
sign-in: `Authorization: Bearer cdk_...`.

- New (Admin, signed in; a key can't use these): `POST /api-keys` `{name, access}` → 201
  with `key`, shown this once (CeDesk keeps its SHA-256 and last 4 characters);
  `GET /api-keys` (live first, then revoked; `hint` `cdk_…abcd`, `created_by_name`,
  `last_used_at` to the minute, `revoked_at`); `DELETE /api-keys/{id}` → 204, the key answers
  401 from then on. Audited: area `api_keys`, actions `created` and `revoked` (name, level
  and hint; never the key).
- What a key does: it acts as the Admin who made it, within its `access`:
  - `read`: GET only.
  - `read_write`: also writes on tickets (messages, notes, tags, fields, status, assign,
    macros, ticket tools), `/contacts`, `/tasks`, `/time`, and `/inbound/tickets`.
  - `admin`: also writes on `/teams`, `/ticket-types`, `/saved-replies`, `/macros`,
    `/rules`, `/article-categories` and `/articles`.
  - Reads: those, plus `/reports`, `/members`, `/agents`, `/attachments`, `/files` and
    `GET /me` (who the key acts as).
  - Anything else answers 403 with the reason: sign-in, `/me/...` (password, 2FA,
    status), members, roles, security, SSO, Cevanos, email addresses, imports, privacy,
    audit, sandbox, the live stream, `/api-keys`. A key never approves.
  - System lines and the audit log name the key: `Priority set to High by Admin (API key
    "Shop sync")`.
  - The workspace's IP list (S4) applies to keys. A key stops (401) when revoked or when
    the member who made it is deactivated.
- Rate limit (PL2): 700 requests a minute per workspace, all its keys together (setting
  `CEDESK_API_KEY_LIMIT`); over it, 429 with `Retry-After`. Requests by people aren't
  counted.
- New (C3, a `read_write` or `admin` key): `POST /inbound/tickets` `{contact {email, name},
  message, subject?, priority?, type_id?, team_id?, fields?, tags?, external_id?}` → 201
  `{ticket_id, number, created}`. A ticket on the `api` channel made as the customer:
  card numbers removed, triage, rules, SLA and routing run, line "Ticket created by Priya
  (api)". The subject defaults to the first 60 characters of the message. The same
  `external_id` again → 200 with the same ticket, `created: false`. No assignee: routing
  picks.
- New (C3): `POST /inbound/tickets/{id}/messages` `{message, external_id?}` → 201
  `{message_id, outcome}`: the customer's reply (reopens a resolved ticket; a thank-you
  keeps it resolved; reply rules run). The same `external_id` again → 200, `outcome:
  duplicate`. 409 on a ticket that didn't come in through the API, or was merged (the
  message names the ticket it went into). 404 for another workspace's ticket.
- Both inbound routes answer 403 to a person signed in: agents reply or add notes.
  Agents' replies on `api` tickets aren't sent anywhere by CeDesk; the system reads them
  with `GET /tickets/{id}` (webhooks: PL3).
- Migration 0042: `hd_api_key` (forced RLS).

## 2026-11-12: Fix: notifications failed after live updates (C11, P7)

- Since live updates (migration 0040), any change that notifies an agent (auto-assignment,
  @mention, customer reply, task alerts) answered 500 and was not saved. Two causes in the
  `notification` stream event: the ticket id was sent as two types in one query, and the
  event name `notification` broke the event table's name rule (it asked for a dot).
- Fixed in the query and by migration 0041, which lets an event name be one word. No API
  change; the stream's `notification` event now arrives as documented.


## 2026-11-12: Reports: more metrics, Governance link, article votes, CSV (Phase 3, R3-R5, R7, R8)

Who: Supervisors, Admins and custom roles with `reports` (as the team report). A
`team_only` role sees its own team in the ticket reports (another team is 404) and every
article in the article report. Every report takes `from` and `to` (default: the 30 days up
to now; with a time zone; at most 366 days; 422 otherwise).

- New: `GET /reports/metrics?from&to&team_id` (R3, R4, R5):
  - `summary`: `open` (the backlog now); `one_touch`, `one_touch_measured`,
    `one_touch_percent` (resolved in the period with exactly one reply from a team member
    and never reopened; chat history copied in from Cevanos is not a reply);
    `reopened`, `reopen_measured`, `reopen_rate_percent` (created in the period and resolved
    at least once: the share reopened); `time_logged_minutes`, `billable_minutes` (logged in
    the period, by anyone). Imported tickets are left out of one-touch and reopen; merged
    tickets are left out everywhere. A `%` is `null` when there is nothing to measure.
  - `backlog_by_age`: Open or Pending now, `under_1h`, `1h_4h`, `4h_24h`, `over_1d` (each
    bucket from its lower bound, inclusive, to its upper bound).
  - `channels`, `topics`, `sentiment`: `{value, label, created, open}` per bar; every
    channel and keyword topic is listed (zeros included, fixed order); `value: null` with
    label "Not set" is last when some tickets have none.
  - `agents`: per active member who takes tickets (light agents not listed; with `team_id`,
    that team's members): `logged_minutes`, `billable_minutes`, `open_tasks`,
    `overdue_tasks` (now).
  - `governance_url`: the Governance report in Cevanos, only when this workspace has
    connected Cevanos; else `null`. CeDesk only links to it (R5). AI numbers (AI7) come
    with the Cevanos AI features.
- New: `GET /reports/articles?from&to` (R8): votes cast or changed in the period (a visitor
  has one vote per article; their latest answer counts): `votes`, `helpful`,
  `not_helpful`, `helpful_percent`, `articles_voted`, and `articles` (most votes first, at
  most 500; `truncated` when more had votes): `{article_id, title, category, status,
  votes, helpful, not_helpful, helpful_percent}`.
- New on all three reports (`/reports/team`, `/reports/metrics`, `/reports/articles`):
  `?format=csv` (R7) answers the same report as a CSV attachment
  (`cedesk-<report>-report-<from>-to-<to>.csv`, UTF-8 with a byte order mark): the name and
  period at the top, then one section per table (title row, header row, blank row
  between). Text that a spreadsheet would run as a formula (starting with `=`, `+`, `-`,
  `@`) is written with a `'` in front. Any other `format` is 422.
- Server setting: `CEDESK_CEVANOS_APP_URL` (default `https://cevanos.ai`), the Cevanos web
  app the Governance link points into; https outside development.

## 2026-11-12: Live updates: a stream of changes for agents and portal customers (Phase 2, C11, P7)

New (agents): `POST /stream/ticket` -> 201 `{ticket, expires_in: 60}`, then `GET /stream?ticket=...`
opens a Server-Sent Events stream (`text/event-stream`). A browser's EventSource can't send an
Authorization header, so the ticket stands in for it: it is random, lasts a minute and works once,
so the session token never goes in an address. A client that can send headers may use it the same
way. 401 when the ticket is unknown, used, expired or its sign-in ended; 429 with `Retry-After`
when the person already has 5 streams open.

New (portal customers): `POST /portal/stream/ticket` (portal token) and `GET /portal/stream?ticket=...`.

What a stream sends is that something changed, never what it says: an event carries ids and the
names of what changed, never a subject, a message text, an address or a name. Fetch the ticket
through the normal API; it applies the same permissions as always.

- Frame: `id: <n>`, `event: <kind>`, `data: <json>`. Send the last `id` back as the `Last-Event-ID`
  header (an EventSource does it by itself on reconnect) or as `?last_event_id=` to carry on
  without losing one. An id may repeat after a reconnect: ignore one you have seen. A first
  line `retry: 3000` tells the browser how long to wait before it reconnects.
- `event: reset`: your last id is older than the 24 hours kept (or isn't one): refetch what you
  show, then go on. `event: end` with `{"reason": "signed_out"}`: the sign-in ended; don't
  reconnect. A line starting with `:` is a heartbeat, every 15 seconds.
- A role limited to one team's tickets only gets that team's events (and their own
  notifications); a role change applies within a minute. A stream looks for new events about
  once a second.

Agent kinds (`data` has `ticket_id`, and `number` where the ticket's is known):

| `event` | `data` besides the ticket |
|---|---|
| `ticket.created` | |
| `ticket.updated` | `changes`: the fields that changed (`status`, `priority`, `team_id`, ...) |
| `ticket.resolved` | |
| `message.created` | `message_id`, `message_kind` (customer, agent, ai, note, system) |
| `assignment.changed` | `from`, `to`, `by`: member ids, or null |
| `chat.accepted` | `by`: an agent clicked Accept on a Cevanos chat |
| `chat.taken_over`, `chat.closed`, `chat.reopened` | the state of a Cevanos chat (P7) |
| `notification` | `notification`: its kind (assigned, mention, customer_reply, ...) |

Portal customers only get, for their own tickets: `message.created` for a customer, agent or AI
message (never a note or a system line), `ticket.updated` with `changes: ["status"]`, and
`ticket.resolved`.

- Not sent to anyone: `security.alert` (the email to the Admins) and contact changes.
- Events are kept 24 hours; the worker removes older ones. New migration 0040 (`hd_event_log`,
  `hd_stream_ticket`, both with row-level security).
- Deploy: Caddy sends `/api/v1/stream` and `/api/v1/portal/stream` unbuffered and uncompressed;
  CORS allows the `Last-Event-ID` header.


## 2026-11-12: Audit log, privacy export and erase, sandbox (Phase 3, S9-S11)

**Audit log (S9)**: every settings change is recorded with who, when, from which IP
and what changed. Needs the `settings` right.
- `GET /audit?area=&before=&limit=`: newest first, `{entries, next_before, areas}`. Each
  entry: `{seq, at, actor_id, actor_name, ip, area, action, target, target_id, detail}`.
  `detail.changes` is `{field: [before, after]}`; secrets (passwords, keys, the SSO
  certificate, webhook secret) show `"(hidden)"`; long values are cut to 300 characters.
  Areas: members, roles, security, sso, two_factor, teams, ticket_types, routing, sla,
  rules, macros, saved_replies, email (addresses and signatures), portal, help_center,
  triage, cevanos, sandbox, privacy, imports (connect, start, close). Unknown `area`: 422.
- `POST /audit/verify` → `{ok, checked, broken_at, message}`: entries are hash-chained;
  an edited or removed entry shows as `broken_at`.
- `GET /audit/export.csv?area=`: CSV, oldest first (seq, when (UTC), who, ip, area,
  action, what, changes). Cells that would start a spreadsheet formula get a `'`.
- Changes made from the server's command line (`clear-ip-allowlist`) show as "Server
  (command line)" with no IP. Entries can't be changed by the app.

**Privacy (S11)**: Admins only (`manage_access`). Audited by contact id (never the
address).
- `POST /privacy/export` `{contact_id}` or `{email}` → 201 `PrivacyRequest`. The file:
  the customer, every ticket with every message (internal notes too, kind `note`), file
  names and ratings. `GET /privacy/requests/{id}/file` downloads it (JSON, signed in)
  for 7 days; then 409.
- `POST /privacy/erase` `{contact_id, confirm_email}` → 201 `PrivacyRequest` with
  `counts`. Name and address become "Erased customer" / `erased-<id>@erased.invalid`;
  every message, subject and field on their tickets, rating comments, task, approval and
  notification texts, time notes and queued emails become `(erased)` (queued emails are
  stopped); files and their exports are deleted from storage (download: 409 "This file
  was erased ..."); portal sign-ins end; events not sent yet are stopped; the import
  link is removed, so a later import makes a new contact. Ticket counts, reply times,
  scores and time logged stay. `confirm_email` doesn't match: 422; already
  erased: 409. If they write again they become a new contact. A chat kept in Cevanos must
  be erased in Cevanos.
- `GET /privacy/requests?limit=` → newest first: `{id, kind, contact_id, requested_by,
  requested_by_name, requested_at, customer_erased, file_ready, size, expires_at, counts}`.
- Files: new `scan_status` value `erased` (blocked like `infected`).

**Sandbox (S10)**: the `settings` right. One sandbox per workspace.
- `POST /sandbox` copies the live rules, macros and saved replies (409 if one exists);
  `GET /sandbox` (404 if none) → `{rules, macros, saved_replies, created_at, updated_at,
  updated_by_name}`; `DELETE /sandbox`.
- `PUT /sandbox/{rules|macros|saved_replies}` `{items}`: the whole section in order,
  checked as on the live pages (422 "Item 2: ..."). Keep `id` to change a live item,
  leave it out to add, drop an item to remove it. Nothing live changes.
- `GET /sandbox/compare` → per section `{added, changed: [{id, label, changes}], removed,
  order_changed}` and `same`.
- `POST /sandbox/try` → the live and the sandbox rules run on 5 sample new tickets, side
  by side: `[{sample, text, channel, live, sandbox, same}]`, each result `{lines, type,
  team, priority, status, tags}`. Nothing is saved.
- `POST /sandbox/deploy`: all or nothing; each added, changed, removed or reordered item
  gets its own audit entry, then "Sandbox deployed". Returns what changed; the sandbox
  becomes a fresh copy. 422 when there's nothing to deploy or an item no longer fits
  ("Nothing was deployed. Macros: ..."); 409 when two names would clash while saving.
## 2026-11-12: Imports from Intercom, Zendesk, Freshdesk, Gorgias and Help Scout (Phase 2, M1-M8)

No new route: `POST /imports/connections` now accepts these five sources, and the rest of the
flow (discover, dry run, run, close) is as for a CSV file. Read-only: nothing is ever written
back to the source. HubSpot and Zoho Desk are not offered (Phase 3).

What `POST /imports/connections` `{source, settings, credentials}` needs per source:

| source | `settings` | `credentials` |
|---|---|---|
| `intercom` | `region`: `us` (default), `eu` or `au` | `token`: an access token |
| `zendesk` | `subdomain`: `acme` | `email`, `token`: an API token |
| `freshdesk` | `domain`: `acme`; `include_archived`: `"true"` to import archived tickets (see below) | `api_key` |
| `gorgias` | `domain`: `acme` | `email`, `api_key` |
| `helpscout` | none | `app_id`, `app_secret` (OAuth app; the token is fetched by the server); optional `docs_key` for help articles |

- The account name is the part before the vendor's domain. `acme`, `acme.zendesk.com` and a
  pasted `https://acme.zendesk.com/agent` are all read as `acme`; anything that isn't a plain
  name (a dot, `@`, `#`, a port, an IP address, another domain) is a 422. The server only ever
  calls the vendor's own domain, over https, never follows a redirect, and refuses a host that
  resolves to a private, loopback or link-local address.
- 422 when the credentials are missing or the source refuses them, naming what's missing; 503
  when the source can't be reached.
- `discover` and `dry-run` return `warnings` that matter before running, for example: Freshdesk
  archived tickets, Help Scout without a `docs_key` ("Add a Docs API key to import help
  articles."), spam conversations that are skipped, and counts that are estimates.
- What each source brings: contacts, tickets with every message (a private or internal note
  is a note), tags, custom fields, the group or team (for `team_map`), the assignee (matched
  by email) and the customer rating where the source has one. Help articles and saved replies
  where the source has them (Intercom has no saved replies; Gorgias articles aren't imported).
- Status: open and active are open; pending, on hold and snoozed are pending; solved and
  closed are resolved. A status the source has that CeDesk doesn't is imported as open.
- Freshdesk: closed tickets are archived after 120 days and Freshdesk has no way to list them,
  so by default they are **not** imported. With `settings.include_archived = "true"` every
  ticket number is tried, one request each: slow, and it uses the account's Freshdesk API
  quota. Zendesk's incremental export is used for tickets.
- Rate limits: a 429 pauses the run for the source's Retry-After and goes on from the same page.
- Not in this change: attachments (files on source messages are not copied yet), and keeping a
  source in sync until go-live.


## 2026-11-12: Imports from other help desks: framework and CSV (Phase 2, M1-M8)

All routes are Admin only (Settings). The first source is a CSV file; Intercom, Zendesk,
Freshdesk, Gorgias and Help Scout follow on the same routes (`connect` answers 422 "isn't
available yet" until then; HubSpot and Zoho Desk come in Phase 3 and aren't offered). The flow is connect (or upload) → discover → dry run → run.

- New (Admin): `POST /imports/csv?filename=` with the CSV file as the request body (not a
  form). 201 `{id, source, label, state, credentials_hint, created_at}`; 422 for a file that
  isn't UTF-8 or lacks a required column; 413 over 25 MB. One row per message, grouped by
  `ticket_id`. Required: `ticket_id`, `subject`, `requester_email`, `message_from`
  (customer, agent, note), `message_body`, `message_created_at` (ISO 8601). Optional: `status`,
  `priority`, `channel`, `team`, `tags` (`;`), `ticket_created_at`, `resolved_at`,
  `requester_name`, `assignee_email`, `csat_score`, `csat_comment`, `message_id`,
  `author_name`, `author_email`, `field.<key>`. A row that can't be read is listed in
  `warnings` and left out. The file is deleted when the run ends.
- New (Admin): `POST /imports/connections` `{source, settings, credentials}`: checks the
  credentials with the source and keeps them encrypted (only the last 4 characters are
  ever shown). 201; 422 when the source refuses them; 503 when it can't be reached.
- New (Admin): `GET /imports/connections/{id}/discover` → counts, the source's groups with
  the team each probably maps to (by name, only a team that exists), the status map, and
  `warnings`. 409 when the connection was closed.
- New (Admin): `POST /imports/connections/{id}/dry-run` `{kinds?, team_map?}` → what a run
  would add (`will_create`), what this source brought before (`already_imported`) and
  `warnings` such as "No team picked for 'Shipping'.". Saves nothing. 422 for an unknown
  kind or team.
- New (Admin): `POST /imports/connections/{id}/run` `{kinds?, team_map?}` → 202 with the run
  (`state` queued → running → done or failed). The worker does it in the background, in
  slices, resuming at the page it was on after a crash or a pause (a source's 429 pauses
  it for Retry-After). 409 when this connection already has a run in progress. Start a
  failed run again: records already imported are skipped.
- New (Admin): `GET /imports/runs/{id}` → `{state, kinds, counts, sync_on, last_error, ...}`;
  `counts` per kind has `created` and `skipped` (tickets also `unknown_agent_messages`;
  a CSV also `rows_left_out`).
- New (Admin): `POST /imports/connections/{id}/close` (204): deletes the stored credentials
  or file now. The history stays. 409 while a run is in progress.
- What an imported ticket is: its own dates, status, tags, custom fields and CSAT; source
  `csv` (etc.) and `source_id` so running again makes no duplicates; the tag
  `imported-<source>`; a first system line "Imported from … with full history · group ·
  source ID … · was …". It is not routed, no rule runs, and it sends no notification,
  outbox event or webhook. Triage runs and card numbers are removed from customer text.
  Agents are matched by email; one who isn't in CeDesk keeps their name on their messages
  ("Sana K. (CSV file, not in CeDesk)") and the ticket stays unassigned. Imported tickets
  stay out of the report's time metrics; their CSAT counts. Ticket numbers are new
  (T-numbers); the old id is `source_id`.
- Changed: `hd_csat.source` (see `csat` on a ticket) can now be an import source.
- `Idempotency-Key` is accepted on `POST /imports/csv`, `POST /imports/connections` and
  `POST /imports/connections/{id}/run`: a repeat returns what the first made (header
  `Idempotent-Replayed: true`), the same key with a different request is 422. Without a key,
  a repeated connect makes a second connection, and a run can't be started twice on one
  connection (409).
- Imported tickets are never alerted for an SLA breach (their times come from the old desk,
  as in the team report). This shipped with the foundation (migration 0038).

## 2026-11-12: Custom roles, security settings, 2FA and SAML single sign-on (Phase 3, A3-A5, S1-S6, S8)

New error codes (all 403, `error.code`): `IpNotAllowed`, `PasswordExpired`,
`TwoFactorRequired`, `TwoFactorSetupRequired`, `SsoRequired`. Members, roles and every
security setting below are for Admins only (new right `manage_access`); a custom role
with the settings right never gets them.

**Custom roles (A3-A5)**
- `GET/POST /roles`, `PUT/DELETE /roles/{id}`: `{name, reply, assign_any, reports,
  settings, approve, scope, team_id}`. Every custom role reads tickets, adds notes, edits
  fields and lists members; `reply` adds replying, creating tickets and taking tickets
  (routing and assigning skip a role without it); `assign_any` assigns to anyone.
  `scope`: `all`, `team_edit` (edit the team's tickets, view the rest) or `team_only`
  (other teams' tickets are hidden). A scoped role needs `team_id`. Name taken: 409. A
  role in use can't be deleted (409), nor a team a role works on.
- Give one: `PATCH /members/{id}` (or `POST /members`) `{role: "custom", role_id}`.
  Members now have `role_id`, `role_name` and `two_factor`. Changes apply on the next
  request, no new sign-in.
- `GET /me` has `access`: `{role, role_name, actions, scope, team_id, team_name}`.
- A scoped role is never routed or assigned another team's tickets (422 "Ravi's role
  works on Shipping's tickets only ..."), and opens tickets for its own team only (its
  team is used when none is sent; 422 for another). A child ticket can go to any team.
- `team_only`: other teams' tickets are 404 everywhere (ticket, messages, files, tools,
  Related card) and missing from lists, tab counts and the customer's tickets; the team
  report shows its own team only. `team_edit`: other tickets can be read and noted, every
  change is 403 with the reason.
- Ticket detail has `can_edit` and `view_only_reason` ("Your role Billing desk can edit
  Billing tickets only.", or "... can read tickets and add notes only." for light agents).

**Security settings (S3-S6)**: `GET/PUT /security-settings` (only the fields sent change;
the rest keep their saved values)
- `password_min` 8-64 (default 10): new passwords only. `password_expiry_days` null, 90
  or 180: sign-in then has `password_expired: true` and only `GET /me`,
  `POST /me/password` and sign-out work (403 `PasswordExpired`).
- `session_length`: `1h_idle`, `8h`, `12h` (default) or `7d`. A shorter setting also ends
  longer open sessions. `1h_idle` ends a session after an hour without use (at most 7 days).
- `ip_allowlist`: CIDR blocks or addresses (up to 100), checked at sign-in and on every
  signed-in request (403 `IpNotAllowed`). Saving a list without your own address is 422.
  `POST /security/test-my-ip` `{ip_allowlist?}` → `{ip, allowed}`.
- `attachments_need_sign_in`: download links become `GET /attachments/{id}/content` (and
  `GET /portal/attachments/{id}/content`), which need the Bearer token; the link answers
  have `needs_sign_in: true`. These content routes always work for signed-in callers.
- Locked out by the IP list (every Admin)? On the server: `uv run cedesk
  clear-ip-allowlist --workspace <slug>` (the Admins are emailed).
- `admin_alerts` (default on): every Admin is emailed when a member is added, deactivated
  or reactivated, a member's role changes, the IP list, 2FA rule or SSO setup changes, a member's 2FA is reset, or
  the alerts are turned off (outbox event `security.alert`, sent by the worker).
- Score (S8): `GET /security/score` → `{score, level, items}`: SSO enforced 20, 2FA
  required 25, IP list 15, passwords 12+ characters 10, card redaction 10, and 5 each for
  sign-out not 7 days, files need sign-in, Admin alerts, portal email domains. Level
  healthy 80+, recommended 50-79, critical under 50; each item healthy / critical (big
  items off) / recommended.

**Two-step sign-in (S2)**, authenticator app (TOTP) with 10 recovery codes
- Set up: `POST /me/2fa/setup` → `{secret, otpauth_uri}` (show the QR); `POST
  /me/2fa/confirm` `{code}` → `{recovery_codes}` (shown once; your other sessions end).
  `GET /me/2fa` → `{enabled, required, recovery_codes_left}`. `POST
  /me/2fa/recovery-codes` `{code}` → a new set. `POST /me/2fa/disable` `{code}` (403 when
  the workspace requires it).
- Sign-in with 2FA on: the answer has `two_factor_required: true` and a token that only
  works for `POST /auth/2fa` `{code}` (6 digits or a recovery code) for 10 minutes;
  everything else is 403 `TwoFactorRequired`. Wrong codes count toward the 10-try lockout;
  a code works once.
- `require_2fa` in the security settings (turn it on for yourself first): members without
  2FA can only set it up (403 `TwoFactorSetupRequired`); `GET /me` has
  `two_factor_setup_required`.
- Admin: `POST /members/{id}/2fa/reset` → 204 (turns it off, signs them out everywhere).

**SAML single sign-on (S1)**
- Admin: `GET/PUT /sso-settings` `{enabled, enforced, idp_entity_id, idp_sso_url,
  idp_cert}`; the answer also has `sp_entity_id`, `acs_url`, `metadata_url` to give the
  identity provider. `enforced`: password sign-in is 403 `SsoRequired` for everyone except
  Admins.
- Sign-in page: `GET /sso/{workspace}` → `{enabled, enforced, start_url}` (no sign-in).
  Open `start_url` (optionally `?next=/tickets/...`) in the browser. After the identity
  provider, the browser lands on `<app_url>/sso/callback#token=...&expires_at=...&next=...`
  or `#error=...`. Members must already exist (matched by email). The start sets a
  short cookie (`cedesk_sso`, only for the ACS URL); an answer posted from a browser
  without it is refused, so nobody can sign you into their account.
- `GET /sso/{workspace}/metadata`: our SP metadata XML.
- New setting `CEDESK_API_URL`: the API's public address for the ACS URL.

## 2026-11-12: Ticket tools: merge, links, child tickets, approvals, tasks, time (Phase 3, X1-X4, X6, X8, X9)

Light agents read all of these but change none (403). Every change writes a grey line.

- Merge (X1): `POST /tickets/{id}/merge` `{into}` → the ticket merged into. The customer,
  agent, AI and note messages are copied into it in time order (their files move with
  them), and so do its open tasks and waiting approvals (line "Moved from T-1004: 2 open
  tasks, 1 waiting approval"); the merged ticket is resolved with `merged_into_id` set and a line "Merged into
  T-1005 by Neha". No CSAT and no events come from it. 422 into itself; 409 when the
  target is resolved or merged, the ticket was already merged, it is a live Cevanos chat
  (resolve it first), or it has open child tickets.
- Related card: `GET /tickets/{id}/related` → `{parent, merged_into, children, links,
  merged_from}`, each ticket as `{id, label, subject, status, assignee_name, team_name}`
  (lists newest first, up to 50).
- Links (X2): `POST /tickets/{id}/links` `{ticket_id}` → 201 the Related card (both ways;
  linking again changes nothing). `DELETE /tickets/{id}/links/{other_id}` → 204 (404 if
  not linked).
- Child tickets (X3): `POST /tickets/{id}/children` `{team_id, subject?, note?,
  assignee_id?}` → 201 the new ticket (takes `Idempotency-Key`). It copies the customer,
  type and fields; channel `email` if the parent's is email, otherwise `portal`;
  `parent_id` is set; the note (default "Opened from T-1001: …") is its first message, a
  private note. Rules and routing run as on any new ticket, so a rule can still move it to
  another team. The parent gets "Child ticket T-1012 created for Shipping by Neha".
- X4: resolving a ticket with an open child is 409 ("Resolve the child tickets first:
  T-1012 still open."), from Resolve, the status field and macros (a macro then runs no
  step). A rule that would resolve it leaves the status as it was (its other steps run)
  and writes "Status not changed: …". Resolving or merging a child writes "Child ticket T-1012 resolved by Chetan" on the parent
  and notifies the parent's assignee (new notification kind `child_resolved`).
- Approvals (X6): `POST /tickets/{id}/approvals` `{approver_id, what?, amount?,
  currency?}` → 201. `what` defaults to "Refund of ₹1,500 for order #1042" from the amount
  and the ticket's `order_id` field (422 when there is neither). The approver must be able
  to approve (Supervisor or Admin), active, and not you (422). They get a notification
  (kind `approval`). `POST /approvals/{id}/decide` `{decision: approve|reject, note?}`:
  the named approver or an Admin, never the requester (403); a decided request is 409.
  The requester is notified. `GET /tickets/{id}/approvals` (newest first),
  `GET /approvals/waiting` (mine to decide, oldest first). A Cevanos Governance hold (X7)
  is not passed on yet: it needs a Cevanos `/v1` route.
- Tasks (X8): `POST /tickets/{id}/tasks` `{text, owner_id?, due_in?: 1h|4h|tomorrow,
  due_at?}` → 201 (owner default: you; not a light agent; "tomorrow" is 9:00 next day in
  the SLA calendar's time zone; `due_at` needs a time zone). The owner is notified (kind
  `task`). `PATCH /tasks/{id}` `{text?, owner_id?, due_in?, due_at?, done?}` by the owner,
  the person who added it, or a Supervisor/Admin. Tasks carry `overdue`. `GET
  /tickets/{id}/tasks` (open first), `GET /tasks/mine` (my open tasks, due soonest first).
  The worker tells the owner once when a task passes its due time (new notification kind
  `task_overdue`, "Task overdue on T-1001: …"); a new due time missed again alerts again;
  a due time set in the past by hand is never alerted.
- Time log (X9): `POST /tickets/{id}/time` `{minutes 1-1440, note?, billable}` → 201.
  `GET /tickets/{id}/time` → `{total_minutes, billable_minutes, entries}`. `DELETE
  /time/{id}`: your own; an Admin can remove any.
- `hd_notification.kind` gains `approval`, `task`, `task_overdue` and `child_resolved`.

## 2026-11-12: Macros change the type; rules on the SLA (Phase 2, W14, W15, W18)

- Macros: new step `{"kind": "set_type", "type_id": "...", "only_if_empty": false}`, as in
  rules. Fields the new type lacks are cleared, each with a line. Needs the right to change
  fields (not light agents). A deleted type shows in `problems`; saving one is a 422.
- Rules: new event `sla_breached`: runs when an open ticket's SLA clock runs out (the
  worker, once a minute, once per clock), before the breach alert, so the alert goes to
  whoever has the ticket after the rules (a rule that moves the team re-routes it).
- Rules: new condition `{"kind": "sla_in", "states": [...]}` (on_track, close, breached,
  met): the clock the SLA pill shows. 422 on `created`.

## 2026-11-11: SLA editors, breach alerts, checking support addresses (Phase 2, W10-W12, W14, C10)

- New (Admin): `PUT /sla/policies` `{policies: [{name, enabled, vip_only, channels,
  priorities, first_reply_minutes, resolve_minutes}] | null}`: the whole ordered list
  (first enabled match wins). The last one must be on with no conditions; no other may
  match every ticket; names once; channels from email, web, portal, api, chat, phone,
  whatsapp, slack. `null`: back to the three built-in policies. 422 with a message.
- New (Admin): `PUT /sla/calendar` `{timezone, days, opens, closes, always_open}`.
- New (Admin): `POST /holidays` `{day, name}` (201; 409 when the day has one),
  `PATCH /holidays/{id}`, `DELETE /holidays/{id}` (204).
- Changed: `GET /sla` holidays now carry `id`. Every settings change above (and targets)
  works out each open ticket's SLA again at once.
- New: SLA breach alerts. Once a minute the worker notifies the assignee and the
  Supervisors of the ticket's team when a ticket's SLA clock runs out: notification
  `kind: "sla_breach"`, text "SLA breached on T-1001 (first reply overdue): <subject>".
  Once per clock (first reply, then resolution). Only when time runs out: a change that
  puts a due time in the past at once (shorter targets, new hours, a reopened old
  ticket) shows as breached but sends no alert. Tickets already overdue at upgrade are
  not alerted.
- New (Admin): `POST /email-addresses/{id}/forwarding-check` (202; 409 under a minute
  after the last): emails the address a one-time code from itself. When it comes back in,
  `forwarding_verified` turns true; it never makes a ticket. The inbound webhook answers
  `outcome: "verification"` for it.
- New (Admin): `POST /email-addresses/{id}/dns-check`: SPF (following include/redirect;
  checks the mail service's name when the server sets `CEDESK_MAIL_SPF_INCLUDE`), DKIM
  (at `dkim_selector`) and DMARC, each `{status: ok|missing|wrong|not_checked|error,
  record, note}`. At most about 15 seconds.
- Changed: email addresses carry `forwarding_verified`, `forwarding_verified_at`,
  `forwarding_check_sent_at`, `forwarding_check_email` (pending | sent | failed | null),
  `forwarding_check_error`, `forward_to`, `dkim_selector` and `dns` (the last check).
  `PATCH /email-addresses/{id}` takes `dkim_selector` ("" clears).

## 2026-11-11: Triage, card redaction and the team report (Phase 2, I1-I5, R1, R2, R6, W6)

- Changed: every new ticket and every customer reply is triaged by keywords (no AI):
  `topic` ("Refund / return", "Billing", "Delivery", "App problem", "General question"),
  `sentiment` (negative / neutral / positive) and `language` ("hi", "en"; null when it
  can't tell). These were null before unless Cevanos sent them. On a reply, topic and
  language read the customer's first message and their 20 newest; the mood is the newest
  message's, except that a mood Cevanos's AI sent (`conversation.classified`) is kept. Triage
  runs before the rules, so the default "Upset customer goes first" rule now works on
  every channel.
- Changed: card numbers (13-19 digits, groups split by one space or dash, Luhn) are removed
  before saving from customer messages, the subject, the stored email subject, text
  fields filled in on a new ticket, Cevanos's AI chat lines and handover reason, and CSAT
  comments:
  "[card number removed]", with the system line "1 card number removed from the
  customer's message". It can't be undone. Agent replies and notes are not touched.
- Changed: portal and web form subjects are made from the message after redaction.
- Changed: routing puts Hindi tickets (`language: "hi"`) first with agents who have the
  skill `hindi` (`PATCH /agents/{id}/routing {"skills": ["hindi"]}`), after the ticket
  type's skill. The line shows "skill hindi".
- New (Admin): `GET/PUT /triage-settings` `{topics: [{name, keywords}] | null,
  redact_cards}`. Each of the four topics once; `null` goes back to the built-in lists.
  `keywords_are_default`, `general_topic`, `updated_by_name` in the answer.
- New (Supervisor, Admin): `GET /reports/team?from&to&team_id` (default the last 30 days;
  times with a time zone; at most 366 days). `summary`: open and open_breaching (now),
  created, resolved, avg_first_response_minutes, avg_resolution_minutes,
  first_reply_sla_met_percent (+ `first_reply_sla_measured`), csat {ratings, average,
  happy_percent (4-5), by_score 5..1}. `teams[]` (with a "No team" row) and `agents[]`
  (active members except light agents: role, status now, open, resolved, average first
  response, CSAT). Imported tickets (`source` intercom, zendesk, freshdesk, gorgias,
  helpscout, hubspot, zoho, csv) are left out of the time metrics; their CSAT counts.
  Merged tickets are left out. 403 for agents, 404 unknown team, 422 bad period.

## 2026-11-11: Customer portal, CSAT, web form and help center (Phase 2, C2, P1-P6, S7)

- New (customer, no agent login): `POST /portal/{workspace}/code` `{email}` → always 202
  (unknown emails and workspaces too). Emails a 6-digit code that works 10 minutes; a
  new code replaces the last; at most 5 per hour per email. 403 with a message when the
  portal is limited to other email domains ("Only acme.com emails can open tickets here").
- New: `POST /portal/{workspace}/session` `{email, code}` → `{token, expires_at, customer}`
  (7 days). 401 for a wrong, used or expired code (5 wrong tries burn it). The customer's
  contact is made on first sign-in. `GET /portal/me`, `POST /portal/sign-out`.
- Portal tokens work only on `/portal/...` routes, and agent tokens never work there.
- New: `GET /portal/ticket-types` (with their fields), `GET /portal/tickets` (mine, newest
  first, cursor paging), `POST /portal/tickets` `{message, type_id?, fields?}` → 201 (channel
  `portal`; subject = the first 60 characters; rules, routing and SLA run; 429 over 10 new
  tickets an hour), `GET /portal/tickets/{id}`, `POST /portal/tickets/{id}/messages`
  `{body}` → 201 (a reply on a resolved ticket reopens it, except a plain thank-you),
  `GET /portal/attachments/{id}` (a download link, only for files on visible messages).
- Customers see only customer, agent and AI messages (`author`: "You", "Acme assistant",
  "Neha · Acme Support"), never notes or system lines, and `status_label` ("Waiting on you"
  for pending). Another customer's ticket is 404.
- New (Admin): `GET / PUT /portal-settings` `{allowed_domains, csat_survey}` (empty
  domains = anyone; `csat_survey`, default true, emails the survey on resolve).
- New CSAT (P5): when a ticket is resolved the worker emails a 1-5 survey (not for Cevanos
  chats or merged tickets; once per resolve). The links open `<portal_url>/rate?score=N#token=…`
  (new setting `CEDESK_PORTAL_URL`, empty = `CEDESK_APP_URL`); the page calls
  `GET /public/csat/{token}` and `POST /public/csat/{token}` `{score, comment?}` (no
  sign-in; 410 bad or expired link after 30 days; 409 if the ticket was reopened).
  In the portal: `POST /portal/tickets/{id}/csat` (409 until resolved), and portal tickets
  carry `csat`. Rating again replaces it. Agents get a line "Customer rated 4/5: …".
- Changed: `GET /tickets/{id}` has `csat` (`{score, comment, source, rated_at}` or null),
  and `contact` has `csat_average` and `csat_count`.
- New web form (C2, no sign-in): `GET /public/{workspace}/form` (types and fields; 404
  while it's off) and `POST /public/{workspace}/form` `{name, email, message, type_id?,
  fields?, website}` → 202. Makes a `web` ticket with `source: "web_form"` (rules,
  routing, SLA run); agent replies on it are emailed like email tickets. `website` is a
  honeypot: send it empty (filled: 202, nothing made). 429 over 10 posts per IP per 10
  minutes (`CEDESK_FORM_LIMIT`) or 5 an hour per email. The Admin turns it on:
  `web_form` in `/portal-settings` (off by default).
- New help center (P6). Agents: `GET /article-categories`, `GET /articles?q&status&category_id`,
  `GET /articles/{id}` (drafts too, with `helpful_count`/`not_helpful_count`); Admin:
  `POST/PATCH/DELETE /article-categories[/{id}]` (deleting keeps its articles, with no
  category), `POST /articles`, `PATCH/DELETE /articles/{id}` (`status` draft/published; body
  is Markdown). Public, no sign-in: `GET /public/{workspace}/articles?q&category_id` (published
  only; search matches every word, the last as a prefix, best first, top 20),
  `GET /public/{workspace}/articles/{id}`, `POST /public/{workspace}/articles/{id}/vote`
  `{helpful, visitor}` → 204 (one vote per visitor id, changeable; 429 over 60 per IP per
  10 minutes).
- New: ticket types have `for_customers` (default true; `POST/PATCH /ticket-types`). false
  hides a type from `GET /portal/ticket-types` and the web form, and they refuse it (422).
- New: when an agent replies on a `portal` ticket, the worker emails the customer "New
  reply on your request [T-1001] …" with a link to `<portal_url>/<workspace slug>/tickets/<id>`
  and no message text. At most one per ticket in 15 minutes.
- With no support address and no `CEDESK_MAIL_FROM_ADDRESS`, the survey and the portal
  reply email are skipped with a warning in the worker log (not failed events).
- CSAT: changing a rating adds "Customer changed the rating to n/5"; sending the same
  rating again adds no line.
- Limits: the CSAT link routes and the public help center reads have their own per-IP
  limit (`CEDESK_PUBLIC_LIMIT`, 120 per 10 minutes; 429), apart from sign-in.
- Fixed: CORS now allows the `Idempotency-Key` request header and exposes
  `Idempotent-Replayed`. Before, a browser on another address could not send the key.

## 2026-11-10: Cevanos connector (Phase 1, C4-C7, W8 on handover)

- New (Admin): `GET / PUT / DELETE /cevanos/connection` and `POST /cevanos/connection/check`.
  The API key and webhook secret are write-only (encrypted at rest; only `api_key_hint`,
  the last 4 characters, comes back). `team_id`: the team handovers go to;
  `close_on_resolve`: resolving a ticket closes the chat. `connected: false` means the
  Cevanos features are hidden.
- New: `POST /cevanos/events/{workspace_id}` for Cevanos's signed webhooks (not for the
  frontend). A `conversation.escalated` becomes a `chat` ticket with the chat copied in
  (message kinds `customer`, `ai`, and `agent` for a person in Cevanos), `source:
  "cevanos"`, `handover` (reason, channel, `order_id` when the customer wrote `#1234`) and
  `accepted: false`. Rules run and routing assigns it (W8).
- New: `POST /tickets/{id}/accept` (agents and up; `Idempotency-Key`). Assigns it to the
  caller, `accepted: true`, a line "Chat accepted by …", and the chat is taken over in
  Cevanos. 409 if the ticket isn't waiting.
- Changed: a `reply` on a ticket with `accepted: false` is now **409** (notes are allowed).
  Before, only macros checked it. A reply with files on a Cevanos chat is 422.
- New: `POST /tickets/from-conversation/{conversation_id}` → 201 turns a Cevanos chat into
  a ticket (409 if it has one, 404 if Cevanos has no such chat for the key).
- Replies on a Cevanos chat reach the customer through the worker; new customer messages
  come in every few seconds. The chat's end and reopening are lines on the ticket.
- New error code `Unavailable` (503): Cevanos could not be reached; try again.
- New outbox event `handover.accepted` (`{"by": member id}`).

## 2026-11-10: Phase 1 review fixes

- `POST /tickets/{id}/macros/{macro_id}` accepts `Idempotency-Key` (a macro can send a
  reply): a repeat runs nothing again and returns the ticket, with `Idempotent-Replayed: true`.
- New (Admin): `GET /email/failed` lists emails that failed every retry (newest first,
  cursor paging); `POST /email/{id}/retry` → 204 puts one back in the queue, and its ticket
  gets a line. Account emails (password reset) can't be resent: `can_retry: false`, 404.
- The worker fills the SLA of tickets with none stored (e.g. from before 0019) when it
  starts, so `sort=sla` is right without running `cedesk sla-refresh` by hand.

## 2026-11-10: virus scan (Phase 1, T18)

- With `CEDESK_SCAN_BACKEND=clamd` the worker checks every stored file with ClamAV.
  `scan_status` becomes `clean`, `infected`, or the new `scan_failed` (ClamAV could not
  check it, e.g. too big for it). Infected files' bytes are deleted and a system line on
  the ticket names the virus.
- With scanning on, `GET /attachments/{id}` is 409 until the file is `clean` (and always
  for `infected` / `scan_failed`); emails wait for their files to be checked.
  Sending a blocked file in a message is 422.
- Scanning off (the default): no change, files stay `not_scanned` and download at once.
- `CEDESK_CLAMD_MAX_BYTES` (default 100 MB, clamd's own default limit): a bigger file is
  `scan_failed` at once instead of being sent (checked against a real ClamAV 1.4).

## 2026-11-10: Idempotency-Key and outbox delivery (Phase 1)

- `Idempotency-Key` header (optional) on `POST /tickets`, `POST /tickets/{id}/messages` and
  `POST /tickets/{id}/attachments`: 1–255 printable characters, per member, kept 24 hours.
  A repeat returns what the first request made (same status, header
  `Idempotent-Replayed: true`), also when the repeats arrive at the same moment. The same
  key with a different request is 422. A request that failed doesn't use up its key.
- The worker now delivers outbox events after each change commits (one ticket's events in
  order; retries at 1 min, 5 min, 30 min, 2 h, 12 h). Handlers: none yet (the Cevanos
  connector is next; webhooks in Phase 3), so events are marked delivered.
- New (Admin): `GET /outbox/failed` lists events that failed every retry, with the error;
  `POST /outbox/{id}/retry` → 204 tries one again now (404 if it isn't failed).

## 2026-11-10: files on replies, notes and customer emails (Phase 1, T18)

- `POST /tickets/{id}/messages` takes `attachment_ids` (up to 10): files uploaded to the
  ticket first with `POST /tickets/{id}/attachments`. Only your own files, not sent before
  and not infected (422 otherwise). With files, `body` may be empty.
- Every message (ticket page, `GET /tickets/{id}/messages`, the answer to a new message)
  has `attachments: [{id, name, size, mime, scan_status}]`. `GET /attachments/{id}` gives
  the download link as before.
- On email tickets: a reply's files are sent as email attachments (at most 18 MB together,
  422 above). Files in a customer's email are kept on their message (each up to the upload
  limit, at most 20); the rest are named in a system line.
- `hd_message.body` may now be empty (migration 0020), for a message that is only files.

## 2026-11-10: SLA on tickets (Phase 1, W9, W13, T9)

- Every ticket (list, detail, customer's tickets) has `sla`: `policy`, `current`
  (`first_reply` until the first agent reply, then `resolve`), `state` (`on_track`,
  `close` under 25% left, `breached`, `met`), `paused` (outside business hours or a
  holiday; Pending does not pause), and both clocks with `target_minutes`, `due_at`,
  `stopped_at`, `state`, `minutes_left` (business minutes, negative once overdue).
- New sort: `GET /tickets?sort=sla`: open and pending by due time, soonest (or most
  overdue) first; resolved after, newest first.
- New: `GET /sla` (anyone signed in): targets per priority with a `reads_as` sentence,
  policies in order, calendar and holidays. Until an Admin changes them these are the
  defaults: Urgent 15 min / 4 h, High 1 h / 8 h, Medium 4 h / 24 h, Low 8 h / 72 h;
  policies VIP customers (30 min / 4 h), Live chat & messaging (10 min first reply),
  Default; Mon–Sat 9:00–19:00 Asia/Kolkata.
- New: `PUT /sla/targets` (Admin) `{targets: [{priority, first_reply_minutes,
  resolve_minutes}]}`: only the priorities sent change; 1 to 43200 minutes; 422 if the
  first reply is due after the resolution or a priority is repeated. Open tickets follow
  the new targets at once.
- New outbox event `contact.updated` `{contact: {id, name, email, vip}, changes}` when a
  customer's name or VIP flag changes (no ticket in it). A VIP change moves the SLA of
  the customer's open tickets.
- Server: `cedesk sla-refresh` once after the upgrade, so tickets from before it sort by
  their real due time.

## 2026-11-10: forgot password (Phase 1, A3)

- New: `POST /auth/forgot-password` `{workspace, email}` → always 202 (unknown workspace or
  email, inactive member and the hourly cap all look the same). An active member gets an
  email with a link `<CEDESK_APP_URL>/reset-password#token=...` valid for 30 minutes; at
  most 3 per hour per member. Sent from the workspace's default support address, else the
  server's `CEDESK_MAIL_FROM_ADDRESS`; with neither, nothing is sent.
- New: `POST /auth/reset-password` `{token, new_password}` → 204: new password, account
  unlocked, every session ended, all the member's links removed. Link invalid, used or
  expired → 410; password not allowed → 422 (the link still works).
- Both share the sign-in limit (429 with `Retry-After`).
- The reset email's text is wiped from the email queue once it is sent.

## 2026-11-10: email in (Phase 1, C8)

- Customers' emails to a support address now create tickets by themselves (`channel:
  "email"`, `source: "email"`, `source_id` = the email's Message-ID), with the contact made
  from the sender. The address they wrote to is recorded, so the default "billing@" rule
  works; rules and auto-assignment run as for any new ticket. The first system line is
  "Ticket created by <name> (email)".
- An email that answers a ticket (In-Reply-To / References match one of its messages, or
  the subject has its `[T-1001]` label and the sender is its customer) is added to that
  ticket as a customer message, with reopen, thank-you and reply rules as before. Quoted
  earlier messages ("On ... wrote:", "> ...") are cut off. Someone in CC who replies is
  shown by their own name (`author_name`). A merged ticket passes it to the ticket it was
  merged into.
- Customer messages from email carry `email`: `{message_id, from, to, cc, subject,
  in_reply_to, references}`; an agent reply then threads to the customer's latest email
  and comes from the address they wrote to.
- Ignored: a second delivery of the same email, auto-replies / bounces / mailing lists,
  emails from our own support addresses, and emails to no support address.
- New (for mail services, not the frontend): `POST /inbound/email` with a token; 404 when
  the server has no token set. See `deploy/README.md`.

## 2026-11-10: email out (Phase 1, C9)

Support email addresses (Admin):
- New: `GET /email-addresses` (default first), `POST /email-addresses` `{address, name,
  signature?, is_default?}` → 201, `PATCH /email-addresses/{id}` `{name?, signature?,
  is_default: true?}`, `DELETE /email-addresses/{id}` → 204,
  `POST /email-addresses/{id}/test` → 202 `{sent_to}` (emails the Admin). Others 403.
- The first address is the default; making another the default moves it; deleting the
  default hands it to the oldest remaining one. `is_default: false` → 422.
- An address is stored lower-case and belongs to one workspace only: in use anywhere → 409.
  Not an address → 422. The address itself can't be changed: add a new one, delete the old.
- `signature` (max 2,000) is added under every reply from that address, after "-- ".
  Placeholders such as `{{agent.name}}` are filled.

Replies on email tickets (`channel: "email"`), including a macro's reply step:
- Queued in the same transaction as the reply, then sent by the new `cedesk worker`.
- From: the address the customer wrote to (if it is one of ours), else the default.
  To: the customer. CC: from the customer's first email with a CC, without our addresses
  or the customer. Subject: `Re: <subject> [T-1001]`. `Message-ID`, `In-Reply-To` and
  `References` keep it in the customer's thread.
- Messages have a new field `delivery`: `pending` (queued or retrying), `sent`, `failed`,
  or `null` (not an emailed reply). A reply's `email` now holds the headers we sent.
- No support address yet: the reply is saved, not emailed, and the ticket gets the line
  "Not emailed: add a support email address in Settings first."
- The worker retries after 1, 5, 15, 60 and 240 minutes, then gives up; a refused address
  or a bad mail-server sign-in gives up at once. Giving up writes the line "Email to
  <address> could not be sent: <reason>" and `delivery` becomes `failed`.
- Notes and replies on other channels are never emailed.

## 2026-11-10: rules, customer replies and macros (Phase 2)

Rules (md W15, W16):
- New (Admin): `GET /rules` (run order), `POST /rules` `{name, enabled?, event,
  conditions, actions}` → 201 (goes last), `PATCH /rules/{id}` (any of those),
  `PUT /rules/order` `{ids}` (every rule id once), `DELETE /rules/{id}` → 204. Others 403.
- `event`: `created` (a new ticket, before auto-assignment) or `customer_replied` (the
  customer wrote on an Open or Pending ticket). Agent edits never run rules.
- Conditions (all must match): `text_contains_any` (any case; on a new ticket the subject
  and first message, on a customer reply only that new message), `channel_in`, `to_address_contains`, `status_in`, `sentiment_in`. Actions (in
  order): `set_type` (`only_if_empty`), `set_team`, `set_priority` (`keep_urgent`),
  `set_status`, `add_tag`. Shapes are in `openapi.json`. Bad JSON → 422; a name in use → 409.
- Every matching rule runs, in order; later rules see earlier changes. Each writes the
  ticket line "Rule applied: <name> → Type Refund · Team Billing · Priority High",
  listing only what actually changed; a rule that changes nothing writes no line. The
  new ticket's `ticket.created` event carries the state after the rules.
- `problems` on each rule lists what to fix (e.g. "Its team was deleted."); the rest of
  that rule still runs.
- New workspaces get the mock's six rules (refund, tracking, billing@, errors, reopen on
  reply, upset customer).

Customer replies (md 6.4, W16, W17):
- On a Resolved ticket, a reply that is only a thank-you ("thanks", "thank you so much",
  "thanks a lot", "ty", "ok thanks", "dhanyavaad", "shukriya", "🙏", ...) keeps it resolved: line "Thank-you detected · ticket stays
  resolved", nobody is notified. Any other reply reopens it (`reopen_count` + 1), line
  "Customer replied · ticket reopened".
- On Open or Pending, the `customer_replied` rules run (the default turns Pending into Open).
- The assignee gets a notification of the new kind `customer_reply`:
  "<customer> replied on T-1001: <subject>". If a reply rule moves the ticket to another
  team, the new assignee gets one `assigned` notification ("<customer> replied on T-1001,
  and it was auto-assigned to you: ..."); if nobody in that team is free, the previous
  assignee gets the `customer_reply` one.
- The same channel message (same `channel_message_id`, 1-200 characters, on a ticket) is
  saved once; a retried delivery changes nothing.
- Real customer replies come from the Cevanos connector, email and the portal (later).
  For testing now: `POST /tickets/{id}/simulate-customer-reply` `{body}` (Admin; 404 when
  the server runs with `CEDESK_ENV=production`).

Macros (md W18):
- New: `GET /macros` (everyone signed in, menu order). Admin: `POST /macros` `{name,
  steps}`, `PATCH /macros/{id}`, `PUT /macros/order` `{ids}`, `DELETE /macros/{id}`.
- Steps (in order): `reply` (placeholders filled when it runs), `note`, `set_status`,
  `set_priority`, `set_team` (re-routes the ticket like a team change), `add_tag`. At most
  one reply and one note; 1–20 steps.
- New: `POST /tickets/{id}/macros/{macro_id}` (optional `If-Match`) → the ticket with a
  new `ETag`. One transaction: if a step is refused, nothing is saved. Light agents 403;
  an unaccepted handover 409 "Accept the handover first."; a stale `If-Match` 409. The
  ticket gets one line: 'Macro “Refund approved” run by Neha: Reply sent · Tag refund ·
  Priority Low · Note added · Status Resolved' (only what actually changed).
- New workspaces get the mock's three: Refund approved, Ask for a photo, Escalate to Tech.

Existing workspaces get the default rules and macros with
`cedesk seed-workflow --workspace <address>`. Each default is given once: one an Admin
deleted or renamed never comes back. A default whose team or type is missing is left out
and added on a later run once it exists.

## 2026-11-10: saved replies (Phase 2)

- New: `GET /saved-replies` (everyone signed in), sorted by shortcut. `?q=ref` (or `/ref`)
  keeps the ones whose shortcut starts with it, for the `/` list in the reply box.
- New (Admin): `POST /saved-replies` `{shortcut, body}` → 201, `PATCH /saved-replies/{id}`
  `{shortcut?, body?}`, `DELETE /saved-replies/{id}` → 204. Others get 403.
- A shortcut is 1–32 letters, digits, `-` or `_`; it is stored lower-case without the
  slash, so `/Refund` and `refund` are the same. A taken shortcut → 409 `Conflict`. The
  body is up to 10,000 characters; placeholders such as `{{customer.first_name}}` are
  filled when the reply is sent (as for any reply), not when it is listed.
- `source` on each reply: `default` (came with the workspace), `manual`, and later
  `calendly` or an import source.
- New workspaces get the mock's four: `/refund`, `/shipping`, `/photo`, `/thanks`. An
  existing workspace gets them with `cedesk seed-workflow --workspace <address>`, which
  keeps any reply already using one of those shortcuts.

## 2026-11-10: ready for a shared server

- Sign-in and `POST /me/password` allow 20 attempts per 5 minutes per client address
  (settings `CEDESK_SIGN_IN_LIMIT`, `CEDESK_SIGN_IN_WINDOW_SECONDS`). Above that: 429,
  code `TooManyAttempts`, with a `Retry-After` header in seconds. This is on top of the
  per-account lockout.
- Browsers: pages from the sites in `CEDESK_CORS_ORIGINS` may call the API. Allowed
  request headers: `Authorization`, `Content-Type`, `If-Match`, `X-Request-ID`; readable
  response headers: `ETag`, `X-Request-ID`, `Retry-After`.

## 2026-11-10: change my password, Admin password reset

- New: `POST /me/password` with `{current_password, new_password}` → 204. Ends the
  member's other sessions; the one that made the change stays signed in. 422 when the
  current password is not right, the new one is not 10–128 characters or equals the
  current one. A wrong current password counts as a failed sign-in: after 10 the account
  is locked for 15 minutes (403 here, 401 on sign-in). 409 if the password was changed
  elsewhere at the same moment.
- New: `POST /members/{id}/password` with `{new_password}` → 204 (Admin). Signs the
  member out everywhere and unlocks a locked account. 403 for your own account (use
  `/me/password`), 404 for an unknown member.

## 2026-11-09: new agents start Offline

- A member added from now on starts with agent status `offline` and gets no tickets
  until they set `available` (`PUT /me/status`) or an Admin does
  (`PATCH /agents/{id}/routing`). Members who existed before keep `available`.
- Moving a resolved ticket to another team keeps its assignee; it is no longer routed.
- Confirmed as final: routes under `/api/v1`; errors as
  `{"error": {code, message, details?}}`.

## 2026-11-09: ticket attachments

- New: `POST /tickets/{id}/attachments?name=...` with the file itself as the body and its
  type in `Content-Type` (not a form). 413 above the limit (25 MB by default), 422 for an
  empty file or a form upload.
- New: `GET /tickets/{id}/attachments` (cursor pages, newest first).
- New: `GET /attachments/{id}` returns `{url, expires_at, attachment}`: a link that opens
  the file without signing in for 5 minutes (configurable). 409 if the virus scan marked
  the file infected.
- New: `GET /files/{token}`, the download behind that link when files are on local disk.
  404 for a changed link, 410 for an expired one. Always downloads (`attachment`), never
  opens in the browser.

## 2026-11-08: search, list speed, ticket columns, outbox, logs

- Tickets (detail and list rows) gain `accepted`, `topic`, `sentiment`, `language`,
  `source`, `parent_id`, `merged_into_id` and `child_count`. The detail also has
  `conversation_id`, `handover` and `source_id`. The handover, triage and import code fill
  them; agents can't set them yet.
- Messages gain `email` (from, to, cc, message_id) and `channel_message_id`, both null
  for now.
- `q` on `GET /tickets` also matches field values and tags.
- Every response carries `X-Request-ID`. A caller's own id is reused when it looks like
  an id (8–64 characters from `A-Z a-z 0-9 . _ -`).
- Creating a ticket for a source record that already has one returns 409.

## 2026-11-08: lists and concurrent edits (PR #3)

- Cursor pages: `cursor` / `next_cursor`, `limit` 1–200 (default 50), on tickets, ticket
  messages, notifications and members. Offset paging removed.
- `sort` on `GET /tickets`: `priority` (default), `newest`, `oldest`, `updated`.
- `ETag` on ticket responses; `If-Match` on `PATCH /tickets/{id}` and
  `POST /tickets/{id}/resolve` returns 409 when the ticket changed.
- New: `POST /tickets/{id}/resolve`, `GET /tickets/{id}/messages`,
  `GET /contacts/{id}/tickets`, `PATCH /contacts/{id}/profile`.
- `GET /tickets/{id}` shows the latest 50 messages with `older_messages_cursor`.

## 2026-11-08: hardening (PR #2)

- Every error is `{"error": {"code", "message", "details"?}}`.
- New: `GET/PUT /routing-defaults` (workspace capacity per channel). Team limit 1–50.

## 2026-11-06: first version (PR #1)

- Sign-in and members, tickets, ticket types and fields, teams, notes, replies,
  @mentions, notifications, agent status and routing.
