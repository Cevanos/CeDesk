---
title: Orders and payments on a ticket
slug: orders-and-payments-on-a-ticket
topic: integrations
order: 3
summary: See a customer's latest orders and payments next to their ticket.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: With Shopify and Stripe, or Cevanos, connected
routes:
  - GET /api/v1/tickets/{ticket_id}/orders
  - GET /api/v1/tickets/{ticket_id}/payments
related:
  - integrations/install-apps
---

When an order or payment tool is connected, a ticket shows the customer's recent orders and payments beside the conversation, so you do not have to look them up.

> **Who can do this:** Everyone who can read the ticket. An Admin connects the source first.

## Where the data comes from

- **Shopify** gives orders and **Stripe** gives payments, if an Admin installed them under **Settings › Developers › Apps**.
- If Cevanos is connected, orders and payments come from its own integrations instead.

CeDesk looks up the customer by their email each time you open the ticket. It only reads. Nothing is copied into CeDesk.

## See it

Open a ticket. The orders and payments appear in the ticket's side panel.

## Check it worked

A customer with a known order shows it. A customer the store does not know shows nothing.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Nothing shows | No source is connected, or the store does not know this email. | Ask an Admin to connect one. |
| It is slow or missing | The provider did not answer. | Reopen the ticket in a moment. |
