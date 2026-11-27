---
title: Rules
slug: rules
topic: automation
order: 2
summary: If a ticket matches these conditions, do these actions, such as route refunds to Billing.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/rules
  - POST /api/v1/rules
  - PATCH /api/v1/rules/{rule_id}
  - PUT /api/v1/rules/order
  - DELETE /api/v1/rules/{rule_id}
related:
  - automation/test-automation
---

A rule says: when this happens, and the ticket matches these conditions, do these actions. For steps that need to wait, use [Workflows](workflows.md).

> **Who can do this:** Admins.

## See your rules

Go to **Settings › Service › Rules**. Each rule shows its name, whether it is on, and a line such as "When a ticket is created · Subject or customer message contains: refund → Set ticket type, Set team, Set priority: High".

## Add a rule

1. Select **Add rule** and name it.
2. Choose when it runs:

| When | Runs |
|------|------|
| A ticket is created | For every new ticket. |
| The customer replies | When the customer writes on an Open or Pending ticket. |
| Its SLA is breached | When an open ticket's response-time clock runs out. Checked once a minute, once per clock. |
| A snooze ends | When a snoozed ticket wakes up. |

3. Add conditions. **All** must match.

| Condition | Checks |
|-----------|--------|
| Subject or customer message contains | Any of the words you list, any case. |
| Channel is | Email, chat, web and so on. |
| Sent to an address containing | Part of the support address the customer wrote to. |
| Status is | Open, Pending or Resolved. |
| Sentiment is | Negative, neutral or positive. |
| SLA state is | On track, close, breached or met. |
| Contact field | A customer's field equals, contains, is more or less than a value, or is set. |
| Customer is VIP | Yes or no. |
| Customer is in a segment | Or is not. |
| Time since | More than a number of minutes since the customer's or an agent's last reply. |
| Snoozed, Has followers | Yes or no. |

4. Add actions. They run in order.

| Action | Does |
|--------|------|
| Set ticket type | Changes the type, optionally only if empty. |
| Set team | Changes the team, which re-routes the ticket. |
| Set priority | Changes the priority, optionally keeping Urgent. |
| Set status | Open, Pending or Resolved. |
| Add tag | Adds a tag. |
| Snooze | For a number of minutes. |
| Add follower | Adds a member as follower. |
| Send email | Replies to the customer. Placeholders such as `{{customer.first_name}}` work. |
| Call a webhook | Sends the event to a webhook. |

5. Save.

## Order, on and off, and delete

Rules run top to bottom, and the order matters: use the up and down arrows next to a rule. The **Enabled** switch turns a rule on or off. **Delete** removes it.

A rule that points at something deleted, such as a team, shows a problem you need to fix.

## Check it worked

Make a test ticket that matches. Its history shows "Rule applied: …".

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The rule did not apply | Agent edits never run rules, or a condition did not match. | Use **Test on a ticket**. |
| A problem is shown on the rule | A team, type or webhook it uses was deleted. | Edit the rule and choose another. |
| Two rules fight | They run in order, and the later one wins. | Reorder them. |
