# CeDesk API changelog

`openapi.json` next to this file is the API as served (`uv run cedesk openapi`). Every
route is under `/api/v1`. Breaking changes only come in v2 (md 2B). Newest first.

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
