---
title: How to route refund requests to a team
slug: route-refund-requests
topic: how-to
order: 2
summary: Make tickets that mention a refund go to Billing, with a higher priority.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes: []
related:
  - automation/rules
---

> **Who can do this:** Admins.

1. Make sure you have a **Billing** team under **Settings › Workspace › Teams**.
2. Go to **Settings › Service › Rules** and select **Add rule**.
3. Set it to run when a ticket is created.
4. Add the condition "Subject or customer message contains" and the words refund and money back.
5. Add the actions "Set team" to Billing and "Set priority" to High.
6. Save.
7. Open **More actions** on the rule, choose **Test on a ticket**, and try it on a real refund ticket.
8. Turn the rule on with the **Enabled** switch.

**Check it worked:** a new ticket that mentions a refund shows "Rule applied" and is in Billing.

More: [Rules](../automation/rules.md), [Test rules and workflows](../automation/test-automation.md).
