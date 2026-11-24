---
title: Single sign-on (SAML)
slug: single-sign-on
topic: security-and-privacy
order: 2
summary: Let people sign in with your identity provider, and optionally refuse password sign-in.
status: draft
updated: 2026-11-24
who: Admins
plan: All workspaces
routes:
  - GET /api/v1/sso-settings
  - PUT /api/v1/sso-settings
  - GET /api/v1/sso/{workspace}/metadata
related:
  - security-and-privacy/security-settings
---

With single sign-on, people use the account they already have at your identity provider, such as Okta or Google Workspace, instead of a CeDesk password.

> **Who can do this:** Admins.

## Set it up

1. Go to **Settings › Security & data › Security** and find **Single sign-on (SAML)**.
2. Under **Give these to your identity provider**, copy the **Service provider entity ID**, the **ACS URL** and the **Metadata URL** into your identity provider's setup screen.
3. From your identity provider, copy back its **entity ID**, its **sign-in URL** (https) and its **certificate** in PEM form.
4. Turn on **Enable single sign-on** and select **Save**.

The sign-in page then shows "Sign in with SSO".

## Only allow SSO

Turn on **Only allow SSO** and password sign-in is turned off for everyone except Admins. Admins keep it so a broken setup can be fixed. Do this only after you have signed in with SSO yourself.

## Check it worked

Open the sign-in page in a private window and choose **Sign in with SSO**. You return signed in.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| Sign-in comes back with an error | The identity provider's answer was refused, often a wrong certificate or entity ID. | Compare the three values on both sides. |
| Password sign-in is refused | **Only allow SSO** is on. | Use SSO, or ask an Admin. |
