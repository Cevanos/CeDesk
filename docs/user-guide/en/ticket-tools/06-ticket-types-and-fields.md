---
title: Ticket types and their fields
slug: ticket-types-and-fields
topic: ticket-tools
order: 6
summary: Define the kinds of requests you handle and the extra fields each one asks for.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/ticket-types
  - POST /api/v1/ticket-types
  - PATCH /api/v1/ticket-types/{type_id}
  - PUT /api/v1/ticket-types/order
  - DELETE /api/v1/ticket-types/{type_id}
related:
  - ticket-tools/approvals
---

A ticket type says what kind of request a ticket is, for example Refund or Shipping issue. Each type can ask the agent for its own fields, such as an order ID.

> **Who can do this:** Admins add and change types. Everyone sees them on tickets.

## Add a type

1. Go to **Settings › Workspace › Ticket types** and select **Add type**.
2. Name it.
3. Add fields, up to 30. A field is text, a number or a choice from a list of up to 50 options. Mark it required if agents must fill it in.
4. Optionally set a required skill, so routing sends these tickets to agents who have it.
5. Choose whether **Customers can pick** it in the portal and web form.
6. Save.

The list shows each type with its field count, its skill and whether customers can pick it. Use **Edit** and **Delete**, and drag to reorder.

## Change a type

You can rename it, add, remove, rename or reorder its fields, change its skill and hide it from customers. A field's key, which is how it is stored on tickets, does not change.

## Delete a type

A deleted type's tickets keep everything else; only the type and its fields are lost.

## Check it worked

Open a ticket and set its **Type**. Its fields appear under a heading with the type's name.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| A field value is refused | It breaks the field's rules, for example text over 2,000 characters. | Correct it. |
| Customers cannot see a type | **Customers can pick** is off. | Turn it on. |
