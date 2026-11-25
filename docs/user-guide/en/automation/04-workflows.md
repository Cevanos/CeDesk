---
title: Workflows
slug: workflows
topic: automation
order: 4
summary: Chains of steps that can wait, branch and follow up on their own, with versions you can restore.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/flows
  - POST /api/v1/flows
  - PATCH /api/v1/flows/{flow_id}
  - DELETE /api/v1/flows/{flow_id}
  - GET /api/v1/flows/{flow_id}/versions
  - POST /api/v1/flows/{flow_id}/versions/{version}/restore
related:
  - automation/test-automation
---

A workflow is a chain of steps that can wait. For example: when a VIP writes in, tag the ticket, wait a day for them to reply, then raise the priority.

> **Who can do this:** Admins.

## How it runs

A workflow starts on its trigger, right after the rules. It goes from step to step until it waits or ends. A waiting run carries on when the time passes or, if you choose, when the customer replies. Every change writes a line on the ticket.

## Build one

1. Go to **Settings › Service › Workflows** and select **New workflow**.
2. Draw the steps in the builder. The kinds of step are:

| Step | Does |
|------|------|
| Trigger | The event that starts it. One per workflow. |
| Condition | Checks the ticket, then goes the "yes" or the "no" way. |
| Action | Does one thing, like a rule's action. |
| Wait | Waits from 1 minute up to 30 days, optionally until the customer replies, with a path for "replied" and one for "timed out". |

3. Save. A new workflow starts **turned off**. Turn it on when you are ready.

The builder checks the shape as you draw: one trigger, every step reachable, no loop without a wait, at most 50 steps and 100 connections.

## Versions

Each change to the steps saves a new version. Open the version history to go back to an older one. It is saved as a new version, so history is never lost. Runs already waiting carry on with the version they started on.

## Stop a run, or delete

A waiting run can be stopped from the **Automation log**. Deleting a workflow cancels its waiting runs.

## Safety limits

A workflow has a limit of runs per hour (1,000 by default) and per day (10,000 by default), across all tickets. Runs over the limit are blocked and logged.

## Check it worked

Use **Test** on a ticket, then turn it on and watch the **Automation log**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The workflow does not run | It is off. | Turn it on. |
| It cannot be saved | The shape breaks a rule, for example a loop with no wait. | Read the message. |
| Runs are blocked | The hourly or daily limit was reached. | Raise the limit or fix what triggers so many runs. |
| A problem is shown | A team or other item it uses was deleted. | Edit the step. |
