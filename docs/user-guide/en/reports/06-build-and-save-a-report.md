---
title: Build and save a report
slug: build-and-save-a-report
topic: reports
order: 6
summary: Use a ready-made report or set up your own, then save it for yourself or the team.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/reports/run
  - GET /api/v1/reports/saved
  - POST /api/v1/reports/saved
  - PATCH /api/v1/reports/saved/{report_id}
  - DELETE /api/v1/reports/saved/{report_id}
related:
  - reports/dashboards
---

**Trends** lets you pick a ready-made report or build your own, and keep it.

> **Who can do this:** Supervisors and Admins. Sharing a report needs the right to share.

## Run a ready-made report

Open **Reports › Trends**. On the left are lists:

- **Over time**: tickets created or resolved per day, first response, resolution time, CSAT trend, reopen rate and backlog by day.
- **Breakdowns**: created by tag, ticket type, priority, team or channel, and first response, resolution time and CSAT by team.
- **Open now**: open tickets by priority, ticket type, team or channel.

Select one. The report runs and shows a chart and table.

## Build your own

1. Choose the **Metric**, for example Tickets created.
2. Choose **Group by**, such as Day.
3. Choose the **Range**, for example Last 30 days.
4. Add **Filters** if you want.
5. Select **Run**.

Over time, empty days show as zero, days are in your workspace's time zone, and weeks start on Monday.

## Save it

Select **Save**, name it and choose whether to share it. It appears under **SAVED REPORTS**. You can keep 50 personal and 100 shared reports. Names must be unique. A saved report runs again fresh each time, with its range counted from today, and shows you only what your own role may see.

## Rename, change or delete

Shared reports can be changed only by people who may share. Deleting a report also deletes the dashboard cards and your schedules that use it. It is refused while another member's schedule or dashboard uses it.

## Check it worked

The report runs and appears under **SAVED REPORTS**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A report takes too long | It covers too much. | Shorten the range or add filters. |
| Too many reports are running | The server limit was reached. | Wait and run again. |
| A report with that name exists | Names are unique. | Pick another name. |
| The report cannot be deleted | Another member's schedule or dashboard uses it. | Ask them to remove it first. |
