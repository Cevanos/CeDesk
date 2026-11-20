"""Times the report engine and the live view at 100k tickets in one workspace (feature-gap
plan 3.1: p95 under 1 s). Uses the database in CEDESK_DATABASE_URL and
CEDESK_MIGRATION_DATABASE_URL (the dev database by default; point both at a throwaway
database to keep your own data out of it):
`uv run python scripts/bench_reports.py [tickets] [--keep]`.

It creates a fresh workspace named bench-<random>, bulk-inserts the tickets as the owner
role (with that workspace's RLS setting, so policies still apply), times the real service
calls as the app role and prints p50 / p95 per case, then which of the new indexes were
used. The workspace is deleted afterwards unless `--keep` is given.
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
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.service import live, report_engine
from cedesk.helpdesk.service.auth import create_workspace
from cedesk.settings import get_settings

RUNS = 20
TARGET_MS = 1000

FILL = """
WITH contacts AS (
    INSERT INTO hd_contact (workspace_id, email, name)
    SELECT :ws, 'customer' || g || '@example.com', 'Customer ' || g
    FROM generate_series(1, 5000) g
    RETURNING id, email
)
INSERT INTO hd_ticket (workspace_id, number, subject, status, priority, contact_id, channel,
                       fields, tags, assignee_id, created_at, updated_at, resolved_at,
                       first_reply_at, topic, sla_due_at)
SELECT :ws, 1000 + g, 'Order ' || g, s.status,
       (ARRAY['urgent', 'high', 'medium', 'low'])[1 + g % 4],
       c.id, (ARRAY['email', 'web', 'chat'])[1 + g % 3], '{}',
       CASE WHEN g % 10 = 0 THEN ARRAY['vip'] WHEN g % 7 = 0 THEN ARRAY['refund', 'late']
            ELSE '{}' END,
       CASE WHEN g % 3 = 0 THEN CAST(:me AS uuid) END,
       now() - make_interval(mins => (:n - g) * 5),
       now() - make_interval(mins => (:n - g) * 5 / 2),
       CASE WHEN s.status = 'resolved' THEN now() - make_interval(mins => (:n - g) * 5 / 3) END,
       CASE WHEN g % 2 = 0 THEN now() - make_interval(mins => (:n - g) * 5 - 20) END,
       (ARRAY['billing', 'shipping', 'other'])[1 + g % 3],
       now() + make_interval(mins => g % 5000 - 2500)
FROM generate_series(1, :n) g
CROSS JOIN LATERAL (SELECT (ARRAY['open', 'pending', 'resolved', 'resolved'])[1 + g % 4]
                    AS status) s
JOIN contacts c ON c.email = 'customer' || (1 + g % 5000) || '@example.com'
"""


def run(me: Caller, sm: object, metric: str, group_by: str, rng: str = "last_30d"):
    definition = defs.parse({"metric": metric, "group_by": group_by, "range": {"preset": rng}})
    return lambda: report_engine.run(sm, me, definition, timeout_ms=30_000, slots=None)  # type: ignore[arg-type]


async def main(n: int, keep: bool) -> int:
    settings = get_settings()
    owner = make_engine(settings.migration_database_url)
    app = make_engine(settings.database_url)
    sm, owner_sm = make_sessionmaker(app), make_sessionmaker(owner)
    workspace_id: UUID | None = None
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
        print(f"{slug}: {n} tickets in {time.perf_counter() - started:.1f}s")

        caller = Caller(workspace_id, me, Role.admin, uuid4())
        cases: dict[str, Callable[[], Awaitable[object]]] = {
            "created per day, 30 days": run(caller, sm, "created", "day"),
            "created per week, 365 days": run(caller, sm, "created", "week", "last_365d"),
            "created by priority, 90 days": run(caller, sm, "created", "priority", "last_90d"),
            "created by channel, 30 days": run(caller, sm, "created", "channel"),
            "created by tag, 90 days": run(caller, sm, "created", "tag", "last_90d"),
            "created by team, 30 days": run(caller, sm, "created", "team"),
            "resolved per day, 30 days": run(caller, sm, "resolved", "day"),
            "first response by priority": run(caller, sm, "avg_first_response", "priority"),
            "resolution per week, 365 days": run(caller, sm, "avg_resolution", "week", "last_365d"),
            "reopen rate per week": run(caller, sm, "reopen_rate", "week"),
            "open now by priority": run(caller, sm, "open_now", "priority"),
            "open now by tag": run(caller, sm, "open_now", "tag"),
            "live view": lambda: live.live_view(
                sm, caller, team_id=None, window_minutes=60, cache=live.LiveCache(),
                timeout_ms=30_000,
            ),
        }  # fmt: skip
        failed = 0
        print(f"{'case':34} {'p50 ms':>8} {'p95 ms':>8}")
        for name, call in cases.items():
            times = []
            for _ in range(RUNS):
                t0 = time.perf_counter()
                await call()
                times.append((time.perf_counter() - t0) * 1000)
            times.sort()
            p50, p95 = statistics.median(times), times[int(len(times) * 0.95) - 1]
            flag = "" if p95 <= TARGET_MS else "  <-- over the target"
            failed += p95 > TARGET_MS
            print(f"{name:34} {p50:8.1f} {p95:8.1f}{flag}")
        async with owner.connect() as conn:
            used = await conn.execute(
                text("SELECT indexrelname, idx_scan FROM pg_stat_user_indexes "
                     "WHERE indexrelname IN ('hd_ticket_created_range', "
                     "'hd_ticket_resolved_at', 'hd_ticket_sla_alert') ORDER BY 1")
            )  # fmt: skip
            for name, scans in used:
                print(f"index {name}: {scans} scans")
        print(f"{failed} case(s) over {TARGET_MS} ms")
        return 1 if failed else 0
    finally:
        if workspace_id is not None and not keep:
            async with owner.begin() as conn:
                await conn.execute(
                    text("DELETE FROM workspace WHERE id = :id"), {"id": workspace_id}
                )
        await app.dispose()
        await owner.dispose()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(asyncio.run(main(int(args[0]) if args else 100_000, "--keep" in sys.argv)))
