---
title: Business hours and holidays
slug: business-hours-and-holidays
topic: automation
order: 6
summary: Tell the SLA clock when your team works, and which days it stops.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - PUT /api/v1/sla/calendar
  - POST /api/v1/holidays
  - PATCH /api/v1/holidays/{holiday_id}
  - DELETE /api/v1/holidays/{holiday_id}
related:
  - automation/sla-targets-and-policies
---

The response-time clock pauses outside your working hours and on holidays, so nobody is blamed for a night.

> **Who can do this:** Admins.

## Set business hours

1. Go to **Settings › Service › SLA** and find **Business hours and holidays**.
2. Either choose **Open 24/7**, where the clock never pauses, or pick the **Time zone**, the **Open days** (Mon to Sun), and when it **Opens** and **Closes**.
3. Select **Save business hours**.

The hours are the same every working day, and closing must be after opening, so a night shift is not possible.

## Add holidays

Select **Add holiday**, pick the day and name it, for example "Diwali". The clock does not run that day. You can rename a holiday, move it to another day or remove it.

## Check it worked

A ticket opened outside hours shows its clock paused, and its due time moves to the next opening.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The time zone is refused | It is not a known time zone name. | Use one such as Asia/Kolkata. |
| Closing is refused | It is not after opening. | Pick a later closing time. |
