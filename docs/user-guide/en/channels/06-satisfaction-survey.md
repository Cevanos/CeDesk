---
title: The satisfaction survey (CSAT)
slug: satisfaction-survey
topic: channels
order: 6
summary: Ask customers to rate the help from 1 to 5 after a ticket is resolved.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/portal-settings
  - PUT /api/v1/portal-settings
  - POST /api/v1/portal/tickets/{ticket_id}/csat
  - GET /api/v1/public/csat/{token}
  - POST /api/v1/public/csat/{token}
related:
  - channels/customer-portal
---

When a ticket is resolved, CeDesk can email the customer and ask them to rate the help.

> **Who can do this:** Admins turn it on. Customers answer.

## Turn it on

1. Go to **Settings › Customers › Portal**.
2. Under **Features**, turn on **CSAT survey**.
3. Select **Save changes**.

## What the customer sees

The email asks them to rate from 1 to 5. They can select a star in the email, which opens a rating page, or rate on the ticket in the portal. They can change the rating within 30 days. Each rating adds a line to the ticket for your agents.

Tickets that are merged send no survey.

## Check it worked

Resolve a test ticket whose customer is your own email. The survey email arrives, and after you rate it, the rating shows on the ticket.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No survey email | **CSAT survey** is off, or the server cannot send email. | Turn it on, and check Failed events. |
| A rating cannot be changed | More than 30 days have passed. | Nothing to do. |
