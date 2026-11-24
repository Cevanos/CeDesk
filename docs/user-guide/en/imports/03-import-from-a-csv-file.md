---
title: Import tickets from a CSV file
slug: import-tickets-from-a-csv-file
topic: imports
order: 3
summary: Bring tickets in from a spreadsheet, with one row per message.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - POST /api/v1/imports/csv
related:
  - imports/plan-your-move
---

Use a CSV file when your old help desk is not on the list, or when you only have an export. To import customers alone, see "Import customers from a CSV file" in Customers.

> **Who can do this:** Admins.

## Prepare the file

Save it as UTF-8 CSV, up to 25 MB. Each row is one message. Rows with the same `ticket_id` form one ticket.

Required columns:

| Column | What it holds |
|--------|---------------|
| `ticket_id` | Any value that groups messages of one ticket. |
| `subject` | The ticket's subject. |
| `requester_email` | The customer's email. |
| `message_from` | customer, agent or note. |
| `message_body` | The text. |
| `message_created_at` | When it was written, in ISO 8601, such as 2026-11-24T10:30:00Z. |

Optional columns: `status`, `priority`, `channel`, `team`, `tags` (separate with `;`), `ticket_created_at`, `resolved_at`, `requester_name`, `assignee_email`, `csat_score`, `csat_comment`, `message_id`, `author_name`, `author_email`, and `field.<key>` for a ticket field.

## Import

1. Go to **Settings › Channels › Imports** and select **New import**, then **CSV file**.
2. Upload the file.
3. Run a dry run, check it, then import.

Rows that cannot be read are listed as warnings and left out. They never stop the import. The file is deleted when the run ends. Sync is not possible for a CSV file.

## Check it worked

Tickets from the file appear in **Tickets**, tagged as imported from CSV. You can filter by that.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The file is too big | It is over 25 MB. | Split it into several files. |
| Rows are left out | A required column is empty or a date is not ISO 8601. | Fix those rows and import again. |
| Tickets are unassigned | The `assignee_email` is not a member. | Invite them first. |
