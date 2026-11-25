---
title: Save a view
slug: save-a-view
topic: working-with-tickets
order: 11
summary: Keep a set of filters as a one-click view, personal or shared with the team.
status: draft
updated: 2026-11-24
who: Everyone; Admins and Supervisors can share
plan: All workspaces
routes:
  - GET /api/v1/views
  - POST /api/v1/views
  - PATCH /api/v1/views/{view_id}
  - DELETE /api/v1/views/{view_id}
related:
  - working-with-tickets/find-and-sort-tickets
---

A view is a saved tab, search and filter set. Select it and the ticket list shows exactly that.

> **Who can do this:** Everyone can keep personal views. Only Admins and Supervisors can share a view with the workspace.

## Save one

1. On **Tickets**, set the tab, search, filters and sort you want.
2. Select **Save view**.
3. Name it. Choose whether to share it with everyone.

It appears under **VIEWS** above the list. Open it to apply it. Use the options next to a view to rename it, change its filters, share or unshare it, or delete it.

## Limits

50 personal views per person and 100 shared views per workspace. Names must be unique.

## Check it worked

The view appears under **VIEWS**, and selecting it shows the same tickets.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Sharing is refused | Your role cannot share views. | Ask an Admin or Supervisor. |
| A view with that name already exists | Names are unique. | Pick another name. |
