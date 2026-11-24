---
title: Response-time targets (SLA)
slug: sla-targets-and-policies
topic: automation
order: 5
summary: Set how fast tickets must get a first reply and be resolved, by priority, with policies for special cases.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/sla
  - PUT /api/v1/sla/targets
  - PUT /api/v1/sla/policies
related:
  - automation/business-hours-and-holidays
---

An SLA is the time you promise to reply and to resolve. CeDesk shows the time left on each ticket and warns you before and when it runs out.

> **Who can do this:** Admins.

## Targets by priority

Go to **Settings › Service › SLA**. Under **Targets by priority** you see, for each priority, the time to first reply and the time to resolution, counted in business minutes. The built-in defaults are:

| Priority | First reply | Resolution |
|----------|-------------|------------|
| Urgent | 15 minutes | 4 hours |
| High | 1 hour | 8 hours |
| Medium | 4 hours | 1 day |
| Low | 8 hours | 3 days |

Change the numbers and select **Save targets**. The first reply cannot be due after the resolution. Saving works out every open ticket's clock again.

## Policies

Under **Policies**, the first matching policy applies, from top to bottom. A policy can match tickets by conditions, such as channel or priority, and set its own targets or use the priority targets. The last policy, **Default**, catches every ticket the others do not, so it stays at the bottom. Use **Add policy**, **Edit**, **Delete**, or **Reset to built-in**.

## Check it worked

Open a ticket. Its **SLA** panel shows the targets, due times and the policy name, for example "Policy: Default".

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Saving is refused | A name repeats, a target is out of range, the last policy has a condition or is off, or another policy matches every ticket. | Fix the list and save again. |
| The first reply is due after the resolution | The numbers are the wrong way round. | Make the first reply shorter. |
| A ticket shows no SLA | No policy matched. | Make sure the last policy catches everything. |
