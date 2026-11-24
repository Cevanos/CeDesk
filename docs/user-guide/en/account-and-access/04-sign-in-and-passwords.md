---
title: Sign in and passwords
slug: sign-in-and-passwords
topic: account-and-access
order: 4
summary: Sign in, change your password and recover one you forgot.
status: draft
updated: 2026-11-24
who: Everyone
plan: All workspaces
routes:
  - POST /api/v1/auth/sign-in
  - POST /api/v1/auth/forgot-password
  - POST /api/v1/auth/reset-password
  - POST /api/v1/me/password
related:
  - account-and-access/two-step-sign-in
---

You sign in with your workspace address, your email and your password. If your Admin turned on single sign-on, use **Sign in with SSO** instead.

> **Who can do this:** Everyone.

## Sign in

Open CeDesk, type the workspace address, your email and your password. If two-step sign-in is on for you, type the code from your app next.

## Change your password

1. Open the account menu and select **Change password**, or go to **My account**.
2. Type your **Current password**, the **New password** and confirm it.
3. Select **Update password**.

You stay signed in here. Your other devices are signed out.

## Forgot your password

On the sign-in page, ask for a link to choose a new password. The email link lasts 30 minutes, and at most 3 are sent an hour. CeDesk answers the same way whether or not the email exists, so nobody can use it to find accounts. Choosing a new password signs you out everywhere. If you cannot get the email, an Admin can set a new password for you.

## Check it worked

You land on **Tickets**.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The details did not match | A wrong address, email or password. CeDesk gives one answer for every reason. | Check all three. |
| You are locked after too many wrong passwords | Too many tries. | Wait, or ask an Admin to reset your password. |
| Sign-in is refused from this address | Your workspace allows only certain IP addresses. | Ask an Admin. |
| You must change your password | It expired. | Choose a new one. |
| The reset link is invalid | It was used or expired. | Ask for a new one. |
| Too many attempts | Too many from this address. | Wait and try again. |
