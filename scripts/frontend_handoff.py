"""Writes docs/frontend/FRONTEND_HANDOFF_CEDESK.md for the frontend team (md 2.8 step 5):
every route, by the screen that uses it, with what that screen needs.

The guidance per screen is written here by hand; the route tables come from the OpenAPI
file, so no route is missed or mistyped. A route no screen claims stops the script: add
it to a screen below.

    uv run python scripts/frontend_handoff.py
    uv run python scripts/frontend_handoff.py --pr "PR #42 mobile=path/to/openapi.json" ...

`--pr` adds the routes of a branch not merged yet (its own openapi.json), marked with
that label, so the doc can be ahead of `main` while PRs wait for review.
"""

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/api/openapi.json"
OUT = ROOT / "docs/frontend/FRONTEND_HANDOFF_CEDESK.md"
PREFIX = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # without /api/v1
    summary: str
    pr: str | None  # None: on main


@dataclass(frozen=True)
class Screen:
    title: str
    needs: str  # markdown
    claims: Callable[[Route], bool]


def starts(*prefixes: str) -> Callable[[Route], bool]:
    return lambda r: any(r.path == p or r.path.startswith(p + "/") for p in prefixes)


def exact(*pairs: str) -> Callable[[Route], bool]:
    wanted = {tuple(p.split(" ", 1)) for p in pairs}
    return lambda r: (r.method, r.path) in wanted


def matches(pattern: str) -> Callable[[Route], bool]:
    rx = re.compile(pattern)
    return lambda r: bool(rx.search(r.path))


def either(*tests: Callable[[Route], bool]) -> Callable[[Route], bool]:
    return lambda r: any(t(r) for t in tests)


INTRO = """# CeDesk: frontend handoff (every route, by screen)

For the frontend team. Written from the API as served (`docs/api/openapi.json`, the
source of truth for every field) and `docs/api/CHANGELOG.md` (what changed and why).
Regenerate with `uv run python scripts/frontend_handoff.py`.

Every route is under `/api/v1`, shown here without it. Routes marked with a PR are
built and tested but wait for review: they exist on `main` once that PR is merged.

## 1. What every screen needs to know

**Signing in.** `POST /auth/sign-in` gives a `token`; send it on every call as
`Authorization: Bearer <token>`. Two flags on the answer change what to show next:
`two_factor_required` (ask for the authenticator code, send it to `POST /auth/2fa` with
the same token within 10 minutes) and `password_expired` (show Change password; until then
only `GET /me`, `POST /me/password` and sign-out work). A workspace with single sign-on
starts at `GET /sso/{workspace}/start` instead.

**What to show this person.** `GET /me` returns `access`: the role, the `actions` it may
take (`read_ticket`, `create_ticket`, `reply`, `private_note`, `change_fields`,
`assign_self`, `assign_others`, `approve`, `reports`, `settings`, `list_members`,
`manage_access`) and its `scope` (`all`, `team_edit`: other teams' tickets are view only,
`team_only`: other teams' tickets don't exist for them). Hide what `actions` doesn't
allow; the API refuses it anyway (403). Built-in roles: Admin (everything), Supervisor
(adds assign others, approve, reports), Agent, Light agent (reads and adds notes only).
`/me` also says `two_factor_setup_required` (the workspace requires 2FA: send them to set
it up).

**Errors.** Every error is `{"error": {"code", "message", "details"?}}`. Show `message`:
it is written for the person using the screen. `details` comes only on 422 validation
errors, one entry per field (`field`, `message`), to put next to the right input.

| Status | Means | What to do |
|---|---|---|
| 401 | Not signed in, or the session ended | Back to sign-in |
| 402 | Over the cloud plan's limit | Show `message` (it names the limit) |
| 403 | Not allowed (role, scope, IP list, old password) | `message`; a ticket: `view_only_reason` |
| 404 | Doesn't exist, or another team's (team-only roles) | "Not found" |
| 409 | Changed by someone else, or already done | Reload, then show `message` |
| 422 | A field is wrong | Show `details` next to the fields |
| 429 | Too many tries | Wait `Retry-After` seconds |
| 503 | A provider (Slack, Shopify, Cevanos...) didn't answer | "Try again" |

**Lists.** Lists page with a cursor: the answer has `next_cursor`; send it back as
`cursor` (with the same filters) for the next page. `null` means the last page.

**Two people editing one ticket.** A ticket comes with an `ETag` header. Send it back as
`If-Match` on `PATCH /tickets/{id}` and resolve: if someone changed the ticket meanwhile
the answer is 409, so reload and let the agent try again instead of overwriting.

**Double clicks and retries.** Routes that create something take an optional
`Idempotency-Key` header (any unique string, a UUID is good). Send the same key when you
retry: the repeat returns what the first one made (header `Idempotent-Replayed: true`)
instead of sending a reply twice. Keys last 24 hours.

**Live updates.** `POST /stream/ticket` gives a one-use ticket (valid 60 s); open
`GET /stream?ticket=...` with `EventSource`. Events: `ticket.created`, `ticket.updated`,
`ticket.resolved`, `message.created`, `assignment.changed`, `chat.accepted`,
`chat.taken_over`, `chat.closed`, `chat.reopened`, `notification`. An event says what
changed (ids), never the text: fetch the ticket or list again through the normal routes.
On reconnect, send `Last-Event-ID` to get what you missed (kept 24 hours). The portal has
its own stream (`/portal/stream`) with only the customer's own messages and status.

**Placeholders.** Replies, saved replies and macros may contain
`{{customer.first_name}}` and the like; the API fills them when the reply is sent.
Show them as they are in the editor.
"""

