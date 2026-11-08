# CeDesk API changelog

`openapi.json` next to this file is the API as served (`uv run cedesk openapi`). Every
route is under `/api/v1`. Breaking changes only come in v2 (md 2B). Newest first.

## 2026-11-08: new agents start Offline

- A member added from now on starts with agent status `offline` and gets no tickets
  until they set `available` (`PUT /me/status`) or an Admin does
  (`PATCH /agents/{id}/routing`). Members who existed before keep `available`.
- Moving a resolved ticket to another team keeps its assignee; it is no longer routed.
- Confirmed as final: routes under `/api/v1`; errors as
  `{"error": {code, message, details?}}`.

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
