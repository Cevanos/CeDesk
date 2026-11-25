---
title: Roles and rights
slug: roles-and-rights
topic: account-and-access
order: 2
summary: The built-in roles, and how to build a custom role with exactly the rights you want.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/roles
  - POST /api/v1/roles
  - PUT /api/v1/roles/{role_id}
  - DELETE /api/v1/roles/{role_id}
related:
  - account-and-access/teams
---

A role decides what a member can see and do.

> **Who can do this:** Admins.

## Built-in roles

| Role | What it can do |
|------|----------------|
| Admin | Everything. |
| Supervisor | What an Agent does, plus assigning to anyone, approvals and reports. |
| Agent | Works tickets: replies, notes, tasks and time. |
| Light agent | Reads tickets and adds notes. |

## Build a custom role

1. Go to **Settings › Workspace › Roles** and select **Add role**.
2. Name it.
3. Choose its **Rights**:

| Right | What it allows |
|-------|----------------|
| Reply to customers | Reply, use macros, accept chats, resolve, create tickets, assign to themselves. |
| Assign tickets to anyone | Give tickets to other members. |
| View reports | See Reports. |
| Change workspace settings | Teams, ticket types, rules, macros, SLA and email. Never members, roles or security. |
| Approve requests | Be asked to approve something, and decide. |

4. Under **Which tickets**, choose **All tickets**, or limit it to one team: edit that team's tickets and only view the rest, or see only that team's tickets and nothing else.
5. Save.

Managing members, roles and security always stays with Admins.

## Change or delete a role

Edit a role and members get the change at once. A role can be deleted only when nobody has it.

## Check it worked

Sign in as a member with the role, or ask them what they see. The menu shows only what the rights allow.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A role cannot be deleted | Members still have it. | Move them to another role. |
| A member cannot be given tickets | The role has no reply right. | Add the right. |
| A team is required | You limited the role to a team without choosing one. | Choose the team. |