SCREENS: list[Screen] = [
    Screen(
        "Sign-in and my account",
        "- Sign-in page: workspace address, email, password. Then, if asked, the 2FA code "
        '(or a recovery code). "Forgot password" emails a link; the link\'s page calls '
        "`POST /auth/reset-password`.\n"
        "- SSO: `GET /sso/{workspace}` says whether the workspace uses it; then send the "
        "browser to `/sso/{workspace}/start`.\n"
        "- My account (avatar menu): change password, two-step sign-in (`POST "
        "/me/2fa/setup` gives an `otpauth_uri`: show it as a QR code; confirm with the "
        "first code, then show the recovery codes once), new recovery codes, sign out.",
        either(starts("/auth", "/me/password", "/me/2fa", "/sso"), exact("GET /me")),
    ),
    Screen(
        "Top bar: my status, notifications, my work",
        "- Status switch (`available`, `on_break`, `transfers_only`, `offline`): "
        "`PUT /me/status`. New "
        "agents start Offline, so routing gives them nothing until they switch on.\n"
        "- Bell: `GET /notifications` (with `unread` for the badge), mark one or all "
        "read. A `notification` event on the live stream means fetch again.\n"
        '- "Waiting for my approval" and "My tasks" lists.\n'
        "- Live stream: see section 1.",
        either(
            starts("/notifications", "/stream"),
            exact("PUT /me/status", "GET /approvals/waiting", "GET /tasks/mine"),
        ),
    ),
    Screen(
        "Inbox: the ticket list",
        "- Tabs Open, Pending, Resolved with counts (`counts` on the answer). Filters: "
        "scope (`everyone`, `mine`, `unassigned`), priority, team, `imported` (tickets "
        "brought from another help desk). Search box: `q` (number, subject, customer, "
        "field value, tag). Sort: `priority` (default), `sla`, `newest`, `oldest`, "
        "`updated`.\n"
        "- New ticket: `POST /tickets` (type, its fields, team, assignee, tags). The "
        "ticket types and their fields come from `GET /ticket-types`.\n"
        "- A team-only role only ever gets its team's tickets; nothing to filter on your "
        "side. With the archive PR, resolved tickets older than the archive age (default "
        "120 days) leave the list: see Archive.",
        exact("GET /tickets", "POST /tickets"),
    ),
    Screen(
        "Ticket page: the conversation",
        "- `GET /tickets/{id}`: the ticket with its newest messages (load older ones with "
        "`older_messages_cursor`), the SLA clock (`sla`), the CSAT rating, the customer "
        "card, `can_edit` and `view_only_reason` (show it as a banner and disable the "
        "editing controls).\n"
        "- Reply or private note: `POST /tickets/{id}/messages` with `kind` `reply` or "
        "`note`, `mention_ids` for @mentions in notes, `attachment_ids` for files "
        "uploaded first with `POST /tickets/{id}/attachments`. Send an "
        "`Idempotency-Key`.\n"
        "- Sidebar fields: `PATCH /tickets/{id}` (status, priority, type, fields, team, "
        "assignee, subject, tags) with `If-Match`. Resolve: `POST /tickets/{id}/resolve`.\n"
        "- Macros menu: `GET /macros`, run one with `POST /tickets/{id}/macros/{macro_id}`. "
        "Saved replies: type `/` in the box, `GET /saved-replies?q=`.\n"
        "- Cevanos chat handovers: `accepted: false` means waiting; show **Accept** "
        "(`POST /tickets/{id}/accept`) before replies are allowed. `handover` holds the "
        "AI's summary and reason.\n"
        "- Files: `GET /attachments/{id}` gives a short-lived download link.",
        either(
            exact(
                "GET /tickets/{ticket_id}",
                "PATCH /tickets/{ticket_id}",
                "POST /tickets/{ticket_id}/resolve",
                "POST /tickets/{ticket_id}/accept",
                "POST /tickets/from-conversation/{conversation_id}",
                "POST /tickets/{ticket_id}/simulate-customer-reply",
                "POST /tickets/{ticket_id}/macros/{macro_id}",
            ),
            starts("/attachments", "/files"),
            matches(r"^/tickets/\{ticket_id\}/(messages|attachments)$"),
        ),
    ),
    Screen(
        "Ticket page: related tickets, approvals, tasks and time",
        "- Related card: `GET /tickets/{id}/related` (parent, children, links, merges). "
        "Merge into another open ticket, link two similar tickets, or make a child ticket "
        "for another team.\n"
        '- Approvals: ask a named person; they decide from "Waiting for my approval".\n'
        "- Tasks: add with a quick due time (`1h`, `4h`, `tomorrow`) or a date; the owner "
        "is told. Time log: minutes, a note, billable or not; the total is on the answer.",
        either(
            matches(r"^/tickets/\{ticket_id\}/(related|links|merge|children|approvals|tasks|time)"),
            starts("/approvals/{approval_id}", "/tasks/{task_id}", "/time"),
        ),
    ),
    Screen(
        "Ticket page: side conversations, Jira, orders and payments",
        "- Side conversations (agents only, never shown to the customer): by email "
        "(addresses) or in a Slack channel (needs the Slack side conversations app). "
        "Replies come back to the same side conversation, and the assignee gets a "
        "`side_reply` notification. `via` says `email` or `slack`.\n"
        "- Jira: create an issue from the ticket or link an existing one.\n"
        "- Orders and payments cards: `available: false` comes with a `message` to show "
        "(not installed, Cevanos connected, provider down); the ticket still works.",
        either(
            starts("/side-conversations"),
            matches(r"^/tickets/\{ticket_id\}/(side-conversations|jira|orders|payments)"),
        ),
    ),
    Screen(
        "Customers",
        "- Customer card: `GET /contacts/{id}/tickets` (all their tickets, newest first). "
        "Change the name or VIP flag with `PATCH /contacts/{id}/profile`.",
        starts("/contacts"),
    ),
    Screen(
        "Archive",
        "- Old resolved tickets live here: search by number (`1042` or `T-1042`) or by "
        "customer (`email`, `contact_id`); open one read only (messages, files' names, "
        "rating). No reply, change or reopen.",
        starts("/archive/tickets"),
    ),
    Screen(
        "Reports",
        "- Supervisors and Admins (`reports`). Team report, more metrics and help-article "
        "votes, for a period (`from`, `to`) and a team (`team_id`; not for articles). "
        "Each one downloads as CSV with `format=csv`.",
        starts("/reports"),
    ),
    Screen(
        "Settings: members, roles, teams and routing",
        "- Members: add (with a role and first password), change role or deactivate, set "
        "a new password, reset someone's 2FA (lost phone). Admins only "
        "(`manage_access`).\n"
        "- Custom roles: name, the five rights, scope (all, team edit, team only) and its "
        "team.\n"
        "- Teams: name, auto-assign, max open per agent, members. Agents: status, channels, "
        "capacity per channel, skills; workspace defaults in Routing defaults.",
        either(starts("/members", "/roles", "/teams", "/agents", "/routing-defaults")),
    ),
    Screen(
        "Settings: ticket types, SLA, rules, macros, saved replies, triage, sandbox",
        "- Ticket types: fields (`text`, `number`, `select`), required skill, shown to "
        "customers or not; order matters (menus follow it).\n"
        "- SLA: targets per priority, ordered policies, business hours and holidays.\n"
        "- Rules (run in order) and macros (menu order): send the whole order to "
        "`/order`.\n"
        "- Sandbox: copy the live rules, macros and saved replies, change them, try them "
        "on sample tickets, compare, then deploy.",
        starts(
            "/ticket-types",
            "/sla",
            "/holidays",
            "/rules",
            "/macros",
            "/saved-replies",
            "/triage-settings",
            "/sandbox",
        ),
    ),
    Screen(
        "Settings: email",
        "- Support addresses: add, make default, signature, DKIM selector; DNS check "
        "(SPF, DKIM, DMARC), forwarding check, send yourself a test.\n"
        "- Failed emails: the list and Retry.",
        starts("/email-addresses", "/email"),
    ),
    Screen(
        "Settings: customer portal and help center",
        "- Portal settings: who may sign in (allowed email domains), the CSAT survey and "
        "the public web form on or off.\n"
        "- Help center: categories and articles (draft or published).",
        starts("/portal-settings", "/article-categories", "/articles"),
    ),
    Screen(
        "Settings: security, privacy and audit",
        "- Security: minimum password length, password expiry, session length, 2FA "
        "required, IP list (test my IP first, so nobody is locked out), files need "
        "sign-in, admin alerts; the security score. SSO settings.\n"
        "- Privacy: export everything about one customer (a JSON file kept 7 days) or "
        "erase them (type their email again to confirm; can't be undone).\n"
        "- Audit log: filter by area, download CSV, verify the chain.",
        starts("/security-settings", "/security", "/sso-settings", "/privacy", "/audit"),
    ),
    Screen(
        "Settings: apps, API keys, webhooks, Cevanos",
        "- Apps: `GET /apps` lists each app with what to ask for: `settings_fields`, "
        "`credential` (null: none, as for Calendly) and `second_credential` (Slack side "
        "conversations: `signing_secret`). Credentials are never shown again, only "
        "`secret_hint`. `blocked` explains why an app can't be installed here.\n"
        "- API keys and webhook secrets are shown **once**, on creation: say so.\n"
        "- Webhooks: events, delivery log, retry, test ping, new secret.\n"
        "- Cevanos: connect with a key, check, disconnect. Failed events list (outbox).",
        starts("/apps", "/api-keys", "/webhooks", "/cevanos/connection", "/outbox"),
    ),
    Screen(
        "Settings: imports",
        "- Connect a help desk (or upload a CSV), see what it holds and the team each group "
        "maps to, dry run, run, watch progress, sync now, go live (stop syncing), "
        "history.",
        starts("/imports"),
    ),
    Screen(
        "Settings: plan, retention, archive and demo",
        "- Usage and plan limits (cloud). Retention: delete resolved tickets after N days. "
        "Archive age (default 120 days).\n"
        "- Demo (not on production servers): load the demo, load sample tickets (for an "
        "empty inbox), reset the demo.",
        either(starts("/usage", "/retention", "/archive/settings", "/demo")),
    ),
    Screen(
        "Customer portal (customers, signed in with an emailed code)",
        "- Sign in: email, then the 6-digit code from the email. The customer's own "
        "tickets only; new ticket (types shown to customers), reply, rate a resolved "
        "ticket. Their own live stream.\n"
        "- The emailed CSAT survey link opens `/public/csat/{token}` (no sign-in).",
        starts("/portal", "/public/csat"),
    ),
    Screen(
        "Public pages (no sign-in): help center and web form",
        '- Help center: categories, search, articles, "Was this helpful?".\n'
        "- Web form: the ticket types and fields to show, then send it (rate limited).",
        matches(r"^/public/\{workspace\}/"),
    ),
    Screen(
        "Mobile app",
        "- `GET /me/queue`: mine and unassigned, not resolved; mine first, then chats "
        "waiting to be accepted, then by SLA. Each item has the last message's first 140 "
        "characters.\n"
        "- `POST /tickets/{id}/mobile-reply`: one tap; accepts a waiting chat first; a "
        "light agent's text is saved as a note (`saved_as`). Everything else uses the "
        "web routes.",
        exact("GET /me/queue", "POST /tickets/{ticket_id}/mobile-reply"),
    ),
    Screen(
        "Not for the frontend: server to server",
        "- Called by other systems with their own keys or signatures: mail services, "
        "Cevanos, Slack, other systems through the inbound API, and health checks.",
        starts("/inbound", "/cevanos/events", "/slack/events", "/healthz", "/readyz"),
    ),
]


