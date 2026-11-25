---
title: Import customers from a CSV file
slug: import-customers-from-a-csv-file
topic: customers
order: 4
summary: Bring in a list of customers, with a dry run first so nothing is changed by mistake.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/contacts/import
related:
  - customers/contact-fields
---

Use this to add customers you already have in a spreadsheet. To bring in tickets from another help desk, use the import tools under **Settings › Channels › Imports** instead.

> **Who can do this:** Admins. Every import is recorded in the audit log.

## Prepare the file

- A CSV file saved as UTF-8, with a header row.
- At most 2 MB and 5,000 rows.
- One column for the email. It is the only required column. You can also have name, phone, organization, VIP (true or false, yes or no, 1 or 0) and one column for each contact field.

## Import

1. On **Contacts**, select **Import CSV** and choose the file.
2. Check the column mapping. By default columns are matched by their names.
3. Choose what to do with customers who already exist, matched by email, including addresses merged into them:

| Choice | What happens |
|--------|--------------|
| Fill blanks (the default) | Sets only what the customer has no value for. |
| Overwrite | Sets every value the row has. An empty cell never clears a value. |
| Skip | Leaves existing customers alone. |

4. Run it as a **dry run** first. It writes nothing and shows what would happen. Rows that cannot be read are listed by line number and left out.
5. If it looks right, run it for real.

The whole file is written at once or not at all.

## Check it worked

The new customers appear in **Contacts**, and the counts match the dry run.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The file is too big | Over 2 MB or 5,000 rows. | Split it. |
| A row is left out | It could not be read, for example a bad email. | Fix that line and import again. |
| It says a contact was added meanwhile | Someone added a customer during the import. | Run it again. |
