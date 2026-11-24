---
title: Add and manage members
slug: add-and-manage-members
topic: account-and-access
order: 1
summary: Invite people, give them a role, reset passwords and two-step sign-in, and deactivate leavers.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces, within your agent limit
routes:
  - GET /api/v1/members
  - POST /api/v1/members
  - PATCH /api/v1/members/{member_id}
  - POST /api/v1/members/{member_id}/password
  - POST /api/v1/members/{member_id}/2fa/reset
related:
  - account-and-access/roles-and-rights
---

A member is a person on your team with an account.

> **Who can do this:** Admins. Adding a member can be refused when you reach your plan's limit on agents. Light agents are free.

## Add a member

1. Go to **Settings › Workspace › Members** and select **Add member**.
2. Type their **Name** and **Email**.
3. Choose a **Role**: Admin, Supervisor, Agent, Light agent, or a custom role.
4. Type a **Temporary password**, at least 8 characters, or more if your workspace asks for it.
5. Select **Add member**.

Share the temporary password with them privately. New members start **Offline** until they set themselves **Available**.

## Change a member

The list shows each member's name, email and role. Next to each are:

| Button | What it does |
|--------|--------------|
| Deactivate | The person can no longer sign in or be given tickets. Their history stays. **Reactivate** brings them back. |
| Reset password | You give them a new password, and they are signed out everywhere. |
| Reset 2FA | Turns off their two-step sign-in, for example after a lost phone, and signs them out everywhere. Admins are emailed. |

You can also change a member's name and role. Your own row is marked "You" and has no Reset password or Reset 2FA buttons. Change your own password under **My account**.

## Check it worked

The member appears in the list with their role and can sign in.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Adding is refused over a limit | Your plan's agent limit is reached. | Deactivate someone, use a Light agent, or change plan. |
| The password is refused | It is shorter than your workspace's minimum. | Use a longer one. |
| A member cannot be given tickets | They are a Light agent or deactivated. | Change their role. |
