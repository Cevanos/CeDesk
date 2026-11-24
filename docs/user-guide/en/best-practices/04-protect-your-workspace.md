---
title: Protect your workspace
slug: protect-your-workspace
topic: best-practices
order: 4
summary: A short list of security habits that matter most, in the order to do them.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes: []
related:
  - security-and-privacy/security-settings
  - account-and-access/api-keys
---

Support desks hold customer data, so protect it. Do these in order.

> **Who can do this:** Admins.

## 1. Two-step sign-in for everyone

Turn it on for yourself first, then require it. See [Security settings](../security-and-privacy/security-settings.md) and [Two-step sign-in](../account-and-access/two-step-sign-in.md). Keep recovery codes safe.

## 2. Single sign-on, if you have an identity provider

[Single sign-on](../security-and-privacy/single-sign-on.md) means people leave your company and lose access in one place. Sign in with it yourself before you turn on **Only allow SSO**.

## 3. Give the lowest role that works

Most people are Agents. Make a custom role for people who need only reports or only settings. See [Roles and rights](../account-and-access/roles-and-rights.md). Deactivate people who leave the same day, under [Members](../account-and-access/add-and-manage-members.md).

## 4. One API key per system

Make a key for each system, with the lowest level that works, and revoke it when the system goes. See [API keys](../account-and-access/api-keys.md).

## 5. Do not keep what you do not need

Set [retention](../security-and-privacy/retention.md) so old resolved tickets go. Turn on card number hiding under [Topics and card redaction](../automation/topics-and-card-redaction.md).

## 6. Watch the log

Read the [audit log](../security-and-privacy/audit-log.md) now and then, and turn on the email to Admins about security events.

## Take care with the IP allowlist

It must include your own address, or you lock yourself out. Use **Test my IP** first.

## Check it worked

Your security score goes up, and only the people you expect appear in the audit log.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Everyone is locked out | The IP list is wrong. | Fix it from an allowed address, or ask whoever runs your server. |
| Someone lost their phone | They have no second step. | Reset their two-step sign-in under Members. |
