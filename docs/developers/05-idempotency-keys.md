---
title: Idempotency keys
slug: idempotency-keys
topic: developers
order: 5
summary: Retry a request that may or may not have worked without making a duplicate.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - POST /api/v1/tickets
  - POST /api/v1/tickets/{ticket_id}/messages
related:
  - developers/errors-and-limits
---

A request can fail in the middle: the network drops after CeDesk made the ticket but before you got the answer. If you send it again you may make a second one. An idempotency key stops that.

> **Who can do this:** A developer.

## How to use it

Add an `Idempotency-Key` header to a POST. Use any unique string, up to 255 characters. A UUID is good. If you retry, send the same key.

```
curl -X POST "$CEDESK_URL/api/v1/tickets" \
  -H "Authorization: Bearer $CEDESK_KEY" \
  -H "Idempotency-Key: 6f1c0e5e-1b7a-4a3e-9d2e-000000000001" \
  -H "Content-Type: application/json" \
  -d '{"subject": "…", "contact": {"email": "ana@example.com", "name": "Ana"}}'
```

A repeat returns what the first request made, and the response has the header `Idempotent-Replayed: true`. Nothing is made twice.

## The rules

- Keys last 24 hours, and belong to the member or key that sent them.
- The same key with a **different** request is refused with 422.
- If the first request did not finish, the repeat is refused with 422 and asks for a new key.
- A repeat that arrives while the first is still running waits for it.

## Where it works

| Route | What it makes |
|-------|---------------|
| `POST /tickets` | A ticket. |
| `POST /tickets/{id}/messages` | A reply or note. |
| `POST /tickets/{id}/attachments` | A file. |
| `POST /tickets/{id}/children` | A child ticket. |
| `POST /tickets/{id}/accept` | Accepting a chat. |
| `POST /tickets/{id}/macros/{macro_id}` | Running a macro. |
| `POST /tickets/{id}/mobile-reply` | A reply from the mobile app. |
| Import routes | Connecting a source, uploading a file, starting and resuming a run. |

The inbound API for another system does not use this header. It has its own `external_id`. See [The inbound API](inbound-api.md).

## Check it worked

Send the same request twice with the same key. You get one ticket, and the second answer has the replay header.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 422 saying the key was used for a different request | You reused a key. | Use a new key per request. |
| 422 saying the request did not finish | The first try failed part-way. | Use a new key. |