def routes(spec_path: Path, pr: str | None) -> list[Route]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    found = []
    for path, ops in spec["paths"].items():
        for method in METHODS:
            if method in ops:
                found.append(Route(method.upper(), path.removeprefix(PREFIX),
                                   ops[method].get("summary", ""), pr))  # fmt: skip
    return found


def collect(prs: list[tuple[str, Path]]) -> list[Route]:
    seen: dict[tuple[str, str], Route] = {}
    for r in routes(SPEC, None):
        seen[(r.method, r.path)] = r
    for label, path in prs:
        for r in routes(path, label):
            seen.setdefault((r.method, r.path), r)
    return list(seen.values())


def assign(all_routes: list[Route]) -> dict[str, list[Route]]:
    placed: dict[str, list[Route]] = {s.title: [] for s in SCREENS}
    unclaimed = []
    for r in all_routes:
        owners = [s.title for s in SCREENS if s.claims(r)]
        if not owners:
            unclaimed.append(r)
            continue
        placed[owners[0]].append(r)  # the first screen that claims it
    if unclaimed:
        names = ", ".join(f"{r.method} {r.path}" for r in unclaimed)
        raise SystemExit(f"No screen claims: {names}. Add them to a screen.")
    return placed


ORDER = {m: i for i, m in enumerate(("GET", "POST", "PUT", "PATCH", "DELETE"))}


