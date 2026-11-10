"""`cedesk seed-demo`: a workspace that looks like the mock, for the frontend team and demos.

Built only through the services, the same code the API runs, so every rule applies:
routing picks the agents and writes its reasons, notes notify the people mentioned, and
every change writes its system line. Passwords are never stored in the code: all demo
accounts share the one password asked for at the prompt.
"""

from dataclasses import dataclass
from uuid import UUID

from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.domain.routing import AgentStatus
from cedesk.helpdesk.domain.tickets import Channel, Priority, Status
from cedesk.helpdesk.service import auth, catalog, members, messages, routing, tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.messages import MessageKind
from cedesk.helpdesk.service.tickets import NewTicket

DOMAIN = "acme-store.in"


@dataclass(frozen=True)
class DemoMember:
    key: str
    name: str
    role: Role
    skills: tuple[str, ...] = ()
    channels: tuple[str, ...] = ("email", "chat", "messaging")


ADMIN = DemoMember("ritesh", "Ritesh", Role.admin)
MEMBERS = (
    DemoMember("adarsh", "Adarsh Dubey", Role.supervisor, ("refunds",)),
    DemoMember("neha", "Neha", Role.agent, ("hindi",)),
    DemoMember("chetan", "Chetan", Role.agent, (), ("email", "chat")),
    DemoMember("pranav", "Pranav", Role.agent, ("hindi",)),
    DemoMember("anurag", "Anurag", Role.agent),
    DemoMember("meena", "Meena", Role.light_agent),
)
TEAMS = {
    "Billing": ("adarsh", "neha"),
    "Shipping": ("pranav", "anurag"),
    "Tech": ("chetan", "ritesh"),
}


def email(key: str) -> str:
    return f"{key}@{DOMAIN}"


