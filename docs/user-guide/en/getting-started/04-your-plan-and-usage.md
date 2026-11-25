---
title: Your plan and usage
slug: your-plan-and-usage
topic: getting-started
order: 4
summary: See how many agents, tickets and files you use, and what happens when you reach a limit.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/usage
related:
  - getting-started/what-cedesk-is
---

Cloud plans limit how many agents, tickets a month and how much file storage a workspace can use. This page shows where you stand.

> **Who can do this:** Admins. Self-hosted servers usually have no limits.

## See your usage

Go to **Settings › Security & data › Plan & retention**. Under **Plan and usage** you see:

| Line | What it counts |
|------|----------------|
| Plan | Your cloud plan, or "No plan limits". |
| Agents | Active members who take tickets. Light agents are free and are not counted. |
| Tickets this month | Tickets made this calendar month (UTC). Imported tickets are not counted. |
| File storage | The size of files on tickets. |

Next to each is your limit, or "no limit".

The same page sets how long resolved tickets are kept before they are deleted.

## When you reach a limit

Once you are over a limit, CeDesk refuses to:

- add a member,
- upload a file,
- let an agent or an API key open a ticket.

A customer's own email, chat or portal message is never refused, so you never lose a customer message because of a limit.

## Check it worked

Plan & retention shows your numbers. After you add or remove an agent, the **Agents** number changes.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A refusal that names a limit when adding a member or uploading | You reached the plan limit. | Free up space or an agent, or ask to change plan. |
| "No plan limits" | The server has no plan set, as on self-hosted setups. | Nothing. |
