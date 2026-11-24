---
title: Link, merge and child tickets
slug: link-merge-and-child-tickets
topic: working-with-tickets
order: 9
summary: Connect similar tickets, combine duplicates, and hand part of the work to another team.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/tickets/{ticket_id}/related
  - POST /api/v1/tickets/{ticket_id}/links
  - DELETE /api/v1/tickets/{ticket_id}/links/{other_id}
  - POST /api/v1/tickets/{ticket_id}/merge
  - POST /api/v1/tickets/{ticket_id}/children
related:
  - working-with-tickets/change-ticket-details-and-bulk-edit
---

Open a ticket and select **More** in the side panel. The **Related** card shows its parent, children, links and merges, and has the buttons **Link**, **Merge into…** and **Child ticket**.

> **Who can do this:** Agents, Supervisors and Admins who can reply to and change both tickets.

## Link similar tickets

Select **Link** and choose the other ticket. The link shows on both. Nothing else changes. Use it for tickets about the same problem. Remove a link from the Related card.

## Merge a duplicate

Merge when two tickets are the same conversation.

1. Open the duplicate and select **Merge into…**.
2. Choose the ticket that should stay. It must be open.
3. Confirm.

CeDesk copies the customer, agent, AI and note messages into the other ticket in time order, with their files. Open tasks and waiting approvals move there too. The duplicate is resolved and points to the other. No customer survey is sent.

## Make a child ticket

Use a child to hand part of the work to another team while you keep the customer.

1. Select **Child ticket**.
2. Choose the team. Optionally change the subject, add a note for them, or pick a person. By default the subject is the parent's, the note says where it came from, and routing picks the person.
3. Confirm.

You cannot resolve a ticket while its child tickets are open.

## Check it worked

The Related card lists the link, the merge or the child, and the history has a line for it.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "A ticket can't be merged into itself." | You chose the same ticket. | Choose another. |
| "Merge into an open ticket. That one is resolved." | The target is resolved or already merged. | Choose an open ticket. |
| "This ticket was already merged into another one." | It was merged before. | Work on the ticket it was merged into. |
| "Resolve the live chat first, then merge it." | A live Cevanos chat is still open. | Resolve it, then merge. |
| "Resolve the child tickets first" | The ticket has open children. | Resolve them first. |