async def seed_demo(
    sm: Sessionmaker, *, slug: str, name: str, password: str
) -> list[tuple[str, str, str]]:
    """Creates the workspace and fills it. Returns (name, email, role) of every account."""
    await auth.create_workspace(
        sm,
        name=name,
        slug=slug,
        admin_email=email(ADMIN.key),
        admin_name=ADMIN.name,
        admin_password=password,
    )
    callers: dict[str, Caller] = {}

    async def as_(key: str) -> Caller:
        if key not in callers:
            callers[key] = (
                await auth.sign_in(sm, workspace=slug, email=email(key), password=password)
            ).caller
        return callers[key]

    admin = await as_(ADMIN.key)
    ids: dict[str, UUID] = {ADMIN.key: admin.member_id}
    for m in MEMBERS:
        row = await members.add_member(
            sm, admin, email=email(m.key), name=m.name, role=m.role, password=password
        )
        ids[m.key] = row["id"]
    # New agents start Offline (md 14.5): switch everyone on, as they would at the start of
    # a shift, with their channels and skills.
    for m in (ADMIN, *MEMBERS):
        values: dict[str, object] = {"channels": list(m.channels), "skills": list(m.skills)}
        if m.role is not Role.light_agent:
            values["status"] = "available"
        await routing.set_routing(sm, admin, ids[m.key], values)
    await routing.set_routing(sm, admin, ids["neha"], {"cap_chat": 4})

    team_ids = {t["name"]: t["id"] for t in await catalog.list_teams(sm, admin)}
    for team_name, keys in TEAMS.items():
        await catalog.change_team(
            sm,
            admin,
            team_ids[team_name],
            name=None,
            auto_assign=None,
            max_open=None,
            member_ids=[ids[k] for k in keys],
        )
    type_ids = {t["name"]: t["id"] for t in await catalog.list_types(sm, admin)}
    await catalog.change_type(
        sm,
        admin,
        type_ids["Refund"],
        name=None,
        fields=None,
        required_skill="refunds",
        clear_skill=False,
    )

    async def ticket(new: NewTicket) -> UUID:
        view = await tickets.create_ticket(sm, admin, new)
        return UUID(str(view.ticket["id"]))

    # T-1001: a Cevanos AI handover; routing gives it to Adarsh (skill refunds).
    t1 = await ticket(
        NewTicket(
            subject="Refund for damaged T-shirt (order #1042)",
            contact_email="priya.sharma@example.com",
            contact_name="Priya Sharma",
            description="Hi, my T-shirt from order #1042 arrived torn at the seam. "
            "I want a refund.",
            priority=Priority.high,
            channel=Channel.chat,
            type_id=type_ids["Refund"],
            team_id=team_ids["Billing"],
            fields={"order_id": "#1042", "amount": 1500, "reason": "Damaged"},
            tags=["refund"],
            source="cevanos",
            source_id="demo-conv-1042",
            conversation_id="demo-conv-1042",
            topic="Refund / return",
            language="en",
            handover={
                "summary": "Priya received order #1042 (Blue T-shirt, M) with a torn seam and "
                "wants ₹1,500 back. She sent a photo and confirmed the order with a one-time code.",
                "reason": "Refund of ₹1,500 is above the ₹1,000 limit the AI may refund "
                "on its own.",
            },
        )
    )
    await messages.post_message(
        sm,
        await as_("neha"),
        t1,
        kind=MessageKind.note,
        body="@Adarsh Dubey this is above my approval limit, can you approve?",
        mention_ids=[ids["adarsh"]],
    )
    # T-1002: an email to Billing.
    await ticket(
        NewTicket(
            subject="Invoice copy for October bulk order",
            contact_email="rohan@guptatraders.co",
            contact_name="Rohan Gupta",
            description="Hello,\nPlease send a GST invoice copy for our order #0987 "
            "(40 tees).\n\nRegards,\nRohan",
            channel=Channel.email,
            type_id=type_ids["Billing question"],
            team_id=team_ids["Billing"],
            fields={"invoice": "INV-2026-0987"},
        )
    )
    # T-1003: urgent, VIP customer, Hindi.
    t3 = await ticket(
        NewTicket(
            subject="Where is my order #1077?",
            contact_email="arjun.m@example.com",
            contact_name="Arjun Mehta",
            description="Mera order #1077 kahan hai? Kal aana tha. Mujhe kal trip ke liye chahiye!",
            priority=Priority.urgent,
            channel=Channel.chat,
            type_id=type_ids["Shipping issue"],
            team_id=team_ids["Shipping"],
            fields={"order_id": "#1077", "courier": "Blue Dart", "tracking": "88213977"},
            tags=["vip"],
            language="hi",
            topic="Order status",
        )
    )
    view = await tickets.get_ticket(sm, admin, t3)
    await tickets.change_contact(sm, admin, UUID(str(view.ticket["contact_id"])), vip=True)
    # T-1004: Tech, a back-and-forth, now Pending.
    t4 = await ticket(
        NewTicket(
            subject="App crashes on the payment page",
            contact_email="sara.khan@example.com",
            contact_name="Sara Khan",
            description="The app crashes when I press Pay. Error: white screen.",
            priority=Priority.low,
            channel=Channel.chat,
            type_id=type_ids["Technical problem"],
            team_id=team_ids["Tech"],
            fields={"device": "iPhone", "app_version": "3.8.1", "error": "White screen after Pay"},
        )
    )
    chetan = await as_("chetan")
    await tickets.change_ticket(sm, admin, t4, {"assignee_id": str(ids["chetan"])})
    await messages.post_message(
        sm,
        chetan,
        t4,
        kind=MessageKind.reply,
        body="Thanks {{customer.first_name}}. Which iPhone model and iOS version do you have?",
    )
    await messages.post_message(
        sm,
        chetan,
        t4,
        kind=MessageKind.note,
        body="Card SDK 4.2 has a known issue on iOS 17.6. Checking with the dev team.",
    )
    await tickets.change_ticket(sm, chetan, t4, {"status": Status.pending.value})
    # T-1005: resolved.
    t5 = await ticket(
        NewTicket(
            subject="Change delivery address for #1019",
            contact_email="priya.sharma@example.com",
            contact_name="Priya Sharma",
            description="Please deliver #1019 to my office address instead.",
            channel=Channel.chat,
            type_id=type_ids["Shipping issue"],
            team_id=team_ids["Shipping"],
            fields={"order_id": "#1019", "courier": "Ekart", "tracking": "FMPC0991"},
        )
    )
    pranav = await as_("pranav")
    await tickets.change_ticket(sm, admin, t5, {"assignee_id": str(ids["pranav"])})
    await messages.post_message(
        sm,
        pranav,
        t5,
        kind=MessageKind.reply,
        body="Done, {{customer.first_name}}. The courier has the new address.",
    )
    await tickets.resolve_ticket(sm, pranav, t5)
    # T-1006: unassigned, no team.
    await ticket(
        NewTicket(
            subject="Do you ship to Sri Lanka?",
            contact_email="meera.iyer@example.com",
            contact_name="Meera Iyer",
            description="Hi, do you deliver to Colombo? How long does it take?",
            channel=Channel.web,
            type_id=type_ids["General"],
        )
    )
    # As in the mock: Adarsh is on a break and Anurag is offline.
    await routing.set_my_status(sm, await as_("adarsh"), AgentStatus.on_break)
    await routing.set_my_status(sm, await as_("anurag"), AgentStatus.offline)

    for caller in callers.values():
        await auth.sign_out(sm, caller)
    return [(m.name, email(m.key), m.role.value) for m in (ADMIN, *MEMBERS)]
