"""The live stream loop (md C11, P7) with the database calls replaced: where a stream starts,
resuming with Last-Event-ID, a `reset`, an event that committed late, no event sent twice,
the heartbeat, being signed out, a changed role, and the limit of open streams per person.

What the SQL reads and who may see which row is tested against a real database in
`tests/db/test_live_updates.py`."""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest

from cedesk.helpdesk.domain import stream as rules
from cedesk.helpdesk.service import stream
from cedesk.helpdesk.service.errors import TooManyAttempts
from cedesk.helpdesk.service.stream import Audience, Slots

WS, MEMBER = uuid4(), uuid4()
TEAM_ROLE = {
    "role": "custom", "scope": "team_only", "reply": True, "assign_any": False,
    "reports": False, "settings": False, "approve": False,
    "role_name": "Billing only", "team_name": "Billing",
}  # fmt: skip


class World:
    """An event log in memory, and the calls the stream makes on it."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.signed_in = True
        self.team: UUID | None = None
        self.teams_asked: list[UUID | None] = []
        self.polls = 0
        # Called at the start of each look for new events (1, 2, 3 ...): things that happen
        # between two looks, such as a row committing.
        self.on_poll: Callable[[int], None] | None = None

    def add(self, seq: int, kind: str = "ticket.updated", **payload: Any) -> None:
        self.rows.append(
            {
                "seq": seq, "kind": kind, "ticket_id": uuid4(), "team_id": None,
                "payload": payload, "created_at": None,
            }
        )  # fmt: skip

    async def newest(self, s: Any, ws: UUID) -> int:
        return max((r["seq"] for r in self.rows), default=0)

    async def exists(self, s: Any, ws: UUID, seq: int) -> bool:
        return any(r["seq"] == seq for r in self.rows)

    async def read_agent(
        self, s: Any, ws: UUID, member: UUID, team_only: UUID | None, *,
        after: int, floor: int, limit: int, overlap: bool,
    ) -> Sequence[dict[str, Any]]:  # fmt: skip
        self.teams_asked.append(team_only)
        if overlap:
            found = [r for r in self.rows if floor < r["seq"] <= after]
        else:
            self.polls += 1
            if self.on_poll is not None:
                self.on_poll(self.polls)
            found = [r for r in self.rows if r["seq"] > after]
        return sorted(found, key=lambda r: r["seq"])[:limit]

    async def live_agent(self, s: Any, session_id: UUID) -> dict[str, Any] | None:
        if not self.signed_in:
            return None
        row: dict[str, Any] = {"member_id": MEMBER, "role": "agent"}
        if self.team is not None:  # a custom role limited to one team
            row |= {**TEAM_ROLE, "team_id": self.team}
        return row


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()

    @asynccontextmanager
    async def session(sm: Any, ws: UUID) -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(stream, "workspace_session", session)
    for name in ("newest", "exists", "read_agent", "live_agent"):
        monkeypatch.setattr(stream.repo, name, getattr(w, name))
    return w


def me() -> Audience:
    return Audience("agent", WS, uuid4(), member_id=MEMBER)


class Clock:
    def __init__(self, step: float = 1.0) -> None:
        self.now, self.step, self.sleeps = 0.0, step, 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += self.step


async def collect(
    clock: Clock | None = None, *, who: Audience | None = None, polls: int = 3, **kw: Any
) -> list[str]:
    clock = clock or Clock()
    out: list[str] = []
    gen = stream.run(
        object(),  # type: ignore[arg-type]
        who or me(),
        max_polls=polls,
        clock=clock,
        sleep=clock.sleep,
        poll=1.0,
        **kw,
    )
    async for chunk in gen:
        out.append(chunk)
    return out


def ids(frames: list[str]) -> list[int]:
    return [int(f.split("\n")[0].removeprefix("id: ")) for f in frames if f.startswith("id: ")]


async def test_a_new_stream_sends_the_retry_hint_then_only_what_happens_after_it_opened(
    world: World,
) -> None:
    world.add(1)
    world.add(2)  # before the stream opened: never sent
    world.on_poll = lambda n: world.add(3) if n == 2 else None
    out = await collect()
    assert out[0] == "retry: 3000\n\n"
    assert ids(out) == [3]


async def test_last_event_id_carries_on_without_losing_one(world: World) -> None:
    for seq in (1, 2, 3, 4):
        world.add(seq)
    out = await collect(last_event_id=2)
    assert ids(out) == [3, 4]
    assert "event: reset" not in "".join(out)


async def test_a_last_event_id_that_is_no_longer_kept_gets_a_reset_and_no_old_events(
    world: World,
) -> None:
    world.add(50)
    out = await collect(last_event_id=7)  # 7 was pruned
    assert out[1] == "event: reset\ndata: {}\n\n"
    assert ids(out) == []  # the client refetches; it isn't sent a partial history


async def test_an_event_that_committed_late_is_sent_once_and_nothing_is_sent_twice(
    world: World,
) -> None:
    world.add(3)  # the stream starts here
    # Between looks: 5 commits first, then 4, which was numbered earlier but committed later.
    world.on_poll = lambda n: world.add(5) if n == 2 else (world.add(4) if n == 3 else None)
    out = await collect(polls=6)
    assert ids(out) == [5, 4]  # each once, 4 found by the overlap read
    assert len(ids(out)) == len(set(ids(out)))


async def test_nothing_from_before_the_connection_is_sent_by_the_overlap_read(
    world: World,
) -> None:
    for seq in (1, 2, 3):
        world.add(seq)
    out = await collect(polls=4)  # no Last-Event-ID: starts after 3
    assert ids(out) == []


async def test_a_quiet_stream_sends_a_heartbeat_and_a_busy_one_does_not(world: World) -> None:
    quiet = await collect(Clock(step=10.0), polls=6, heartbeat=15.0)
    assert quiet.count(": ping\n\n") == 2  # at 20 s and at 40 s

    world.rows.clear()
    world.add(1)
    world.on_poll = lambda n: world.add(10 + n)  # something every look
    busy = await collect(Clock(step=10.0), polls=6, heartbeat=15.0, last_event_id=1)
    assert ": ping\n\n" not in busy and len(ids(busy)) == 6


async def test_a_stream_ends_with_a_reason_when_the_person_is_signed_out(world: World) -> None:
    world.signed_in = False
    clock = Clock(step=30.0)
    out = await collect(clock, polls=10, recheck=60.0)
    assert out[-1] == 'event: end\ndata: {"reason": "signed_out"}\n\n'
    assert clock.sleeps < 10  # it stopped; it did not run out its polls


async def test_a_changed_role_changes_what_is_sent_from_the_next_check(world: World) -> None:
    clock = Clock(step=30.0)
    team = uuid4()

    def limit_to_a_team(n: int) -> None:
        if n == 2:
            world.team = team  # an Admin limited this member to one team

    world.on_poll = limit_to_a_team
    await collect(clock, polls=6, recheck=60.0)
    assert world.teams_asked[0] is None  # first, every ticket
    assert team in world.teams_asked  # then only that team's


async def test_a_stream_stops_when_its_client_goes_away(world: World) -> None:
    clock = Clock()
    gen = stream.run(object(), me(), clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    assert await gen.__anext__() == "retry: 3000\n\n"
    await gen.aclose()  # what the server does on a disconnect: no error, no leftover
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


# The limit of open streams per person


def test_a_person_can_have_a_few_streams_open_and_no_more() -> None:
    slots, who = Slots(limit=2), me()
    with slots.hold(who), slots.hold(who):
        with pytest.raises(TooManyAttempts) as caught:
            slots.check(who)
        assert caught.value.status == 429 and caught.value.retry_after == 5
    slots.check(who)  # both closed: a new one is fine


def test_the_limit_is_per_person_not_per_workspace() -> None:
    slots = Slots(limit=1)
    other = Audience("agent", WS, uuid4(), member_id=uuid4())
    with slots.hold(me()):
        slots.check(other)  # someone else is not held back
        slots.check(Audience("customer", WS, uuid4(), contact_id=uuid4()))


def test_a_slot_is_released_even_when_the_stream_fails() -> None:
    slots, who = Slots(limit=1), me()
    with pytest.raises(RuntimeError), slots.hold(who):
        raise RuntimeError("boom")
    slots.check(who)
    with slots.hold(who):
        pass
    assert slots._open == {}  # nothing left counted


def test_the_default_limit_is_the_rule() -> None:
    slots, who = Slots(), me()
    holds = [slots.hold(who) for _ in range(rules.MAX_STREAMS_PER_MEMBER)]
    for held in holds:
        held.__enter__()
    with pytest.raises(TooManyAttempts):
        slots.check(who)
    for held in holds:
        held.__exit__(None, None, None)
    slots.check(who)
