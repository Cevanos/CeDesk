---
title: Reports overview
slug: reports-overview
topic: reports
order: 1
summary: What each report page shows, who can open them and the rules all of them follow.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/reports/options
  - GET /api/v1/reports/team
related:
  - reports/team-report
---

Reports show how your team is doing: volume, speed, response times and customer satisfaction.

> **Who can do this:** Supervisors and Admins, and custom roles with the reports right. A role limited to one team sees only that team's numbers, even in a report someone else saved.

## The pages

Select **Reports** in the menu. Tabs across the top open:

| Page | What it is for |
|------|----------------|
| Team | Created, resolved, SLA and satisfaction, by team and by agent. See [The team report](team-report.md). |
| More metrics | One-touch and reopen rates, backlog by age, channels, topics and time logged. See [More metrics](more-metrics.md). |
| AI | How the AI agent is doing, when Cevanos is connected. See [The AI report](ai-report.md). |
| Trends | Build your own report and save it. See [Build and save a report](build-and-save-a-report.md). |
| Backlog | Open tickets at the end of each day. See [Backlog history](backlog-history.md). |
| Dashboards | Several reports on one page. See [Dashboards](dashboards.md). |
| Live | The queue right now. See [The live queue](live-queue.md). |
| Scheduled reports | Reports emailed to you. See [Scheduled reports](scheduled-reports.md). |

## Choosing the period

Team and More metrics have **7 days**, **30 days**, **90 days** and **Custom**, and a team picker ("All teams"). **Download CSV** saves what you see.

## Rules every report follows

- Created and resolved count tickets inside the period you chose. "Open now" and breaching numbers are as of now, not the period.
- Tickets merged into another are left out.
- Tickets imported from another help desk are left out of time numbers such as first reply, but their ratings still count.

## Check it worked

The numbers at the top match the tickets you can find under **Tickets** for the same period.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| There is no **Reports** menu | Your role cannot see reports. | Ask an Admin. |
| A number is smaller than you expect | Merged or imported tickets are left out, or you can see one team. | Check the rules above. |
