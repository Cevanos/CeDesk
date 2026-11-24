---
title: The inbound API
slug: inbound-api
topic: developers
order: 8
summary: Send a customer's requests and replies from your own system into CeDesk, and read agents' answers back.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - POST /api/v1/inbound/tickets
  - POST /api/v1/inbound/tickets/{ticket_id}/messages
related:
  - developers/webhooks-guide
---

If customers talk to you in your own app, the inbound API turns what they say into tickets, and keeps the conversation going. Agents answer in CeDesk.

> **Who can do this:** A developer, with an API key of level read and write or higher. A person's token is refused.

## Make a ticket

```
curl -X POST "$CEDESK_URL/api/v1/inbound/tickets" \
  -H "Authorization: Bearer $CEDESK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contact": {"email": "ana@example.com", "name": "Ana"}, "message": "My order never came.", "external_id": "order-chat-8841"}'
```

- The ticket is on the Api channel, as that customer, found by email.
- Triage, rules, routing and the response-time clock run.
- `subject` is optional. It defaults to the first 60 characters of the message.
- Card numbers in the message are removed.
- `external_id` is your own id for it, up to 200 characters. Send the same one again and nothing is made twice. You get 200 instead of 201, with `created: false`.

The answer has the `ticket_id`, the `number` and `created`.

## Add the customer's next message

```
curl -X POST "$CEDESK_URL/api/v1/inbound/tickets/$TICKET_ID/messages" \
  -H "Authorization: Bearer $CEDESK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "Any news?", "external_id": "msg-5521"}'
```

It works as the customer's reply. The `outcome` says what happened:

| Outcome | Meaning |
|---------|---------|
| added | Saved as a message. |
| rules_ran | Saved, and reply rules ran. |
| reopened | The ticket was resolved and is open again. |
| thanked | A thank-you on a resolved ticket. It stays resolved. |
| duplicate | This `external_id` was saved before. Nothing changed. |

It works only on a ticket that came in through the API.

## Read the agents' answers

Agents' replies stay in CeDesk. To get them, add a webhook on `message.created`. See [Webhooks for developers](webhooks-guide.md). It tells you a message was added. Read the message through `GET /tickets/{id}/messages`.

## Check it worked

The ticket appears in **Tickets** with the channel Api, and sending the same `external_id` again changes nothing.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 403 | The key is read only, or this is not a key. | Use a read and write key. |
| 404 on a message | There is no such ticket. | Check the id. |
| 409 on a message | The ticket came in another way, or was merged. | Make a new ticket. |
| 429 | Over the workspace's key budget. | Wait for `Retry-After`. |
