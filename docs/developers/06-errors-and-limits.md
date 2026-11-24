---
title: Errors and limits
slug: errors-and-limits
topic: developers
order: 6
summary: The shape of every error, what each status code means, and how to back off.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - GET /api/v1/me
related:
  - developers/idempotency-keys
---

Every error has the same shape, so one piece of code can read them all.

> **Who can do this:** A developer.

## The shape

```
{"error": {"code": "NotFound", "message": "Ticket not found.", "details": []}}
```

- `code` is stable. Branch on it, not on the message.
- `message` is plain text you can show to a person.
- `details` appears only on validation errors, one entry per field that is wrong.

## Status codes

| Status | Meaning | What to do |
|--------|---------|------------|
| 400, 422 | The request is not valid. | Read `details`, fix the request. Do not retry unchanged. |
| 401 | Not signed in, the token ended, or the key is revoked. | Sign in again. The response carries `WWW-Authenticate: Bearer`. |
| 402 | A plan limit is reached, for example adding a member or uploading a file. | Free something, or change plan. |
| 403 | Not allowed. Common codes: `IpNotAllowed`, `PasswordExpired`, `TwoFactorRequired`, `TwoFactorSetupRequired`. | Fix the cause. See [Authentication](authentication.md). |
| 404 | Not found, or not visible to you, for example another team's ticket. | Check the id. |
| 409 | The request clashes with the current state. | Read the message, refetch, decide. |
| 413 | The body is too big, for example a file over 25 MB. | Send less. |
| 429 | Too many requests. | Wait for `Retry-After` seconds. |
| 503 | A system CeDesk depends on did not answer, such as Cevanos or Jira. | Try again. |

## Back off

For 429 and 503, wait and retry. Read the `Retry-After` header when it is there. Otherwise wait a second, then double the wait each time, with a little randomness, up to a limit. Use an [idempotency key](idempotency-keys.md) so a retry cannot make a duplicate.

## Limits to know

Limits are listed on each route in the API reference. The common ones:

| What | Limit |
|------|-------|
| Bulk ticket changes | 100 tickets per request, 10 requests a minute per person |
| Live streams | 5 open per person |
| Attachments | 25 MB by default |
| Webhooks | 20 per workspace |
| Transcripts emailed | 10 an hour per person |

## Check it worked

A deliberate bad request returns the error shape with a `code` you can match.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 429 with no `Retry-After` | A limit without a wait time. | Back off as above. |
| 404 for a ticket that exists | The key or person cannot see it. | Check the team scope. |
