---
title: Test rules and workflows
slug: test-automation
topic: automation
order: 3
summary: Try a rule or workflow on a ticket, or on past tickets, without changing or sending anything, and read the run log.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/rules/{rule_id}/test
  - POST /api/v1/rules/replay
  - POST /api/v1/flows/{flow_id}/test
  - GET /api/v1/automation/runs
  - GET /api/v1/replays/{replay_id}
related:
  - automation/rules
---

Before you trust a rule, see what it would do. Test mode changes nothing and sends nothing.

> **Who can do this:** Admins.

## Test on a ticket

1. Go to **Settings › Service › Rules**.
2. Open **More actions** next to the rule and choose **Test on a ticket**.
3. Pick a ticket.

You see whether the conditions match and what the actions would do. You can also test a rule you have not saved yet while you build it, and test a workflow the same way.

## Replay on past tickets

A replay tries a rule or a workflow version on your newest tickets, and counts what would have happened. It also changes and sends nothing. Only one replay runs at a time; start another after it finishes, or stop it and keep what it found so far. A replay of a workflow walks each ticket up to the first wait. Replay is also available through the API, described in the developer docs.

## Read the run log

Go to **Settings › Service › Automation log**. It lists runs, newest first, for 30 days. Open a rule's **Run log** from its **More actions** menu to see only that rule's runs. You can stop a workflow run that is waiting.

## Check it worked

The test shows each condition as matched or not and lists the actions. Nothing on the ticket changed.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A replay is refused | Another replay is queued or running. | Wait, or stop it. |
| The log is empty | Nothing ran in the last 30 days. | Create a ticket that matches. |
