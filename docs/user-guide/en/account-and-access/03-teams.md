---
title: Teams
slug: teams
topic: account-and-access
order: 3
summary: Group agents so tickets are routed to the right people and shared out evenly.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/teams
  - POST /api/v1/teams
  - PATCH /api/v1/teams/{team_id}
  - DELETE /api/v1/teams/{team_id}
related:
  - account-and-access/roles-and-rights
---

A team is a group of agents, such as Billing or Shipping. A ticket can belong to one team.

> **Who can do this:** Admins.

## Add a team

1. Go to **Settings › Workspace › Teams** and select **Add team**.
2. Type a **Name**.
3. Turn on **Auto-assign new tickets (round-robin)** if tickets should be shared out among the team automatically.
4. Set **Max open tickets per agent**.
5. Tick the **Members** of the team.
6. Select **Save team**.

The list shows each team's number of members and the open-ticket limit, and whether auto-assign is on.

## Change or delete

Use **Edit** to rename a team, change its settings or replace its members. **Delete** removes the team, and its tickets become team-less.

## Check it worked

A new ticket set to the team goes to a member who is free, and the ticket history says who was skipped and why.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Tickets are not auto-assigned | Auto-assign is off, or nobody is free. | Check the setting and the Agents page. |
| Tickets have no team | Nothing set one. | Add a rule that sets a team. |
