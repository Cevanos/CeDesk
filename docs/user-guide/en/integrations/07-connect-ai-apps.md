---
title: Connect Claude, ChatGPT and other AI apps
slug: connect-ai-apps
topic: integrations
order: 7
summary: Let an AI app such as Claude or ChatGPT read your tickets and reports and work in CeDesk as you, and disconnect it at any time.
status: draft
updated: 2026-11-27
who: Anyone
plan: All workspaces
routes:
  - GET /api/v1/oauth/consent
  - POST /api/v1/oauth/consent
  - GET /api/v1/oauth/grants
  - DELETE /api/v1/oauth/grants/{grant_id}
  - GET /.well-known/oauth-authorization-server
  - GET /oauth/authorize
  - POST /oauth/token
  - POST /oauth/register
  - POST /oauth/revoke
settings:
  - CEDESK_MCP_ENABLED
  - CEDESK_OAUTH_ISSUER
  - CEDESK_MCP_REQUESTS_PER_MINUTE
  - CEDESK_OAUTH_REQUESTS_PER_MINUTE
  - CEDESK_OAUTH_REGISTRATIONS_PER_HOUR
related:
  - security-and-privacy/ai-apps-and-your-data
  - account-and-access/roles-and-rights
  - security-and-privacy/audit-log
---

CeDesk works with AI apps that speak MCP (the Model Context Protocol): Claude, Claude Code, ChatGPT, Codex, Cursor, VS Code, Gemini CLI and Antigravity. You add CeDesk to the app once, approve it in CeDesk, and then you can ask the app to find tickets, read them, reply, change them, run reports and change some setup, all as you.

> **Who can do this:** Every member, whatever their role, signed in as themselves. **Plan:** All workspaces.

## How it works

