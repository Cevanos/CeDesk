---
title: Support email addresses
slug: support-email-addresses
topic: channels
order: 1
summary: Add the addresses customers write to, set the sender name and signature, and choose the default.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/email-addresses
  - POST /api/v1/email-addresses
  - PATCH /api/v1/email-addresses/{address_id}
  - DELETE /api/v1/email-addresses/{address_id}
  - POST /api/v1/email-addresses/{address_id}/test
related:
  - channels/check-email-forwarding-and-dns
---

A support address is the email address your customers write to, for example support@yourcompany.com. Email sent to it becomes a ticket, and your replies go out from it.

> **Who can do this:** Admins.

## Add an address

1. Go to **Settings › Channels › Email** and select **Add address**.
2. Type the **Email address**. It must not already be used by another workspace.
3. Type a **Sender name**, such as "Acme Support". Customers see it as the sender.
4. Optionally type a **Signature**. It is added to the end of replies from this address. It can use placeholders such as `{{agent.name}}`.
5. Tick **Use as the default sender** if replies should come from this address when the customer wrote somewhere else. The first address you add is always the default.
6. Save.

Then check that email reaches CeDesk. See [Check email forwarding and DNS](check-email-forwarding-and-dns.md).

## Test it

Select **Send test** next to an address. CeDesk emails you a test from it, so you can see how it arrives.

## Change or delete

Select **Edit** to change the sender name, signature, DKIM selector or default. Select **Delete** to remove the address.

## Check it worked

The address is listed, marked **Default** if it is, with "Shown as" and the sender name. A test email arrives in your inbox.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The address is refused | Another workspace already uses it. | Use a different address. |
| The page is missing | Your role is not Admin. | Ask an Admin. |
| "Not fully checked" under Deliverability | Forwarding or DNS was not checked. | See [Check email forwarding and DNS](check-email-forwarding-and-dns.md). |
