---
title: More metrics
slug: more-metrics
topic: reports
order: 3
summary: One-touch and reopen rates, backlog by age, channels, topics, time logged and open tasks.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/reports/metrics
related:
  - reports/live-queue
---

This page goes deeper than the team report.

> **Who can do this:** Supervisors and Admins.

## What it shows

| Number | Meaning |
|--------|---------|
| Open now | Open tickets at this moment, not in the period. |
| One-touch | The share of resolved tickets that needed only one reply, for example 13 of 24. |
| Reopened | The share of resolved tickets that were reopened. |
| Time logged and Billable | Minutes agents logged on tickets. |
| Open tickets by age | How many open tickets are under 1 hour, 1 to 4 hours, 4 to 24 hours or over 1 day old. |

Tabs show tickets by **Channels**, **Topics** and **Sentiment**, with how many were created and how many are still open. **Agents: time and tasks** shows each agent's time logged, billable time, open tasks and overdue tasks. When Cevanos is connected, there is also a link to its Governance report.

Imported tickets are left out of one-touch and reopen.

## Check it worked

The age groups add up to Open now.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Time logged is zero | Nobody logged time. | See [Log time on a ticket](../ticket-tools/log-time.md). |
| Reopened is high | Customers reply after resolution, or tickets are closed too early. | Look at auto-close and your resolve habits. |
