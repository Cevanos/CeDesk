---
title: The AI report
slug: ai-report
topic: reports
order: 9
summary: How often the AI agent hands over, how many chats it resolves, and how customers rate its answers.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: Workspaces connected to Cevanos
routes:
  - GET /api/v1/reports/ai
related:
  - reports/reports-overview
---

When your workspace is connected to Cevanos, the AI page shows how its AI agent is doing.

> **Who can do this:** Supervisors and Admins, when Cevanos is connected.

## What it shows

Open **Reports › AI**. The page shows the handover rate (how often the AI passes a chat to a person), how many chats the AI resolved, and the satisfaction rating of the AI's answers. It covers the last 30 days unless you choose another period, for the whole workspace. The numbers are read live from Cevanos and are not stored in CeDesk.

## Check it worked

The page shows numbers for the period.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| It says Cevanos is not connected | The connection is missing. | An Admin connects it in Settings. |
| It says a permission is missing | The key lacks a scope. The message names it. | An Admin fixes the key. |
| It is slow or unavailable | Cevanos is down. | Try again. |
