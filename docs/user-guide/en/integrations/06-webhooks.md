---
title: Webhooks
slug: webhooks
topic: integrations
order: 6
summary: Send ticket and message events to your own systems as they happen, and retry any that fail.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/webhooks
  - POST /api/v1/webhooks
  - PATCH /api/v1/webhooks/{webhook_id}
  - DELETE /api/v1/webhooks/{webhook_id}
  - POST /api/v1/webhooks/{webhook_id}/secret
  - POST /api/v1/webhooks/{webhook_id}/test
  - GET /api/v1/webhooks/deliveries
  - POST /api/v1/webhooks/deliveries/{delivery_id}/retry
related:
  - integrations/install-apps
---

A webhook tells your own system when something happens in CeDesk, in real time. Your developers read the event and act on it.

> **Who can do this:** Admins, signed in. An API key cannot manage webhooks.

## Add a webhook

1. Go to **Settings › Developers › Webhooks** and select **Add webhook**.
2. Type the web address. It must be https and a public host name.
3. Choose the events:

| Event | When |
|-------|------|
| ticket.created | A ticket is made. |
| ticket.updated | A ticket changes. |
| ticket.resolved | A ticket is resolved. |
| message.created | A customer or agent reply is added, never a private note. The event names the message. |

4. Save. Copy the **secret**. **It is shown once.**

You can have up to 20 webhooks in a workspace.

## Test it

Select **Test** to send a test ping now. It is tried once and the answer is logged.

## Keep it safe

Each delivery is signed with the secret. Your system should check the signature and that the time stamp is no older than 5 minutes. Developers: see the developer docs for the headers and how to check. If the secret leaks, select **New secret**. The old one stops at once.

## If a delivery fails

Your system should answer with a success code within 10 seconds. If not, CeDesk tries again after 1 minute, 5 minutes, 30 minutes, 2 hours and 12 hours, and then gives up. Under **Delivery log**, select **Show log** to see every try, and send a failed delivery again. A webhook that is turned off must be turned on first.

## Change or delete

**Edit** changes the address, events or on and off. **Delete** removes the webhook with its queue and log.

## Check it worked

**Test** shows a success in the log and your system receives the ping.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The address is refused | It is not https or not a public host. | Use a public https address. |
| Deliveries fail | Your system is down, slow, or answers with an error. | Fix it, then send them again. |
| The signature does not match | A wrong secret, or the body was changed. | Check the secret and sign the exact body. |
| Test fails because the secret cannot be read | The server key changed. | Make a new secret. |
| The 20 webhook limit | Too many webhooks. | Delete one. |
