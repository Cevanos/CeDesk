---
title: Create your workspace
slug: create-your-workspace
topic: getting-started
order: 2
summary: Sign up with your email, enter the six-digit code and become the first Admin.
status: draft
updated: 2026-11-24
who: Whoever starts the company's account
plan: Cloud workspaces, where sign-up is turned on
routes:
  - GET /api/v1/auth/signup-config
  - POST /api/v1/auth/signup
  - POST /api/v1/auth/signup/verify
  - POST /api/v1/auth/signup/resend
related:
  - getting-started/what-cedesk-is
---

You create a workspace in two steps: you give your details and CeDesk emails you a code, then you enter the code. The person who signs up becomes the first Admin.

> **Who can do this:** Anyone, if sign-up is turned on for your CeDesk. If there is no sign-up link, an Admin or whoever runs your server creates the workspace for you.

## Before you start

You need an email address you can open now, because the code goes there. Decide on:

- **A workspace address** you will sign in with, for example "acme". It is 3 to 40 characters.
- **A workspace name** that people see, for example "Acme Support". It can be up to 120 characters.
- **Your name**, and a **password**.

## Create it

1. Open the sign-up page and fill in the five items above. If a "I am human" check appears, complete it.
2. Submit. Nothing is created yet. CeDesk emails a 6-digit code to the address you gave.
3. Enter the code within 10 minutes. You have 5 tries.
4. CeDesk creates the workspace and your Admin account. Sign in with the workspace address, your email and your password.

If you sign up again with the same email from the same computer before you enter the code, the new request replaces the waiting one.

## If the code does not arrive

Wait a minute, check your spam folder, then ask for a new code. The old code stops working when a new one is sent. You can ask for another only after a short wait that the page shows.

## If the address is taken

If another workspace already uses your address, CeDesk tells you. Pick a new address and enter the same code again: the code still works.

## Check it worked

You can sign in with your workspace address, and the **Tickets** page opens. **Settings › Workspace › Members** lists you as Admin.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| There is no sign-up page | Sign-up is off on this server. | Ask whoever runs your server. |
| The check was refused | The "I am human" check was missing or failed. | Try again. |
| The address is taken | Another workspace has it. | Choose another address. |
| The address, email, name or password is not allowed | One of them breaks a rule. | Read the message beside the field and change it. |
| The code is wrong | A typing mistake. | Try again. After 5 tries, ask for a new code. |
| The code has expired | More than 10 minutes passed. | Ask for a new code, or start again. |
| Too many tries | Too many sign-ups from this address or codes for this email. | Wait, then try again. |
