---
title: Erase a customer
slug: erase-a-customer
topic: security-and-privacy
order: 5
summary: Permanently remove a customer's personal details and anonymise their tickets.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/privacy/erase
related:
  - security-and-privacy/export-a-customers-data
---

When a customer asks you to delete their data, erasing removes their personal details. **It cannot be undone.** Consider [exporting their data](export-a-customers-data.md) first.

> **Who can do this:** Admins. Every erasure is recorded in the audit log.

## Erase

1. Go to **Settings › Security & data › Privacy**.
2. Under **Erase a customer**, type the customer's email and select **Find**.
3. Check it is the right person.
4. Type their email again to confirm, and erase.

## What happens

- Their name and email, every message, subject and field on their tickets, rating comments, task and approval texts and queued emails are replaced with "(erased)".
- Their files and exports are deleted from storage, and they are signed out of the portal. Events not yet sent are stopped.
- Counts, reply times, scores and time logged stay, so your reports are not broken.
- Erased customers are never emailed by a campaign.

## What you must do yourself

A chat kept in Cevanos must be erased in Cevanos too.

## Check it worked

**Recent requests** lists "erase", and the customer's tickets show "(erased)".

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The confirmation is refused | The email typed does not match. | Type it exactly. |
| It is refused because the customer is already erased | It was done before. | Nothing to do. |
