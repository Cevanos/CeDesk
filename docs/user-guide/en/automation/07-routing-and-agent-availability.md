---
title: Routing and agent availability
slug: routing-and-agent-availability
topic: automation
order: 7
summary: How CeDesk picks an agent for a ticket, and how to set status, capacity and skills.
status: draft
updated: 2026-11-24
who: Admins set capacity; agents set their own status
plan: All workspaces
routes:
  - GET /api/v1/agents
  - PATCH /api/v1/agents/{member_id}/routing
  - GET /api/v1/routing-defaults
  - PATCH /api/v1/routing-defaults
related:
  - automation/how-automation-works
---

When a ticket has a team, CeDesk gives it to an agent who is free. The **Agents** page shows who is available and how busy they are.

> **Who can do this:** Everyone can see the Agents page. Only Admins change capacity, channels and skills.

## The Agents page

**Agents** shows who is online, how busy they are and what they handle. It refreshes every 30 seconds. At the top are the totals: total agents, available now, away or offline, and open tickets. Filter by status:

| Status | Meaning |
|--------|---------|
| Available | Can be given new tickets. |
| On break | Away for a while. Not given tickets. |
| Transfers only | Only takes tickets handed over by hand. |
| Offline | Not working. New members start here until they set themselves Available. |

Each agent shows **Workload** as open tickets out of their capacity, a count per channel (Chat, Email, Messaging) and their skills. Light agents have no status and are never given tickets.

## Who gets a ticket

CeDesk skips agents who are not Available, who are at capacity for the ticket's channel, who lack a skill the ticket's type needs, or who are not on the ticket's team. If nobody qualifies, the ticket stays unassigned and a line says who was skipped and why, for example "Anurag is Offline; Pranav has 11 open".

## Set capacity

At the bottom of the Agents page, **Default capacity per channel** sets how many open tickets an agent can hold in Email, Chat and Messaging before auto-assignment skips them. Select **Save**. An Admin can give one agent their own limit, or set which channels and skills they have. Clearing a personal limit makes them follow the default again.

## Check it worked

Create a test ticket for a team. It goes to an Available agent with room, and the ticket's history says so.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A ticket stays unassigned | Nobody is available, has room or the skill. | Read the line on the ticket, and adjust capacity or skills. |
| An agent is not offered as assignee | They are a Light agent, deactivated, or their role cannot reply. | Choose someone else. |
