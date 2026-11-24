---
title: The team report
slug: team-report
topic: reports
order: 2
summary: Created, resolved, open, SLA, response times and satisfaction, by team and by agent.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/reports/team
  - GET /api/v1/reports/articles
related:
  - reports/more-metrics
---

This is the first report page. It answers "how is the team doing?".

> **Who can do this:** Supervisors and Admins.

## The numbers

| Number | Meaning |
|--------|---------|
| Created | Tickets made in the period. |
| Resolved | Tickets resolved in the period. |
| Open now | Tickets open at this moment. |
| Open breaching | Open tickets already past a target. |
| SLA met | The share of tickets that met their targets. |
| First reply | The average time to the first reply. |
| Resolution | The average time to resolve. |
| CSAT | The average rating out of 5, how many ratings and the share that were happy. |

Times are real elapsed time, not counted only in business hours.

Under them you see the spread of ratings, a table **Tickets by team** (created, resolved, open, SLA met and CSAT) and a table of **Agents** with their role, status, tickets resolved and open, first reply and CSAT.

## Help articles

At the bottom, **Help articles** shows how many people marked each help-center article helpful or not helpful in the period. Each visitor has one vote per article: their latest answer counts.

## Check it worked

Created and Resolved change when you change the period.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "No team" has many tickets | They have no team set. | Add a rule that sets a team. |
| SLA met is low | Many open tickets are past their targets. | See "Response-time targets" in Automation. |
