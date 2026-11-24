---
title: How long tickets are kept (retention)
slug: retention
topic: security-and-privacy
order: 6
summary: Delete resolved tickets after a set time, and keep a copy before they go.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces; a cloud plan may cap the days
routes:
  - GET /api/v1/retention
  - PUT /api/v1/retention
  - GET /api/v1/retention/exports
  - GET /api/v1/retention/exports/{export_id}/file
related:
  - security-and-privacy/audit-log
---

Retention removes old resolved tickets so you do not keep customer data longer than you need to. **Deleted tickets cannot be brought back.**

> **Who can do this:** Admins. Retention is off until you set a number of days.

## Set it

1. Go to **Settings › Security & data › Plan & retention**.
2. Under **Retention**, set **Delete resolved tickets after (days)**, from 30 to 3650. Leave it empty to keep resolved tickets for good.
3. Turn on **Export before delete** if you want a copy first.
4. Select **Save**.

Once a day, CeDesk deletes resolved tickets and their files that are older than that. Each run is in the audit log. Old resolved tickets move to the archive first; see "Find archived tickets" in Ticket tools.

## Retention exports

With **Export before delete** on, each batch of deleted tickets is written to a file, listed under **Retention export files**. The file holds each ticket with its customer, messages including internal notes, file names and rating. Admins can download it for 30 days, then it goes. Downloads are recorded in the audit log. Treat the files as customer data.

## Check it worked

Under **Retention** the "Right now" line shows your setting, and "Last changed by" shows you.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The days are refused | Outside 30 to 3650, or over what your plan allows. | Choose a number in range. |
| An export file is gone | More than 30 days passed. | It cannot be recovered. |
| A ticket you need was deleted | Retention ran after it was resolved. | It cannot be recovered. Lengthen retention. |
