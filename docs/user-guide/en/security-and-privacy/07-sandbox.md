---
title: The sandbox
slug: sandbox
topic: security-and-privacy
order: 7
summary: Try changes to rules, macros and saved replies on a copy, compare them with live, then deploy.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/sandbox
  - POST /api/v1/sandbox
  - DELETE /api/v1/sandbox
  - PUT /api/v1/sandbox/{section}
  - GET /api/v1/sandbox/compare
  - POST /api/v1/sandbox/try
  - POST /api/v1/sandbox/deploy
related:
  - security-and-privacy/audit-log
---

The sandbox is a private copy of your live rules, macros and saved replies. Change it freely. Nothing live changes until you deploy.

> **Who can do this:** Admins. A workspace has one sandbox.

## Make and edit it

1. Go to **Settings › Security & data › Sandbox** and create the sandbox. It starts as a copy of your live settings.
2. Choose **Rules**, **Macros** or **Saved replies**. Each shows a count.
3. Edit the whole list, then select **Save**. Each item is checked as on the normal settings page.

The sandbox is marked **Not live**.

## Try it

**Try on samples** runs your live rules and the sandbox rules on built-in sample tickets, such as an email about a refund or an angry chat, and shows both results side by side. Nothing is saved.

## Compare and deploy

**Compare with live** shows what deploying would change. **Deploy to live…** puts the sandbox over your live settings, all or nothing. Every added, changed and removed item is saved with its own audit entry. The sandbox then holds a fresh copy of the live settings.

## Delete the sandbox

**Delete sandbox** removes it. Live settings are not touched.

## Check it worked

After deploying, the live Rules page matches the sandbox, and the audit log has an entry for each change.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Deploy is refused because there is nothing to deploy | The sandbox matches live. | Make a change first. |
| An item no longer fits | A team or type it uses was deleted. | Edit that item. |
| Making a sandbox is refused | One exists already. | Use it or delete it. |
