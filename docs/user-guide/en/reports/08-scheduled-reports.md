---
title: Scheduled reports
slug: scheduled-reports
topic: reports
order: 8
summary: Email a report to your team every day, week or month.
status: draft
updated: 2026-11-24
who: Supervisors and Admins
plan: All workspaces
routes:
  - GET /api/v1/report-schedules
  - POST /api/v1/report-schedules
  - PATCH /api/v1/report-schedules/{schedule_id}
  - DELETE /api/v1/report-schedules/{schedule_id}
  - POST /api/v1/report-schedules/{schedule_id}/leave
related:
  - reports/build-and-save-a-report
---

A scheduled report is emailed on a timetable. Each email has the numbers and a link to the report here.

> **Who can do this:** Supervisors and Admins with the reports right. Recipients need that right too.

## Make a schedule

1. Open **Reports › Scheduled reports** and select **New schedule**.
2. Name it and choose the report: a ready-made one or one you saved.
3. Choose the period each email covers: the last 7, 30, 90 or 365 days, this month or last month.
4. Choose when: daily, weekly on a weekday, or monthly on a day from 1 to 28, at an hour in your workspace's time zone.
5. Choose recipients. Up to 10, who must hold the reports right and see at least as many teams as you do.
6. Save.

The list shows each schedule's report, timing, recipients, next run, last sent and owner, with an on or off switch. You can keep 20 schedules in a workspace.

## Stop getting one

A recipient who does not want it selects **Leave**. The owner, or an Admin, can edit or delete a schedule.

## Good to know

Each time, the report is worked out with the owner's rights, and every recipient is checked again. Someone who lost the right gets nothing. If the owner loses the right, the schedule switches off.

## Check it worked

**Next run** shows the next time, and **Last sent** fills in after the first email.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| No email arrived | The schedule is off, the owner lost the right, or email sending failed. | Check the switch and Failed events. |
| A recipient is refused | They lack the reports right or see fewer teams. | Pick someone else. |
| You cannot edit it | You are not its owner or an Admin. | Ask the owner. |
