---
title: Dashboards
slug: dashboards
topic: reports
order: 7
summary: Put up to 20 reports on one page and share it with the team.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/dashboards
  - POST /api/v1/dashboards
  - PATCH /api/v1/dashboards/{dashboard_id}
  - DELETE /api/v1/dashboards/{dashboard_id}
  - POST /api/v1/dashboards/{dashboard_id}/cards/{card_id}/run
related:
  - reports/build-and-save-a-report
---

A dashboard is a set of reports on one page, so you see everything at once.

> **Who can do this:** Supervisors and Admins.

## Make one

1. Open **Reports › Dashboards** and select **New dashboard**.
2. Name it.
3. Add cards. A card is a ready-made report or one you saved. Up to 20.
4. Choose whether to share it with everyone who can see reports.
5. Save.

A shared dashboard can hold only ready-made and shared reports. Each dashboard shows its card count and owner. You can keep 20 personal and 50 shared dashboards, with unique names.

## Using it

On the dashboard page, pick the range and team once and every card follows. Each card runs on its own, so one slow card does not stop the rest, and each shows only what your role may see.

## Change or delete

Rename it, change its cards, share or unshare it, or delete it. Only its owner changes a personal dashboard.

## Check it worked

The dashboard lists its cards and each shows numbers.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A report cannot be added to a shared dashboard | It is a personal report. | Share the report first. |
| A card is empty or missing | The report was deleted, or you cannot see it. | Remove the card or pick another. |
