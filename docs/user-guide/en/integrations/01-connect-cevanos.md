---
title: Connect Cevanos
slug: connect-cevanos
topic: integrations
order: 1
summary: Bring AI chats that need a person into CeDesk as tickets, and switch on the AI buttons.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/cevanos/connection
  - PUT /api/v1/cevanos/connection
  - DELETE /api/v1/cevanos/connection
  - POST /api/v1/cevanos/connection/check
related:
  - integrations/install-apps
---

Cevanos is a separate product for AI help and order information. CeDesk works without it. When you connect it, AI chats that need a person arrive in CeDesk as tickets, agents reply to the customer from CeDesk, and the AI buttons appear.

> **Who can do this:** Admins.

## What connecting gives you

- Chats the AI agent could not finish arrive as tickets, waiting to be accepted.
- The AI buttons on tickets: summarise, suggest a reply, improve and translate.
- The AI page in Reports.
- Orders and payments in the ticket sidebar, read from Cevanos. The Shopify and Stripe apps are then not installed a second time.

## Connect

1. Go to **Settings › Channels › Cevanos**.
2. Paste your Cevanos **API key**.
3. Paste the **Webhook secret**. It is used to check that events really come from Cevanos.
4. Choose a **Default team for chat tickets**. Chats are routed to this team.
5. Choose whether to **close the chat in Cevanos when the ticket is resolved**.
6. Select **Save changes**.

The key and secret are stored encrypted and never shown again. Afterwards only the last four characters of the key show. Leave a box empty to keep the saved value.

## Add the webhook in Cevanos

On the same page, copy the **Webhook address**. In Cevanos, add a webhook at that address with the secret you saved. Subscribe it to these events: conversation escalated, classified, reopened and closed. **Without it, no handover from the AI agent reaches CeDesk.**

## Test it

Select **Test the key**. It checks that the saved key works and has the access it needs. If a permission is missing, the message names it.

## Disconnect

**Disconnect** stops chats syncing. Tickets stay.

## Check it worked

The page shows Connected, the AI buttons appear on a ticket, and a test chat that needs a person arrives as a ticket.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| **Test the key** reports a missing permission | The key lacks a scope. | Make a key with it in Cevanos. |
| No chats arrive | The webhook is missing or wrong in Cevanos. | Check the address, secret and four events. |
| Events fail | Cevanos could not be reached. | Retry them under **Settings › Channels › Failed events**. |
| Shopify or Stripe say Unavailable | Cevanos is connected and supplies that data. | Nothing to do. |
