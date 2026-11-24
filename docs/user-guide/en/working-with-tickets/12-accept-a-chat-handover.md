---
title: Accept a chat handover
slug: accept-a-chat-handover
topic: working-with-tickets
order: 12
summary: Take over a live chat that the AI agent passed to your team.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: Workspaces connected to Cevanos
routes:
  - POST /api/v1/tickets/{ticket_id}/accept
related:
  - working-with-tickets/create-a-ticket
---

When the AI agent in a chat cannot finish, it hands the customer to your team. The chat arrives as a ticket that waits until a person accepts it. Until then you cannot reply to the customer.

> **Who can do this:** Agents, Supervisors and Admins. Taking a chat from another agent needs the right to reassign.

## Accept it

1. Open the ticket waiting for a handover.
2. Select the button to accept it.

The ticket is assigned to you, and the customer sees in the chat that you joined. You can now reply.

## Check it worked

The ticket shows you as the assignee and the **Reply** box is available.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The accept is refused | The ticket is not waiting for a handover. | Open it from the list; it may already be taken. |
| You cannot reply | The handover is not accepted. | Accept it first. |
