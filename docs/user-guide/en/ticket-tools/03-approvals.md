---
title: Ask for and give approval
slug: approvals
topic: ticket-tools
order: 3
summary: Ask a Supervisor or Admin to approve something, such as a refund, and decide requests waiting for you.
status: draft
updated: 2026-11-24
who: Agents ask; Supervisors and Admins decide
plan: All workspaces
routes:
  - GET /api/v1/tickets/{ticket_id}/approvals
  - POST /api/v1/tickets/{ticket_id}/approvals
  - GET /api/v1/approvals/waiting
  - POST /api/v1/approvals/{approval_id}/decide
related:
  - ticket-tools/tasks
---

Some actions, such as a refund, need a second person to agree. An approval request records who asked, who decided and why.

> **Who can do this:** Anyone who can reply can ask. Only a Supervisor, an Admin or a custom role with the approve right can be asked.

## Ask for approval

1. Open the ticket. Under **More**, find **APPROVALS** and select **Ask for approval**.
2. Choose the approver. You cannot choose yourself.
3. Write what needs approving. If you leave it empty and enter an amount, CeDesk writes it for you from the amount and the ticket's order ID.
4. For money, add the amount and a 3-letter currency code such as INR or USD. The default is INR.
5. Send.

## Decide a request

Open the request that is waiting for you and approve or reject it, with an optional note. The ticket gets a line such as "Approved by …".

- Only the named approver, or an Admin, can decide.
- You cannot decide your own request.
- A request is decided once.

A refund that the AI agent in a chat wants to make can arrive as a request with no named approver. Anyone who may approve can decide it, and the decision goes back to Cevanos, which then acts or not.

## Check it worked

The request shows as approved or rejected under **APPROVALS**, with who decided and when.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Ask someone else to approve it." | You chose yourself. | Pick another person. |
| "Write what needs approving." | No text and no amount. | Describe it. |
| "You can't decide your own request." | You asked for it. | Ask someone else to decide. |
| "Only … or an Admin can decide this." | You are not the named approver. | Ask the named person or an Admin. |
| "This request was already approved by …" | Someone decided first. | Nothing to do. |
