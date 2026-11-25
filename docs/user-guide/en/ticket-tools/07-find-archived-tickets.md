---
title: Find archived tickets
slug: find-archived-tickets
topic: ticket-tools
order: 7
summary: Where old resolved tickets go, how to read them, and how to change when they move.
status: draft
updated: 2026-11-24
who: Everyone who works tickets; Admins set the age
plan: All workspaces
routes:
  - GET /api/v1/archive/tickets
  - GET /api/v1/archive/tickets/{ticket_id}
  - GET /api/v1/archive/settings
  - PUT /api/v1/archive/settings
related:
  - ticket-tools/ticket-types-and-fields
---

Old resolved tickets move out of the ticket list so it stays short. They go to the **Archive**, where you can still read them.

> **Who can do this:** Everyone who works tickets can search the archive. Admins set the age. A role that sees one team sees only that team's archived tickets.

## Find one

1. Select **Archive** in the menu.
2. Type a ticket number such as 1042 or T-1042, or a customer's email.
3. Open the ticket.

You see up to 50 matches, newest first. An archived ticket is read only: you cannot reply to it, change it or reopen it. It shows everything that was on it, including private notes, files' names, the rating, time, tasks, approvals, links, side conversations and Jira issues. Names of the type, team and assignee are as they were when it was archived.

If a customer answers by email later, the answer becomes a new ticket that follows the archived one.

## Change when tickets move (Admins)

Go to **Settings › Security & data › Plan & retention**. Under **Archive**, set **Archive resolved tickets after (days)**, from 30 to 3650. Empty uses the default, 120 days. The move happens in the nightly run. Retention can still delete tickets when its own setting says so, so a short retention period can leave nothing to archive.

## Check it worked

An old resolved ticket is not in the list but is found by its number in **Archive**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Nothing found | The number or email is wrong, the ticket is not old enough, or it was deleted by retention. | Check the number, and the retention setting. |
| Another team's ticket is missing | Your role sees one team. | Ask an Admin. |
