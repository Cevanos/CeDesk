---
title: Find customers
slug: find-customers
topic: customers
order: 1
summary: Search and filter the Contacts list, and export it to a file.
status: draft
updated: 2026-11-24
who: Everyone who works tickets; Admins export
plan: All workspaces
routes:
  - GET /api/v1/contacts
  - GET /api/v1/contacts/export
related:
  - customers/the-customer-page
---

**Contacts** lists everyone who has written in, with their tickets and history in one place.

> **Who can do this:** Everyone who works tickets can search. Only Admins can export.

## Search and filter

The top of the page shows four numbers for the list in view: contacts, VIPs, contacts with open tickets, and open tickets.

Use the search box to find a customer by part of their name, email, phone or organization. It also finds the email addresses of customers merged into them. Capital letters do not matter.

Narrow the list with:

| Control | What it shows |
|---------|---------------|
| VIP only | Only customers marked VIP. |
| Has open tickets | Customers with a ticket that is not resolved. |
| Created from, to | Customers made between two dates. |
| Contact field | Customers whose custom field has a value you choose. See [Contact fields](contact-fields.md). |

Sort by name A to Z, by latest ticket activity, or by most tickets. Erased customers are never listed.

To keep a set of filters, see [Segments](segments.md).

## Export to a file

Select **Export CSV**. You get one row per customer with their email, name, phone, organization, VIP flag, dates, ticket counts, other email addresses and each custom field. The file uses the same filters as the list, so filter first to export part of it. It holds at most 50,000 customers; with more, add filters. Each export is recorded in the audit log.

## Check it worked

The rows match your search and filters, and the numbers at the top change with them.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A customer is missing | They were erased, or a filter is on. | Clear the filters. |
| **Export CSV** is refused | You are not an Admin. | Ask an Admin. |
| The export says too many match | Over 50,000 customers. | Add filters and export in parts. |
