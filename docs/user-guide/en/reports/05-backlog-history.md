---
title: Backlog history
slug: backlog-history
topic: reports
order: 5
summary: How many tickets were open, pending and past SLA at the end of each day.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/reports/backlog
related:
  - reports/live-queue
---

The backlog report shows whether the pile of open tickets is growing or shrinking.

> **Who can do this:** Supervisors and Admins.

## What it shows

Open **Reports › Backlog**. A chart and a table give, for each day, the **Open**, **Pending** and **Breaching** tickets at the end of that day. Breaching means open or pending and past the SLA time at that moment. The page says when history starts, for example "History starts 22 Nov 2026".

Days follow your workspace's time zone. Merged tickets and imported tickets are left out.

## Why there are gaps

The numbers come from a snapshot the system takes each day. History starts the day the feature was turned on, and a day the system was down has no snapshot.

## Check it worked

Yesterday's row appears the next day.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No history before a date | Snapshots only started then. | Nothing can be recovered. |
| A day is missing | The system was down at snapshot time. | Nothing can be recovered. |
