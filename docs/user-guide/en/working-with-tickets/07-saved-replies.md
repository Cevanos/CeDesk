---
title: Saved replies
slug: saved-replies
topic: working-with-tickets
order: 7
summary: Reusable answers agents insert with a slash, and how Admins manage them.
status: draft
updated: 2026-11-24
who: Agents use them; Admins manage them
plan: All workspaces
routes:
  - GET /api/v1/saved-replies
  - POST /api/v1/saved-replies
  - PATCH /api/v1/saved-replies/{reply_id}
  - DELETE /api/v1/saved-replies/{reply_id}
related:
  - working-with-tickets/macros
---

A saved reply is a text you use often, with a short name. It saves typing the same answer again.

> **Who can do this:** Agents use saved replies. Only Admins add, change or delete them.

## Use one

In the reply box, type `/` and the start of the name, for example `/refund`. Pick the reply from the list. Edit it if you like, then select **Send reply**.

## Add one (Admins)

1. Go to **Settings › Service › Saved replies**.
2. Select **Add saved reply**.
3. Type the shortcut, such as `refund`. CeDesk stores it in lower case.
4. Type the text.
5. Save. Use **Edit** or **Delete** on an existing reply to change or remove it.

## Placeholders

The text can include `{{customer.first_name}}`, `{{customer.name}}`, `{{agent.name}}` and `{{fields.<key>}}`. They are filled in when the reply is sent. A value that is empty shows as a dash. Use only these.

## Check it worked

Type `/` in a reply box. Your new shortcut appears in the list.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No saved replies appear | None exist, or what you typed matches no shortcut. | Ask an Admin to add some. |
| The page is missing | Your role is not Admin. | Ask an Admin. |
