---
title: Quickstart
slug: quickstart
topic: developers
order: 1
summary: Make your first API call in five minutes with an API key.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - GET /api/v1/me
  - GET /api/v1/tickets
  - POST /api/v1/tickets
related:
  - developers/authentication
---

This guide makes three calls: check your key, list tickets and create one.

> **Who can do this:** A developer, with an API key from an Admin.

## Before you start

You need your CeDesk address, such as `https://help.example.com`, and an API key. An Admin makes one under **Settings › Developers › API keys**. See [API keys](../account-and-access/api-keys.md). The key is shown once, so copy it.

Set two variables so the examples work:

```
export CEDESK_URL=https://help.example.com
export CEDESK_KEY=cdk_your_key_here
```

## 1. Check the key

```
curl "$CEDESK_URL/api/v1/me" -H "Authorization: Bearer $CEDESK_KEY"
```

You get the member the key acts as, and the workspace. A `401` means the key is wrong or revoked.

## 2. List tickets

```
curl "$CEDESK_URL/api/v1/tickets?limit=5" -H "Authorization: Bearer $CEDESK_KEY"
```

You get the newest page of tickets and a `next_cursor` for the next page. See [Paging](paging.md).

## 3. Create a ticket

This needs a key of level read and write or higher.

```
curl -X POST "$CEDESK_URL/api/v1/tickets" \
  -H "Authorization: Bearer $CEDESK_KEY" \
  -H "Idempotency-Key: 6f1c0e5e-1b7a-4a3e-9d2e-000000000001" \
  -H "Content-Type: application/json" \
  -d '{"subject": "Where is my order?", "contact": {"email": "ana@example.com", "name": "Ana"}, "description": "It was due yesterday."}'
```

The ticket gets the next T-number. Rules, routing and the response-time clock run as for any new ticket. The idempotency key makes a retry safe. See [Idempotency keys](idempotency-keys.md).

## Where next

- [Authentication](authentication.md) for tokens, key levels and sign-in errors.
- [Webhooks](webhooks-guide.md) to be told when something happens.
- The API reference lists every route.

## Check it worked

The second call lists your tickets, and the ticket from the third call appears in **Tickets** with the channel Api.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 401 | A wrong, mistyped or revoked key. | Check the key. |
| 403 | The key's level is too low, or the action is never allowed for keys. | Use a higher level. |
| 422 | A field is missing or wrong. | Read the details in the error. See [Errors and limits](errors-and-limits.md). |