def render(placed: dict[str, list[Route]], prs: list[tuple[str, Path]]) -> str:
    total = sum(len(v) for v in placed.values())
    pending = sum(1 for v in placed.values() for r in v if r.pr)
    parts = [INTRO]
    parts.append(f"\n**{total} routes** ({pending} in PRs waiting for review: "
                 f"{', '.join(label for label, _ in prs) or 'none'}).\n")  # fmt: skip
    parts.append("\n## 2. Screens\n")
    for n, screen in enumerate(SCREENS, start=1):
        rows = sorted(placed[screen.title], key=lambda r: (r.path, ORDER[r.method]))
        if not rows:
            continue
        parts.append(f"\n### 2.{n} {screen.title}\n\n{screen.needs}\n")
        parts.append("\n| | Route | What it does |\n|---|---|---|\n")
        for r in rows:
            mark = f" *({r.pr})*" if r.pr else ""
            summary = r.summary.replace("|", "\\|")
            parts.append(f"| `{r.method}` | `{r.path}` | {summary}{mark} |\n")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", action="append", default=[], metavar="LABEL=OPENAPI.json",
                        help="Routes of a branch not merged yet, marked with LABEL.")  # fmt: skip
    args = parser.parse_args()
    prs = [(label, Path(p)) for label, p in (item.split("=", 1) for item in args.pr)]
    placed = assign(collect(prs))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(placed, prs), encoding="utf-8")
    print(f"Wrote {OUT} ({sum(len(v) for v in placed.values())} routes).")


if __name__ == "__main__":
    main()
