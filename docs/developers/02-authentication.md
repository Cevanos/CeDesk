---
title: Authentication
slug: authentication
topic: developers
order: 2
summary: Bearer tokens and API keys, key levels, two-step sign-in and the sign-in errors you can get.
status: draft
updated: 2026-11-24
who: Developers
plan: All workspaces
routes:
  - POST /api/v1/auth/sign-in
  - POST /api/v1/auth/2fa
  - POST /api/v1/auth/sign-out
  - GET /api/v1/me
  - GET /api/v1/api-keys
  - POST /api/v1/api-keys
related:
  - developers/quickstart
---

Every call after sign-in carries a bearer token in the `Authorization` header: `Authorization: Bearer <token>`. The token is either a session token from signing in as a person, or an API key.

> **Who can do this:** A developer. Making API keys needs an Admin who is signed in.

## Use an API key for systems

An API key starts with `cdk_` and never expires, until an Admin revokes it. It acts as the Admin who made it, within its level:

| Level | What it may do |
|-------|----------------|
| Read only | GET routes. |
| Read and write | Also tickets, customers, notes, tags, tasks, time, and customer messages in through the inbound API. |
| Admin | Also teams, ticket types, saved replies, macros, rules and the help center. |

A key never reaches sign-in, members, roles, security, single sign-on, Cevanos, imports, privacy or API keys, and it never approves anything. It cannot make or revoke keys.

## Sign in as a person

```
curl -X POST "$CEDESK_URL/api/v1/auth/sign-in" \
  -H "Content-Type: application/json" \
  -d '{"workspace": "acme", "email": "ana@example.com", "password": "..."}'
```

The answer has a `token`, shown once, and `expires_at`. Send it as the bearer token. End it with `POST /auth/sign-out`.

Two flags in the answer need action:

- `two_factor_required` is true: send the code from the authenticator app, or a recovery code, to `POST /auth/2fa` with this token within 10 minutes. Until then every other route answers 403 with the code `TwoFactorRequired`. Ten wrong codes lock the sign-in for a while.
- `password_expired` is true: until the password is changed, every route except `GET /me`, `POST /me/password` and sign-out answers 403 with the code `PasswordExpired`.

## Sign-in errors

| Answer | Meaning |
|--------|---------|
| 401 | The details did not match. The answer is the same for every reason, so it cannot be used to find accounts. |
| 403 `IpNotAllowed` | The address is not on the workspace's IP allowlist. This applies to sign-in and every later request. |
| 403 `TwoFactorSetupRequired` | The workspace requires two-step sign-in and this member has not set it up. |
| 429 | Too many attempts from this address. Read `Retry-After`. |

## Good habits

- Use a key per system and give it the lowest level that works.
- Keep keys out of source control. Revoke one the moment it leaks.
- Do not use a person's token for a service. It ends when they sign out or change their password.

## Check it worked

`GET /me` with your token returns the member and workspace.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| 401 on every call | The token ended or the key was revoked. | Sign in again, or make a new key. |
| 403 on a route that works for you in the app | The key's level is too low. | Use a higher level, or sign in as the person. |
