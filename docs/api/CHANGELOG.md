# CeDesk API changelog

`openapi.json` next to this file is the API as served (`uv run cedesk openapi`). Every
route is under `/api/v1`. Breaking changes only come in v2 (md 2B). Newest first.

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
