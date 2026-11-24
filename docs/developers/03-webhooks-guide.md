---
title: Webhooks for developers
slug: webhooks-guide
topic: developers
order: 3
summary: The events CeDesk sends, how to check the signature, what happens when you fail, and how to handle repeats.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - POST /api/v1/webhooks
  - POST /api/v1/webhooks/{webhook_id}/test
  - GET /api/v1/webhooks/deliveries
  - POST /api/v1/webhooks/deliveries/{delivery_id}/retry
related:
  - developers/idempotency-keys
  - integrations/webhooks
---

A webhook is CeDesk calling your server when something happens. This guide is for the developer who writes the receiver. An Admin adds the webhook under **Settings › Developers › Webhooks**; see [Webhooks](../integrations/webhooks.md).

> **Who can do this:** A developer. Adding a webhook needs an Admin who is signed in. An API key cannot.

## The URL

- It must start with https and have a public host name.
- No IP address, no user or password in it, no `#` part.
- CeDesk checks before every send that the host resolves only to public addresses, and never follows a redirect.
- Up to 20 webhooks per workspace.

## The events

| Event | When |
|-------|------|
| `ticket.created` | A ticket is made. |
| `ticket.updated` | A ticket changes. The body lists the names of the fields that changed. |
| `ticket.resolved` | A ticket is resolved. |
| `message.created` | A customer or agent reply is added. Never a note or a system line. |

A delivery is a POST with a JSON body like this:

```
{
  "event": "ticket.updated",
  "id": "…",
  "occurred_at": "2026-11-24T10:00:00Z",
  "ticket": {"id": "…", "number": 1327, "subject": "…", "status": "open", "priority": "urgent", "team": "…", "topic": "…"},
  "changes": ["status"]
}
```

`message.created` names the message, with its id and kind, but never its text. Read the text with `GET /tickets/{id}/messages`.

## The headers

| Header | Value |
|--------|-------|
| `X-CeDesk-Event` | The event name. |
| `X-CeDesk-Delivery` | A delivery id. **The same on every retry.** |
| `X-CeDesk-Timestamp` | Unix seconds when it was sent. |
| `X-CeDesk-Signature` | `sha256=` and the hex HMAC-SHA256 of the timestamp, a dot, and the raw body, with your webhook secret as the key. |

## Check the signature

Do this before you trust anything. Use the raw bytes of the body, not a re-encoded copy.

Python:

```
import hashlib, hmac, time

def verify(secret: str, timestamp: str, body: bytes, header: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False  # too old: could be a replay
    signed = timestamp.encode() + b"." + body
    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Node:

```
const crypto = require("node:crypto");

function verify(secret, timestamp, body, header) {
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;
  const expected =
    "sha256=" + crypto.createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(header);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

Reject a delivery older than 5 minutes. The timestamp is signed, so an old delivery cannot be replayed as new.

## Answer, then work

Answer with a 2xx within 10 seconds. Do slow work after you answer.

## Retries and repeats

If you answer anything else, or do not answer in 10 seconds, CeDesk tries again after 1 minute, 5 minutes, 30 minutes, 2 hours and 12 hours. After the sixth try the delivery has failed. An Admin sees it under **Delivery log** and can send it again, or you can use `POST /webhooks/deliveries/{id}/retry` after turning the webhook on.

Because of retries, the same event can arrive twice. Keep the `X-CeDesk-Delivery` ids you have handled for a day or two and drop a repeat. Do not rely on order between events.

## Test it

`POST /webhooks/{id}/test` sends a signed ping now, with the body `{"event": "ping", "id": …, "occurred_at": …, "webhook": {"id": …}}`. It is tried once, with no retries, and the answer is logged.

## Rotate the secret

`POST /webhooks/{id}/secret` gives a new secret, shown once. The old one stops at once, so switch your receiver in the same minute.

## Check it worked

The test ping arrives, your code accepts the signature, and the delivery log shows a 2xx.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The signature never matches | You signed a re-encoded body, or used the wrong secret. | Use the raw body and the current secret. |
| Every delivery is old | Your clock is wrong. | Fix the server's clock. |
| Deliveries fail with a timeout | You work before answering. | Answer first. |
| The webhook is refused for its address | It is not https, has an IP address or a user in it. | Fix the address. |
