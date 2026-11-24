---
title: Find and sort tickets
slug: find-and-sort-tickets
topic: working-with-tickets
order: 1
summary: The ticket list, its status tabs, search, filters and sort orders.
status: draft
updated: 2026-11-24
who: Everyone who works tickets
plan: All workspaces
routes:
  - GET /api/v1/tickets
related:
  - working-with-tickets/save-a-view
---

**Tickets** is your queue. It shows every conversation, whether a person or the AI is handling it, in one list.

> **Who can do this:** Everyone with a role that can read tickets. Depending on your role you may see only your team's tickets.

## The status tabs

At the top are tabs with a count each: **New**, **Open**, **Pending**, **Resolved** and **Snoozed**. Select one to show only those tickets.

## Search

Type in the search box. It looks at:

- the ticket number, as T-1005 or just 1005,
- the subject,
- the customer's name or email,
- the value of any ticket field,
- tags.

Capital letters do not matter.

## Filters and sort

Use the filter button next to the search box. You can show tickets that are yours, unassigned, or everyone's, narrow by priority or team, and show only tickets brought in from another help desk. **Hide snoozed** leaves out tickets that are asleep.

Sort by:

| Order | What comes first |
|-------|------------------|
| Priority (the default) | Open and Pending tickets, most urgent first, then oldest. Resolved last, newest first. |
| SLA | Open and Pending tickets whose response time runs out soonest. Resolved last. |
| Newest or Oldest | By when the ticket was made. |
| Updated | The ticket changed most recently. |

The list loads 30 tickets at a time. To keep a set of filters for next time, see [Save a view](save-a-view.md).

## Check it worked

The tickets shown match your tab, search and filters, and the count under the list says how many are shown.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A ticket you expect is not there | It is in another tab, snoozed, or a filter is on. | Clear the search, filters and **Hide snoozed**. |
| The list ends at "30+ tickets" | Only a page is loaded. | Scroll to load more. |
| Another team's ticket is missing | Your role sees your team's tickets only. | Ask an Admin. |
