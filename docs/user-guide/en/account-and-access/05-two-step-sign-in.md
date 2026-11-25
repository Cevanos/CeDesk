---
title: Two-step sign-in
slug: two-step-sign-in
topic: account-and-access
order: 5
summary: Add a code from an authenticator app to your sign-in, and what to do if you lose your phone.
status: draft
updated: 2026-11-24
who: Everyone
plan: All workspaces
routes:
  - GET /api/v1/me/2fa
  - POST /api/v1/me/2fa/setup
  - POST /api/v1/me/2fa/confirm
  - POST /api/v1/me/2fa/recovery-codes
  - POST /api/v1/me/2fa/disable
  - POST /api/v1/auth/2fa
related:
  - account-and-access/sign-in-and-passwords
---

Two-step sign-in asks for a code from an authenticator app on your phone, as well as your password. It protects your account if your password leaks.

> **Who can do this:** Everyone, for their own account. An Admin may require it for everyone.

## Turn it on

1. Open the account menu, select **My account**, and find **Two-step sign-in**.
2. Select **Set up two-step sign-in**.
3. Scan what it shows with an authenticator app.
4. Type the first code the app gives.

CeDesk then shows recovery codes. **Save them somewhere safe.** Each works once if you lose your phone. Setting up signs out your other sessions.

## Sign in with it

After your password, type the code from the app, or a recovery code. Ten wrong codes lock you out for a while.

## Recovery codes and turning it off

You can ask for new recovery codes with a code from your app. The old ones stop working. You can turn it off yourself, unless your workspace requires it.

## If you lose your phone

Use a recovery code. If you have none, ask an Admin to use **Reset 2FA** under **Settings › Workspace › Members**. That turns it off, signs you out everywhere and emails the Admins. If your workspace requires it, you set it up again at your next sign-in.

## Check it worked

**Two-step sign-in** shows On, and your next sign-in asks for a code.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| The code is wrong | The phone's clock is off, or the code expired. | Check the phone's time and try the new code. |
| You cannot turn it off | Your workspace requires it. | Ask an Admin. |
| You can only set it up | The workspace now requires it. | Finish setup. |
