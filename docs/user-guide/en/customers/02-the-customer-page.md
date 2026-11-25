---
title: The customer page
slug: the-customer-page
topic: customers
order: 2
summary: A customer's details, VIP flag, private notes, tickets and activity in one place.
status: draft
updated: 2026-11-24
who: Agents, Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/contacts/{contact_id}
  - PATCH /api/v1/contacts/{contact_id}/profile
  - PUT /api/v1/contacts/{contact_id}/attributes
  - GET /api/v1/contacts/{contact_id}/notes
  - POST /api/v1/contacts/{contact_id}/notes
  - GET /api/v1/contacts/{contact_id}/timeline
  - GET /api/v1/contacts/{contact_id}/tickets
related:
  - customers/contact-fields
---

Select a customer in **Contacts**, or **View customer →** on a ticket, to open their page.

> **Who can do this:** Everyone who works tickets can read it and add notes, including Light agents. Changing the name, VIP flag and details needs the right to change tickets.

## What is on the page

- **The header** has the customer's name and email. **Edit name** changes the name. **Mark as VIP** flags an important customer.
- **ATTRIBUTES** are the extra details your Admin defined, such as a plan. Type a value and select **Save**. Each value is checked against its kind: text (up to 2,000 characters), number, date, one choice from a list, or a checkbox.
- **NOTES** are private notes about the customer. Only your team sees them. Type one and select **Save**.
- The totals show **TOTAL TICKETS**, **ACTIVE**, **RESOLVED**, **SATISFACTION** and **CUSTOMER SINCE**.
- **Tickets** lists all their tickets, newest first, including archived ones. **Activity** shows everything that happened: tickets created and resolved, public messages, ratings, notes and merges. It never shows private notes on tickets or system lines.

A role that sees one team sees only that team's tickets here.

## Edit or delete a note

The author of a note, or an Admin, can change or delete it. Changes are recorded in the audit log, but not the words of the note.

## Check it worked

Your note appears under **NOTES**, and a changed value shows on the page.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| You cannot edit a note | It is someone else's. | Ask the author or an Admin. |
| A value is refused | It does not match the field's kind, such as text in a number field. | Enter a value of the right kind. |
| The customer shows as erased | Their data was erased on request. | Nothing can be changed. |
