---
title: Tasks on a ticket
slug: tasks
topic: ticket-tools
order: 1
summary: Add a to-do to a ticket, give it an owner and a due time, and mark it done.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/tickets/{ticket_id}/tasks
  - POST /api/v1/tickets/{ticket_id}/tasks
  - GET /api/v1/tasks/mine
  - PATCH /api/v1/tasks/{task_id}
related:
  - ticket-tools/log-time
---

A task is a small to-do that belongs to a ticket, for example "Call the courier". Use it for work that is not a reply.

> **Who can do this:** Agents, Supervisors and Admins. Light agents cannot add tasks.

## Add a task

1. Open the ticket and look at **TASKS** in the side panel, under the **More** tab.
2. Add the task text, up to 500 characters.
3. Optionally choose an owner. If you leave it, the task is yours. The owner is told.
4. Optionally set when it is due: in 1 hour, in 4 hours, tomorrow at 9:00 in your workspace's time zone, or any time you pick.
5. Save.

Open tasks are listed first. An overdue task is marked overdue.

## Change a task or mark it done

Edit the text, owner or due time, or tick it as done. Only the task's owner or the person who added it can change it.

## Check it worked

The task appears under **TASKS** and the owner is notified.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Only the task's owner or the person who added it can change it." | You are neither. | Ask one of them, or an Admin. |
| A time zone is required | A custom due time had no time zone. | Pick the time again. |
| The task text is too long | Over 500 characters. | Shorten it. |
