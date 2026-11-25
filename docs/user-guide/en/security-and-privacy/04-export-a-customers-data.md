---
title: Export a customer's data
slug: export-a-customers-data
topic: security-and-privacy
order: 4
summary: Give a customer a copy of everything you hold about them.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/privacy/export
  - GET /api/v1/privacy/requests
  - GET /api/v1/privacy/requests/{request_id}/file
related:
  - security-and-privacy/erase-a-customer
---

A customer can ask for a copy of their data. This makes it in one file.

> **Who can do this:** Admins. Every export is recorded in the audit log.

## Export

1. Go to **Settings › Security & data › Privacy**.
2. Under **Export a customer's data**, type the **Customer email** and select **Export**.
3. Under **Recent requests**, select **Download** when the file is ready.

The file holds the customer, every ticket with every message, including internal notes marked as notes, file names and ratings. It is kept for 7 days, then removed.

## Take care

The file includes your private notes. Read it before you send it on, and send it only to the customer, safely.

## Check it worked

**Recent requests** lists the export with its size and time, and **Download** works.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The file is gone | More than 7 days passed, or the customer was erased. | Export again. |
| No customer found | The email is wrong, or they were erased. | Check the address. |
