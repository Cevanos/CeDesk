---
title: Create a ticket
slug: create-a-ticket
topic: working-with-tickets
order: 2
summary: Log a request for a customer by hand, or start one from a Cevanos chat.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - POST /api/v1/tickets
  - POST /api/v1/tickets/from-conversation/{conversation_id}
related:
  - working-with-tickets/reply-and-add-private-notes
---

Most tickets arrive by themselves from email, chat or the portal. Create one by hand when a customer reached you another way, for example by phone.

> **Who can do this:** Agents, Supervisors and Admins. Light agents cannot.

## Create it

1. Select **New** on the Tickets page.
2. Under **The request**, type the **Subject** and the **First message**, which is what the customer said.
3. Under **The customer**, type their **Name** and **Email**.
4. On the right, under **Properties**, choose a **Priority** (Urgent, High, Medium or Low), the **Channel** (Email, Web, Portal, Api, Chat or Phone), a **Type** and any **Tags**. Separate tags with commas, up to 20.
5. Under **Routing**, choose a **Team** and an **Assignee**. If you pick a team and leave the assignee, CeDesk routes it for you. With neither, it stays unassigned until someone picks it up.
6. Select **Create ticket**.

The ticket gets the next T-number.

## Start from a Cevanos chat

If a customer chatted with the AI agent, paste the chat's conversation ID under **Start from a Cevanos chat**. The chat becomes a ticket. It waits until someone accepts it. See [Accept a chat handover](accept-a-chat-handover.md).

## Check it worked

The ticket opens with its number, and your rules may already have set a type and team. A line on the ticket says it was created by the customer's name.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| **New** is missing | Your role cannot create tickets. | Ask an Admin. |
| The ticket has no assignee | No team or person was chosen, or nobody was free. | Assign it yourself. |
