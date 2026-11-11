# CeDesk API changelog

`openapi.json` next to this file is the API as served (`uv run cedesk openapi`). Every
route is under `/api/v1`. Breaking changes only come in v2 (md 2B). Newest first.

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
