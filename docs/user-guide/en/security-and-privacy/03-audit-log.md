---
title: The audit log
slug: audit-log
topic: security-and-privacy
order: 3
summary: A record of who changed what and when, with a check that it has not been tampered with.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/audit
  - POST /api/v1/audit/verify
  - GET /api/v1/audit/export.csv
related:
  - security-and-privacy/security-settings
---

The audit log records changes to your workspace: settings, members, campaigns, exports and erasures. It tells you who did something, from which address, and when.

> **Who can do this:** Admins.

## Read it

Go to **Settings › Security & data › Audit log**. Entries are listed newest first. Each shows the action, such as changed or created, what it was done to, who did it and from which IP address, the area, and the time. Use **Area** to show one area only, such as campaigns or email.

Secrets are shown as hidden. For a change, the entry records the before and after values of each field. Some entries, such as a customer note, record that something happened but never its words.

## Keep a copy

**Download CSV** saves the log, oldest first.

## Verify it

Entries form a numbered chain. **Verify chain** checks that the chain is unbroken, so you can be sure nothing was removed or altered.

## Check it worked

Change a setting. A new entry with your name appears at the top.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A change is not listed | It is not an audited action. | Not every action is audited. |
| An entry has no person | It was made on the server, not by a member. | Check with whoever runs your server. |
