"""Times the ticket list and search at 100k tickets in one workspace (md 2B: p95 under
300 ms). Uses the dev database: `uv run python scripts/bench_ticket_list.py [tickets]`.

It creates a fresh workspace named bench-<random>, bulk-inserts the tickets as the owner
role (with that workspace's RLS setting, so policies still apply), then times the real
service calls as the app role and prints p50 / p95 per case.
"""

import asyncio
import secrets
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import text

from cedesk.db import make_engine, make_sessionmaker, workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.domain.tickets import Sort, Status
from cedesk.helpdesk.service import tickets
from cedesk.helpdesk.service.auth import create_workspace
from cedesk.settings import get_settings

RUNS = 40
TARGET_MS = 300

FILL = """
WITH contacts AS (
    INSERT INTO hd_contact (workspace_id, email, name)
    SELECT :ws, 'customer' || g || '@example.com', 'Customer ' || g
    FROM generate_series(1, 5000) g
    RETURNING id, email
)
INSERT INTO hd_ticket (workspace_id, number, subject, status, priority, contact_id, channel,
                       fields, tags, assignee_id, created_at, updated_at, resolved_at)
SELECT :ws, 1000 + g,
       'Order ' || g || ' '
           || (ARRAY['refund', 'late delivery', 'app crash', 'invoice'])[1 + g % 4],
       s.status,
       (ARRAY['urgent', 'high', 'medium', 'low'])[1 + g % 4],
       c.id, 'email',
       jsonb_build_object('order_id', '#' || g),
       CASE WHEN g % 10 = 0 THEN ARRAY['vip'] ELSE '{}' END,
       CASE WHEN g % 3 = 0 THEN CAST(:me AS uuid) END,
       now() - make_interval(mins => :n - g),
       now() - make_interval(mins => (:n - g) / 2),
       CASE WHEN s.status = 'resolved' THEN now() - make_interval(mins => (:n - g) / 3) END
FROM generate_series(1, :n) g
CROSS JOIN LATERAL (SELECT (ARRAY['open', 'pending', 'resolved', 'resolved'])[1 + g % 4]
                    AS status) s
JOIN contacts c ON c.email = 'customer' || (1 + g % 5000) || '@example.com'
"""


async def main(n: int) -> int:
    settings = get_settings()
    owner = make_engine(settings.migration_database_url)
    app = make_engine(settings.database_url)
    sm, owner_sm = make_sessionmaker(app), make_sessionmaker(owner)
    try:
        slug = f"bench-{uuid4().hex[:8]}"
        workspace_id = await create_workspace(
            sm, name="Bench", slug=slug, admin_email="admin@bench.test",
            admin_name="Bench", admin_password=secrets.token_urlsafe(18), seed=False,
        )  # fmt: skip
        async with workspace_session(sm, workspace_id) as s:
            me = UUID(str((await s.execute(text("SELECT id FROM member"))).scalar_one()))
        started = time.perf_counter()
        async with workspace_session(owner_sm, workspace_id) as s:
            await s.execute(text(FILL), {"ws": workspace_id, "n": n, "me": me})
            await s.execute(
                text("INSERT INTO hd_counter VALUES (:ws, :next)"),
                {"ws": workspace_id, "next": n + 1001},
            )
        async with owner.connect() as conn:
            await conn.execute(text("COMMIT"))
            await conn.execute(text("ANALYZE hd_ticket"))
            await conn.execute(text("ANALYZE hd_contact"))
        print(f"{slug}: {n} tickets in {time.perf_counter() - started:.1f}s")

        caller = Caller(workspace_id, me, Role.admin, uuid4())

        def page(**kw: object) -> Callable[[], Awaitable[object]]:
            base: dict[str, object] = {
                "status": None, "scope": "everyone", "priority": None, "team_id": None, "q": None,
            }  # fmt: skip
            return lambda: tickets.list_tickets(sm, caller, **(base | kw))  # type: ignore[arg-type]

        deep = {}
        for sort in Sort:
            cursor = None
            for _ in range(200):  # 200 pages of 50 = row 10,000
                result = await tickets.list_tickets(
                    sm, caller, status=None, scope="everyone", priority=None, team_id=None,
                    q=None, sort=sort, cursor=cursor,
                )  # fmt: skip
                cursor = result.next_cursor
            deep[sort] = cursor

        cases: dict[str, Callable[[], Awaitable[object]]] = {}
        for sort in Sort:
            cases[f"sort={sort.value}"] = page(sort=sort)
            cases[f"sort={sort.value}, page 201"] = page(sort=sort, cursor=deep[sort])
        cases["Open tab"] = page(status=Status.open)
        cases["Resolved tab"] = page(status=Status.resolved)
        cases["scope=mine, Open"] = page(status=Status.open, scope="mine")
        cases["search number T-54321"] = page(q="T-54321")
        cases["search subject 'late deliv'"] = page(q="late deliv")
        cases["search field '#77777'"] = page(q="#77777")
        cases["search email 'customer4242@'"] = page(q="customer4242@")
        cases["search tag 'vip'"] = page(q="vip")
        cases["search, no match"] = page(q="zzzzqqq")

        failed = 0
        print(f"{'case':38} {'p50 ms':>8} {'p95 ms':>8}")
        for name, call in cases.items():
            times = []
            for _ in range(RUNS):
                t0 = time.perf_counter()
                await call()
                times.append((time.perf_counter() - t0) * 1000)
            p95 = statistics.quantiles(times, n=20)[18]
            flag = "" if p95 < TARGET_MS else "  <-- over target"
            failed += bool(flag)
            print(f"{name:38} {statistics.median(times):8.1f} {p95:8.1f}{flag}")
        return 1 if failed else 0
    finally:
        await app.dispose()
        await owner.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)))
