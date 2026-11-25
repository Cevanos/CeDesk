---
title: The live queue
slug: live-queue
topic: reports
order: 4
summary: The queue right now: open, waiting, past SLA and due soon, by team, with the tickets waiting for a first reply.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/reports/live
related:
  - reports/backlog-history
---

The live queue is for the person watching the day. It updates by itself.

> **Who can do this:** Supervisors and Admins. A role limited to one team sees that team's queue.

## What it shows

Open **Reports › Live**. At the top: **OPEN**, **PENDING**, **SNOOZED**, **PAST SLA** and **SLA DUE SOON**. Choose how far ahead "due soon" looks with **SLA due within**, for example 1 hour. **By team** splits the same numbers by team. Under it, **Waiting for a first reply** lists tickets nobody has answered, with their team, assignee, priority and how long they have waited.

The page refreshes when tickets change and every 30 seconds. **Download CSV** saves it.

Imported tickets never count as waiting, at risk or breached.

## Check it worked

Reply to a waiting ticket. It leaves the list within a few seconds.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Numbers differ from the Tickets page | Snoozed, merged and imported tickets are counted differently. | Compare like with like. |
| Many tickets past SLA | Targets or business hours do not match reality. | Review your SLA settings. |
