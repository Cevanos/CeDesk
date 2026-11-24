---
title: Macros
slug: macros
topic: working-with-tickets
order: 8
summary: Run several changes on a ticket with one click, and how Admins build them.
status: draft
updated: 2026-11-24
who: Agents run them; Admins build them
plan: All workspaces
routes:
  - GET /api/v1/macros
  - POST /api/v1/macros
  - PATCH /api/v1/macros/{macro_id}
  - DELETE /api/v1/macros/{macro_id}
  - PUT /api/v1/macros/order
  - POST /api/v1/tickets/{ticket_id}/macros/{macro_id}
related:
  - working-with-tickets/saved-replies
---

A macro is a saved list of steps. One click does them all, for example send a refund message, tag the ticket and resolve it.

> **Who can do this:** Agents run macros. Only Admins add, change, delete or reorder them. Light agents cannot run them.

## Run a macro

1. Open the ticket and select **Macros**.
2. Pick one. Each shows its steps, for example "Reply · Tag: refund · Status: resolved".

The steps run in order. If one is refused, nothing is saved, so a ticket is never left half changed.

## Build one (Admins)

1. Go to **Settings › Service › Macros** and select **Add macro**.
2. Name it, then add steps. A step can be:

| Step | What it does |
|------|--------------|
| Send a reply | Sends text to the customer. Placeholders are filled in. At most one. |
| Add a private note | Adds a note. At most one. |
| Set status | Open, Pending or Resolved. |
| Set priority | Urgent, High, Medium or Low. |
| Set type | Changes the ticket type. It can apply only if the type is empty. |
| Move to team | Changes the team and re-routes the ticket. |
| Add tag | Adds one tag. |

3. Save. New macros go last in the menu. Admins can reorder the menu.

## Check it worked

The ticket shows every change, and the history has a line for each step.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| **Macros** does nothing | Your role is Light agent, or a chat handover is not accepted. | Use another role, or [accept the chat](accept-a-chat-handover.md). |
| Nothing changed | A step was refused, so all were undone. | Read the message and fix the cause, for example resolve open child tickets. |
