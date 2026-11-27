---
title: How automation works
slug: how-automation-works
topic: automation
order: 1
summary: What happens to a ticket on its own, and the order it happens in.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/automation/catalog
  - GET /api/v1/rules
related:
  - automation/rules
---

When a ticket arrives, several parts of CeDesk act on it before any agent looks. Knowing the order helps you see why a ticket ended up where it did.

> **Who can do this:** Admins set it all up. Agents see the results as grey lines on the ticket.

## What happens to a new ticket

1. **Triage** gives it a topic, a language and a sentiment, and can hide card numbers. See [Topics and card redaction](topics-and-card-redaction.md).
2. **Rules** run, from top to bottom. They can set the type, team, priority and tags. See [Rules](rules.md).
3. **Workflows** run right after the rules. They can wait and follow up. See [Workflows](workflows.md).
4. **Routing** picks an agent, if the ticket has a team and someone is free. See [Routing and agent availability](routing-and-agent-availability.md).
5. **The response-time clock** (SLA) starts. See [Response-time targets](sla-targets-and-policies.md).

Later, **auto-close** resolves Pending tickets when the customer stops replying. See [Auto-close](auto-close.md).

## When rules run

A rule starts on one of four events: a ticket is created, the customer replies, a ticket's response-time clock runs out, or a snooze ends. **An agent's own edits never run rules**, so an agent can always change what a rule did.

Every change an automation makes writes a grey line on the ticket, such as "Rule applied: Order tracking to Shipping".

## Check it worked

Open a new ticket made by a test customer. Its history shows which rule applied, and the team and priority it set.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A rule did not run | The event did not happen, a condition did not match, or the rule is off. | Use **Test on a ticket**. See [Test automation](test-automation.md). |
| A ticket was left unassigned | Nobody on the team was free. The line on the ticket says who was skipped and why. | Check [Routing and agent availability](routing-and-agent-availability.md). |
