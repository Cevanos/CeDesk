---
title: Set up your desk well
slug: set-up-your-desk-well
topic: best-practices
order: 1
summary: The order that gets a new support desk working with the least rework.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes: []
related:
  - getting-started/first-day-checklist
  - automation/how-automation-works
---

A support desk that is set up in the right order needs fewer fixes later. This is the order we suggest.

> **Who can do this:** Admins.

## 1. People before rules

- Add your team and give each person the lowest role that lets them do their job. Most people are Agents. See [Add and manage members](../account-and-access/add-and-manage-members.md) and [Roles and rights](../account-and-access/roles-and-rights.md).
- Make a team for each group that owns a kind of ticket, such as Billing. Rules and routing need teams. See [Teams](../account-and-access/teams.md).

## 2. Email next

Add your support address and check forwarding and DNS before you tell customers about it. Mail that fails here is mail you never see. See [Support email addresses](../channels/support-email-addresses.md) and [Check email forwarding and DNS](../channels/check-email-forwarding-and-dns.md).

## 3. Then ticket types and fields

Define the types you handle and only the fields an agent really needs to fill in. Every required field is a question an agent must answer on every ticket. See [Ticket types and their fields](../ticket-tools/ticket-types-and-fields.md).

## 4. Then rules, tested first

Start with a few rules that route by obvious words and by channel. Test each on a real ticket before you turn it on. See [Rules](../automation/rules.md) and [Test rules and workflows](../automation/test-automation.md). Use the sandbox to try bigger changes, and deploy when they look right.

## 5. Then targets that match reality

Set response-time targets you can meet, and business hours that match when your team works. A target nobody can meet only produces red numbers. See [Response-time targets](../automation/sla-targets-and-policies.md).

## 6. Last, tidy up

Remove demo tickets, switch on only the automations you trust, and read your [security score](../security-and-privacy/security-settings.md).

## Check it worked

A test email to your support address becomes a ticket, lands with the right team and shows a response-time clock.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Tickets land with no team | No rule or default sets one. | Add a rule that sets a team. |
| Nobody is assigned | Nobody on the team is Available. | See [Routing and agent availability](../automation/routing-and-agent-availability.md). |