- **The address.** On CeDesk Cloud the address to give the app is **https://mcp.cedesk.in/mcp**. A self-hosted CeDesk has its own address: **https://(your CeDesk's address)/mcp**. Ask your Admin if you are not sure.
- **You approve it.** The app opens CeDesk in your browser. You sign in the usual way (password, two-step code, single sign-on and your workspace's IP allowlist all apply) and see a page that says **Connect (app)?**, with your name, your email, your workspace and what the app asks for. Select **Approve** or **Deny**. There is no workspace to pick: you sign in to one workspace, and the app is connected to that one.
- **The app acts as you.** Every tool runs with your role. What you can't do in CeDesk, the app can't do either. If an Admin changes your role, the app's tools change with it at once.
- **Every change is recorded.** Changes the app makes are in the audit log as "(your name) via (app name)". Approving and disconnecting an app are recorded too.
- **Each member connects their own app.** An Admin can't connect an app for someone else, and an API key or another app can't approve one.

## What the app may do (scopes)

The consent page lists what the app asks for. Most apps ask for both.

| On the consent page | What it allows |
|---------------------|----------------|
| Read the tickets, reports and settings you can see | Every read tool below. |
| Reply to tickets, and change the tickets and settings you can change | Every write tool below. |

An app that only asked to read gets "This connection doesn't allow write" when it tries a change. To allow changes, connect the app again and approve both.

## The tools

The app chooses a tool from what you ask. The name in brackets is what the app shows when it uses the tool.

### Reads

| Tool | What it answers | Who gets an answer |
|------|-----------------|--------------------|
| Search tickets (search_tickets) | Tickets you can see by number, words, customer name or email, field value or tag; filter by status, priority, team and whose; a link to each. | Everyone |
| Read a ticket (get_ticket) | One ticket with its latest 50 messages and private notes, the customer, the field values and the SLA. | Everyone |
| Search CeDesk (search) | Up to 10 tickets that match, for ChatGPT's search and deep research. | Everyone |
| Fetch from CeDesk (fetch) | A whole ticket from a search result, as text. | Everyone |
| Live queue (get_live_report) | Open and waiting tickets now, SLA due soon or breached, agents. | View reports |
| Backlog by day (get_backlog_report) | Open tickets at the end of each day, and how many breached their SLA. | View reports |
| Team report (get_team_report) | Created and resolved tickets, first reply and resolution times, CSAT, per team and per agent. | View reports |
| More metrics (get_metrics_report) | One-touch resolution, reopen rate, backlog by age, tickets by channel, topic and sentiment, time logged and open tasks per agent. | View reports |
| Run a report (run_report) | A built-in report, or a metric grouped by time or field. | View reports |
| List teams (list_teams) | Every team, its members and its assignment settings. | Everyone |
| List macros (list_macros) | Every macro with its steps. | Everyone |
| List SLA policies (list_sla_policies) | The SLA policies, targets and business hours. | Everyone |
| List automation rules (list_rules) | Every rule: its event, conditions and actions. | Change workspace settings |
| List routing (list_routing) | Each agent's status, channels, capacity, skills and open tickets. | Everyone |

### Writes

| Tool | What it does | Who can |
|------|--------------|---------|
| Reply to a ticket (reply_to_ticket) | Sends a reply to the customer by email or chat at once, or adds a private note. A reply can't be taken back. | Reply to customers, for a reply; everyone, for a note |
| Update a ticket (update_ticket) | Changes status, assignee or tags. | Everyone but light agents, within their ticket scope; assigning someone else needs the right to assign to anyone |
| Create a team (create_team) | Adds a team, live at once. | Change workspace settings |
| Create a macro (create_macro) | Adds a macro at the end of the menu, live at once. | Change workspace settings |
| Update an SLA policy (update_sla_policy) | Changes one policy's targets or conditions; every open ticket's SLA is worked out again. | Change workspace settings |
| Create an automation rule (create_rule) | Adds a rule that runs after the others. It can't call a webhook. | Change workspace settings |
| Update routing (update_routing) | Changes an agent's status, channels, capacity or skills, or the workspace's default capacity. | Change workspace settings |

The rights are the ones in [Roles and rights](../account-and-access/roles-and-rights.md). The last column names the right a role needs. Supervisors and Admins can view reports; only Admins change workspace settings among the built-in roles; a custom role has the rights its Admin ticked. A light agent can read tickets and add private notes, nothing more.

## What an app never reaches

Whatever your role, these are not offered to an app and can't be reached through one:

- members, invitations and roles;
- security settings, two-step sign-in rules, the IP allowlist and single sign-on;
- API keys, webhooks and connected apps;
- the Cevanos connection, imports, privacy requests (export and erase), retention and the sandbox;
- approvals, exporting every contact, and email campaigns;
- sign-in itself: an app never sees your password or two-step code.

## Connect an app

Use **https://mcp.cedesk.in/mcp** on CeDesk Cloud, or your own CeDesk's address followed by **/mcp**.

| App | How to add CeDesk |
|-----|-------------------|
| Claude (web and desktop) | **Settings › Connectors › Add custom connector**. Name it CeDesk, paste the address and select **Add**, then **Connect**. On a Team or Enterprise plan an owner may need to add it first. |
| Claude Code | Run the command below, then type **/mcp** in Claude Code and choose CeDesk to sign in. |
| ChatGPT | Turn on developer mode (**Settings › Apps › Advanced settings**), then create an app with the address and OAuth sign-in. |
| Codex | Run the two commands below. The second opens your browser to sign in. |
| Cursor, VS Code, Gemini CLI, Antigravity | Add a remote (HTTP) MCP server with the address in the app's MCP settings. The app opens your browser to sign in the first time it is used. |

```
claude mcp add --transport http cedesk https://mcp.cedesk.in/mcp
```

```
codex mcp add cedesk --url https://mcp.cedesk.in/mcp
codex mcp login cedesk
```

Examples of the MCP settings, with the key names each app uses:

```
Cursor (mcp.json):          {"mcpServers": {"cedesk": {"url": "https://mcp.cedesk.in/mcp"}}}
VS Code (.vscode/mcp.json): {"servers": {"cedesk": {"type": "http", "url": "https://mcp.cedesk.in/mcp"}}}
Gemini CLI (settings.json): {"mcpServers": {"cedesk": {"httpUrl": "https://mcp.cedesk.in/mcp"}}}
Antigravity (mcp_config):   {"mcpServers": {"cedesk": {"serverUrl": "https://mcp.cedesk.in/mcp"}}}
```

Then, whichever app:

1. Start the connection in the app. Your browser opens CeDesk.
2. Sign in to your workspace, as you always do.
3. On **Connect (app)?**, check the app's name and the address under "After you answer you go back to". Approve only if that is the app you are setting up.
4. Select **Approve**. You go back to the app, which is now connected.

> **Note:** Menu names in the AI apps change from time to time. If a step above does not match, look for "connectors", "MCP servers" or "custom apps" in the app's settings.

## Self-hosted CeDesk

Each self-hosted CeDesk is its own connector: people add it by its own address, **https://(your host)/mcp**, never by the CeDesk Cloud address. Claude and ChatGPT connect from their own servers, so your CeDesk must be reachable from the internet over HTTPS with a real certificate.

The Caddy setup in the deploy folder already sends the AI-app addresses to the API. Your server's administrator sets the public address (CEDESK_OAUTH_ISSUER) once, and must not change it after apps are connected. CEDESK_MCP_ENABLED turns the connector off. The steps are in the self-hosting guide, under "Connect your CeDesk to Claude, ChatGPT and other AI apps", and every setting is in the settings reference.

## See and disconnect your apps

1. Open the account menu and select **My account**. Admins can also go to **Settings › Developers › Connected apps**.
2. Under **Connected apps** you see each app, whether it can read only or also make changes, when you connected it and when it was last used.
3. Select **Disconnect**, then **Disconnect** again to confirm.

The app stops working with CeDesk at once: every token it holds is ended. You can connect it again later. Disconnecting is recorded in the audit log.

An app also stops when your account is deactivated, and asks you to connect again if it has not been used for 30 days.

## Limits

| Limit | Value |
|-------|-------|
| Requests per connected app | 120 a minute (self-hosted: CEDESK_MCP_REQUESTS_PER_MINUTE). Over it, the app is asked to wait. |
| Sign-in requests | 60 a minute per network address; 20 new app registrations an hour per network address. |
| Sign-in token | Lasts 1 hour; the app renews it by itself for up to 30 days of use. |
| Approval page | Works for 10 minutes. After that, start again from the app. |
| One ticket | The latest 50 messages; each message is cut at 4,000 characters. |
| One search | Up to 50 tickets a page (search for ChatGPT: 10). |
| One request | Up to 4 MB. |
| Connections | One per member, app and address. Approving the same app again updates it. |

Apps can't upload or download attachments, merge tickets, or create tickets.

## Check it worked

Ask the app "Find my open tickets in CeDesk". It answers with your tickets and links to them. The app appears under **My account › Connected apps** with "Last used" today.

## Troubleshooting

| What you see | Why | What to do |
|--------------|-----|------------|
| "Your role can't do this." | Your role doesn't allow that action in CeDesk either. | Ask an Admin, or do it with an account that may. |
| "This connection doesn't allow write. Connect the app again and allow it." | The app asked for read only. | Disconnect it, connect again and approve changes. |
| "Your workspace requires two-step sign-in." | Your workspace requires two-step sign-in and you haven't set it up. | Set it up in **My account**, then start again from the app. |
| The app is refused although you approved it | Your workspace has an IP allowlist, and the app's servers are not on it. | Ask an Admin to add the AI provider's addresses, or use the app from an allowed network. |
| "Too many requests. Wait (seconds) s." | The app sent more than 120 requests in a minute. | Wait; the app usually tries again by itself. |
| "This link is missing its request. Start again from the app." | The approval page was opened without the app. | Start the connection from the app. |
| "This app is no longer known here. Start again from the app." | The app's registration ended or was turned off. | Remove CeDesk from the app and add it again. |
| The app asks you to connect again | It was disconnected, your account was deactivated, or it was unused for 30 days. | Connect it again. |
| A self-hosted address doesn't connect | CeDesk is not reachable from the internet over HTTPS, or the connector is off. | Ask your server's administrator. |

## Related

- [AI apps and your data](../security-and-privacy/ai-apps-and-your-data.md)
- [Roles and rights](../account-and-access/roles-and-rights.md)
- [The audit log](../security-and-privacy/audit-log.md)
