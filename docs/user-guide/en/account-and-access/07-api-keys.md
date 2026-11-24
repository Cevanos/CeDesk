---
title: API keys
slug: api-keys
topic: account-and-access
order: 7
summary: Let your own systems call CeDesk, choose how much access a key has, and revoke it.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/api-keys
  - POST /api/v1/api-keys
  - DELETE /api/v1/api-keys/{key_id}
related:
  - account-and-access/roles-and-rights
---

An API key lets another system, such as your website, use CeDesk without a person signing in. A key starts with `cdk_`.

> **Who can do this:** Admins, signed in. A key cannot make or revoke keys.

## Make a key

1. Go to **Settings › Developers › API keys** and select **Make API key**.
2. Name it, for example the system that will use it.
3. Choose how much it may do:

| Level | What it may do |
|-------|----------------|
| Read only | Read data. |
| Read and write | Also change tickets, customers, notes, tags, tasks and time, and send in customer messages. |
| Admin | Also manage teams, ticket types, saved replies, macros, rules and the help center. |

4. Copy the key. **It is shown once.** CeDesk keeps only its end, shown as "cdk_…" and four characters.

A key acts as the Admin who made it, within its level. It never reaches sign-in, members, roles, security, single sign-on, Cevanos, imports, privacy or API keys, and never approves anything.

## Keep track

The list shows each key's name, level, when it was made, when it was last used, and which are revoked. Make one key for each system so you can revoke one without breaking the rest.

## Revoke a key

Select **Revoke**. From then on the key is refused. Revoking again changes nothing. Making and revoking are recorded in the audit log.

## Check it worked

The key appears in the list, and a call with it works. After revoking, it is refused.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The key is refused | It was revoked, or it is mistyped. | Make a new one. |
| An action is refused | The key's level is too low, or the action is never allowed for keys. | Use a higher level, or do it signed in. |
| You lost the key | It is shown only once. | Revoke it and make a new one. |
