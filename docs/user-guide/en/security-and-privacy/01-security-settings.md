---
title: Security settings
slug: security-settings
topic: security-and-privacy
order: 1
summary: Password rules, session length, two-factor sign-in, an IP allowlist and your security score.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/security-settings
  - PUT /api/v1/security-settings
  - POST /api/v1/security/test-my-ip
  - GET /api/v1/security/score
related:
  - security-and-privacy/single-sign-on
---

These settings decide how people sign in and what protects your workspace. They apply at once when you save.

> **Who can do this:** Admins.

## The security score

Go to **Settings › Security & data › Security**. At the top is a **Security score** out of 100, with a checklist: Single sign-on, Two-factor sign-in for everyone, IP allowlist, Strong password rules, Sensitive data hidden, Short sign-in sessions, Files need sign-in, Email Admins about security events and Customer portal limited to your domains. It updates when you save.

## The settings

| Setting | What it does |
|---------|--------------|
| Minimum password length | From 8 to 64 characters. |
| Password expires | Never, or after 90 or 180 days. An expired password must be changed at the next sign-in. |
| Sign people out after | An hour without use, or 8 hours, 12 hours or 7 days. A shorter setting also ends longer sessions already open. |
| Require two-factor sign-in | Everyone must use a second step. People without it can only set it up until they do. |
| Files need sign-in | Attachments open only for signed-in people, not for anyone holding the link. |
| Email Admins about security events | Admins get an email when a member is added, deactivated or reactivated, the IP list or the two-factor rule changes, or a member's two-factor is reset. |
| IP allowlist | Only these addresses can sign in. |

Select **Save**.

## Before you turn on two-factor

Turn it on for yourself first. If someone loses their second step, an Admin can reset it under **Settings › Workspace › Members** with **Reset 2FA**.

## The IP allowlist

Type one address or range per line, up to 100. Empty allows everyone. The list is checked at sign-in and on every request. **It must include your own address, or you lock yourself out.** Select **Test my IP** to see whether your address is on the list before you save.

## Check it worked

The score goes up after saving, and the "Last changed by" line shows your name.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Someone is told their password expired | The expiry setting is on. | They choose a new password at sign-in. |
| Someone can only set up two-factor | The requirement is on and they have none. | They finish setup. |
| Everyone is refused | The IP list does not include their address. | Fix the list from an allowed address, or ask whoever runs your server. |
