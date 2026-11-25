---
title: Contact fields
slug: contact-fields
topic: customers
order: 3
summary: Define extra details to keep on every customer, such as a plan or an account number.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/contact-fields
  - POST /api/v1/contact-fields
  - PATCH /api/v1/contact-fields/{field_id}
  - DELETE /api/v1/contact-fields/{field_id}
related:
  - customers/segments
---

A contact field is an extra detail kept on every customer. Agents fill in the values on the customer page, and you can filter, import and export by them.

> **Who can do this:** Admins define fields. Agents fill in values.

## Add a field

1. Go to **Settings › Customers › Contact fields** and select **Add field**.
2. Type a **Label**, such as "Plan".
3. Choose a **Kind**:

| Kind | What it holds |
|------|---------------|
| Text | Up to 2,000 characters. |
| Number | A number. |
| Date | A day. |
| Select | One choice from a list of 1 to 50 options you type. |
| Checkbox | Yes or no. |

4. Check the **Key**. It is how the field is stored: lowercase letters, digits and underscores, starting with a letter. You use it in imports and exports.
5. Save.

You can have up to 50 fields. The key and kind never change after you save. Later you can rename the label, change the options and reorder.

## Delete a field

**Delete** removes the field and every customer's value of it. If a segment filters on it, the delete is refused.

## Check it worked

Open any customer. The new field appears under **ATTRIBUTES**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The key is taken | Another field has it. | Choose a different key. |
| The field cannot be deleted | A segment filters on it. | Change or delete the segment first. |
| A field limit message | You have 50 fields. | Delete one you no longer use. |
