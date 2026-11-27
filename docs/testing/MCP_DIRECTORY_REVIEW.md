# CeDesk MCP connector: directory review kit

What Claude's connector directory and OpenAI's app review need from us beyond the code: the
demo video, the test prompts with expected results, and the reviewer account. The connector
itself is `src/cedesk/mcp/` (21 tools) and the OAuth server in `helpdesk/*/oauth.py`; the
user-facing description is
[Connect Claude, ChatGPT and other AI apps](../user-guide/en/integrations/07-connect-ai-apps.md),
and what the tools return is in
[AI apps and your data](../user-guide/en/security-and-privacy/08-ai-apps-and-your-data.md).

## Before recording or submitting

1. Make a fresh review workspace on CeDesk Cloud and retire the last one:
   [DEVELOPMENT.md, "Demo workspace for AI-app directory reviewers"](../DEVELOPMENT.md#demo-workspace-for-ai-app-directory-reviewers).
   Below, `review-2612` is its slug and `review.cedesk.in` its email domain.
2. Accounts used here: **Alex** `alex@review.cedesk.in` (Admin) and **Riley**
   `riley@review.cedesk.in` (Light agent). Same password for both.
3. The demo's tickets (numbers as made by `seed-demo`):

   | Ticket | Subject | State after seeding |
   |---|---|---|
   | T-1001 | Refund for damaged T-shirt (order #1042) | High, Billing, assigned to Sam Rao, a note from Jordan |
   | T-1002 | Invoice copy for October bulk order | Email, Billing |
   | T-1003 | Where is my order #1077? | Urgent, Shipping, VIP customer Arjun Mehta, in Hindi |
   | T-1004 | App crashes on the payment page | Pending, Tech, Casey, a reply and a note |
   | T-1005 | Change delivery address for #1019 | Resolved by Taylor |
   | T-1006 | Do you ship to Sri Lanka? | Web form, no team, unassigned |

4. In Claude and ChatGPT, remove any earlier CeDesk connector, so the video shows the
   first connection.
5. Record at 1080p with the browser zoomed so the tool calls are readable. Show the tool
   call details (expand them) at least once per tool group.

## Demo video (about 3 minutes)

| # | Time | Screen | What happens | Say (voice-over or caption) |
|---|---|---|---|---|
| 1 | 0:00 | CeDesk ticket list as Alex | The six demo tickets. | "CeDesk is a help desk. Its MCP server lets Claude and ChatGPT work in it as the member who connects them." |
| 2 | 0:12 | Claude, Settings › Connectors | **Add custom connector**, name CeDesk, URL `https://mcp.cedesk.in/mcp`, **Add**, **Connect**. | "In Claude, add CeDesk by its MCP address." |
| 3 | 0:25 | CeDesk sign-in in the browser | Type the workspace `review-2612`, Alex's email and password. | "CeDesk opens. The member signs in to their own workspace; that is where the workspace is chosen." |
| 4 | 0:35 | Consent page "Connect Claude?" | Point at Alex's name and email, the workspace name, the two permissions and the return host. Select **Approve**. | "The app acts as Alex, with Alex's role, and nothing more." |
| 5 | 0:45 | Claude chat | Prompt P1. Expand the search_tickets call. | "Search tickets." |
| 6 | 0:58 | Claude chat | Prompt P2. Expand get_ticket. | "Read a ticket with its messages, customer and SLA." |
| 7 | 1:12 | Claude chat, then CeDesk T-1006 | Prompt P3. Claude asks to confirm the write; approve. Switch to CeDesk: the reply is on T-1006, by "Alex via Claude". | "Replying sends to the customer, so Claude asks first." |
| 8 | 1:30 | Claude chat, then CeDesk T-1002 | Prompt P4. CeDesk shows Pending, Jordan, tag invoice. | "Status, assignee and tags." |
| 9 | 1:45 | Claude chat | Prompt P5. Expand get_team_report. | "Reports." |
| 10 | 1:58 | Claude chat, then Settings › Service › Macros | Prompt P6. The macro "Shipping delay" is in CeDesk. | "Setup changes, for members whose role may change settings." |
| 11 | 2:12 | ChatGPT, developer mode | Create the app with the same URL and OAuth; sign in as Riley; approve. | "The same server works in ChatGPT." |
| 12 | 2:30 | ChatGPT chat | Prompt N1 as Riley. The tool answers "Your role can't do this."; T-1006 is unchanged. | "Riley is a light agent: the role's limits hold through any app." |
| 13 | 2:42 | ChatGPT chat | Prompt N2. No tool can do it; the answer says so. | "Members, roles, security and API keys are never offered to an app." |
| 14 | 2:52 | CeDesk: T-1002, then Settings › Security & data › Audit log | The ticket's history lines and the reply on T-1006 say "Alex via Claude"; the audit log has the macro by "Alex via Claude". | "Every change says which app made it." |
| 15 | 3:02 | CeDesk, My account › Connected apps (as Alex) | Claude listed with "Last used" and today's date. **Disconnect**, confirm. Back in Claude, P1 again: Claude is asked to connect again. | "A member disconnects an app here, and it stops at once." |
| 16 | 3:20 | End card | `https://mcp.cedesk.in/mcp`, docs link. | |

Keep it under 4 minutes; cut steps 9 or 13 first if it runs long.

## Test prompts with expected results

Run them in a fresh chat with only the CeDesk connector on. Alex unless stated.

### Positive

| # | Prompt | Tool(s) expected | Expected result |
|---|---|---|---|
| P1 | "Find the urgent tickets in CeDesk." | search_tickets (priority urgent) | T-1003 "Where is my order #1077?", Arjun Mehta, Shipping, with a link to the ticket. |
| P2 | "Open T-1004 in CeDesk and summarise it, including the internal notes." | search_tickets or get_ticket | Sara Khan's app crashes on Pay (iPhone, 3.8.1); Casey asked for the iPhone model; Casey's note about Card SDK 4.2 on iOS 17.6; status Pending, assigned to Casey. |
| P3 | "Reply to T-1006: yes, we ship to Sri Lanka, delivery to Colombo takes 7 to 10 days." | reply_to_ticket (kind reply) | The client asks for confirmation, then the reply is on T-1006 in CeDesk, signed "Alex via (app)". |
| P4 | "Set T-1002 to pending, assign it to Jordan and tag it invoice." | list_routing (to find Jordan), update_ticket | T-1002 is Pending, assigned to Jordan, tagged invoice. |
| P5 | "How did each team do over the last 30 days? First reply time and resolved tickets." | get_team_report | Figures per team (Billing, Shipping, Tech) and per agent, from the six demo tickets. |
| P6 | "Create a macro called Shipping delay that replies 'Sorry for the wait, your order is on its way.' and sets the ticket to pending." | create_macro | The macro is last in Settings › Service › Macros with a reply step and a set-status step. |

### Negative

| # | As | Prompt | Expected result |
|---|---|---|---|
| N1 | Riley (Light agent) | "Close ticket T-1006." | update_ticket is called and refused with "Your role can't do this."; the client reports it. T-1006 is unchanged. (A private note from Riley would work: light agents may add notes.) |
| N2 | Alex (Admin) | "Invite jane@example.com to CeDesk as an Admin, and list every member's email address." | No tool for members or invitations exists; the client says it can't. Nothing changes and no email address of a member is returned. |
| N3 | Alex (Admin) | "Delete ticket T-1005 and turn off two-step sign-in for the workspace." | No delete or security tool exists; the client says it can't. T-1005 and the security settings are unchanged. |

Also worth checking by hand: a client that asked only for read gets "This connection doesn't
allow write. Connect the app again and allow it." on any write, and an expired or
disconnected connection gets a 401 that makes the client sign in again.

## Submission facts

| Asked for | Answer |
|---|---|
| Server URL | `https://mcp.cedesk.in/mcp` (self-hosted: `https://<host>/mcp`) |
| Auth | OAuth 2.1 with PKCE (S256); dynamic client registration and client ID metadata documents; issuer `https://mcp.cedesk.in` |
| Scopes | `read`, `write` |
| Tools | 21: 14 read-only, 7 writes; each has a title and read-only, destructive, idempotent and open-world hints |
| Logos | `frontend/public/connector/` (see its README) |
| Privacy | The AI-apps section: [AI apps and your data](../user-guide/en/security-and-privacy/08-ai-apps-and-your-data.md); the Cloud privacy policy page must carry it too |
| OpenAI domain check | `CEDESK_OPENAI_APPS_CHALLENGE` on the server; served at `https://mcp.cedesk.in/.well-known/openai-apps-challenge` |
| Rate limits | 120 requests a minute per connection |
