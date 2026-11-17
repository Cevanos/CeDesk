"""Times an import of many tickets through the real worker (Section 8 of the migration task).

    CEDESK_DATABASE_URL=postgresql+asyncpg://cedesk_app:...@localhost:5447/cedesk_test \
    CEDESK_MIGRATION_DATABASE_URL=postgresql+asyncpg://cedesk_owner:...@localhost:5447/cedesk_test \
    uv run python scripts/bench_import.py [tickets] [--files]

A simulated help desk (no network) hands out `tickets` tickets in pages of 100, each with a
requester (5,000 distinct customers), 3 tags, 2 custom fields, a CSAT on every 10th and 4
messages; `--files` adds a 20 KB file to every 20th ticket (stored on local disk in a temp
folder). The real `run_due` worker imports them into a fresh workspace with RLS on, 30
seconds a pass as in production. Then:

1. the same import is run again: every ticket must be skipped, nothing duplicated;
2. a sync look over 1,000 changed tickets (a new message each).

It prints throughput, the slowest pass, peak memory, and checks the counts in the database.
At the end it deletes the workspace it made (only that one). Never point it at production.
"""

import asyncio
import resource
import secrets
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from cedesk.db import make_engine, make_sessionmaker, workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Attachment,
    Contact,
    Counts,
    Csat,
    Group,
    Message,
    Page,
    Ticket,
)
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.service.auth import create_workspace
from cedesk.helpdesk.service.imports import framework
from cedesk.settings import get_settings
from cedesk.storage.local import LocalStore

PAGE = 100
PASS_SECONDS = 30.0
FILE = b"%PDF-" + b"x" * 20_000
START = datetime(2024, 1, 1, tzinfo=UTC)


class Simulated:
    source = "zendesk"
    status_map: Mapping[str, str] = {"solved": "resolved", "open": "open", "pending": "pending"}

    def __init__(self, n: int, *, files: bool) -> None:
        self.n, self.files = n, files
        self.changed_ids: list[int] = []
        self.fetched = 0

    def ticket(self, i: int, *, extra: bool = False) -> Ticket:
        at = START + timedelta(minutes=i)
        who = Contact(f"u{i % 5000}", f"customer{i % 5000}@example.org", f"Customer {i % 5000}")
        files = (
            [Attachment("invoice.pdf", f"https://files.example/{i}", len(FILE), "application/pdf")]
            if self.files and i % 20 == 0
            else []
        )
        messages = [
            Message("customer", f"Order {i}: the parcel arrived damaged.", at, external_id=f"{i}-1", attachments=files),  # noqa: E501
            Message("agent", "Sorry to hear that, we are on it.", at + timedelta(minutes=5), author_email="agent@example.org", external_id=f"{i}-2"),  # noqa: E501
            Message("note", "Checked with the warehouse.", at + timedelta(minutes=9), author_email="agent@example.org", external_id=f"{i}-3"),  # noqa: E501
            Message("customer", "Thanks!", at + timedelta(minutes=30), external_id=f"{i}-4"),
        ]  # fmt: skip
        if extra:
            messages.append(
                Message(
                    "customer", "One more thing.", at + timedelta(days=400), external_id=f"{i}-5"
                )
            )
        return Ticket(
            external_id=str(i), subject=f"Order {i} damaged", status="solved" if i % 3 else "open",
            created_at=at, requester=who, messages=messages, tags=["damaged", "parcel", f"t{i % 7}"],  # noqa: E501
            fields={"order_id": f"#{i}", "region": "EU" if i % 2 else "IN"}, group_id="g1",
            csat=Csat(5, "Great") if i % 10 == 0 else None, updated_at=at + timedelta(minutes=30),
        )  # fmt: skip

    async def check(self) -> AccountInfo:
        return AccountInfo("bench.zendesk.com")

    async def discover(self) -> Counts:
        return Counts(0, self.n, 0, 0, 1)

    async def groups(self) -> Sequence[Group]:
        return [Group("g1", "Support")]

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind != "tickets":
            return Page([], None)
        start = int(cursor["offset"]) if cursor else 0
        end = min(start + PAGE, self.n)
        return Page(
            [self.ticket(i) for i in range(start, end)], {"offset": end} if end < self.n else None
        )

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        start = int(cursor["offset"]) if cursor else 0
        chunk = self.changed_ids[start : start + PAGE]
        end = start + len(chunk)
        more = {"offset": end} if end < len(self.changed_ids) else None
        return Page([self.ticket(i, extra=True) for i in chunk], more)

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        self.fetched += 1
        return FILE


async def drain(sm: Any, importers: framework.Importers, label: str) -> tuple[float, int, float]:
    """Worker passes until nothing is due; seconds, passes, slowest pass."""
    started, passes, slowest = time.perf_counter(), 0, 0.0
    while True:
        t = time.perf_counter()
        worked = await framework.run_due(sm, importers, budget=PASS_SECONDS)
        slowest = max(slowest, time.perf_counter() - t)
        if not worked:
            break
        passes += 1
    took = time.perf_counter() - started
    print(f"  {label}: {took:.1f}s in {passes} passes (slowest pass {slowest:.1f}s)")
    return took, passes, slowest


async def count(sm: Any, ws: UUID, sql: str) -> int:
    async with workspace_session(sm, ws) as s:
        return int((await s.execute(text(sql))).scalar_one())


async def main(n: int, files: bool) -> int:
    settings = get_settings()
    if "cedesk_test" not in settings.database_url and "--any-db" not in sys.argv:
        print("Refusing: point CEDESK_DATABASE_URL at the test database (or pass --any-db).")
        return 2
    app, owner = make_engine(settings.database_url), make_engine(settings.migration_database_url)
    sm, owner_sm = make_sessionmaker(app), make_sessionmaker(owner)
    src = Simulated(n, files=files)
    folder = tempfile.TemporaryDirectory(prefix="cedesk-bench-")
    store = LocalStore(Path(folder.name), secrets.token_bytes(32))

    async def build(source: str, s: Mapping[str, Any], secret: str | None) -> Simulated:
        return src

    importers = framework.make_importers(settings.secret_key.get_secret_value(), store, build=build)
    slug = f"bench-import-{uuid4().hex[:8]}"
    ws = await create_workspace(
        sm, name="Bench import", slug=slug, admin_email="admin@bench.test", admin_name="Bench",
        admin_password=secrets.token_urlsafe(18), seed=False,
    )  # fmt: skip
    failed = 0
    try:
        async with workspace_session(sm, ws) as s:
            me = UUID(str((await s.execute(text("SELECT id FROM member"))).scalar_one()))
        caller = Caller(ws, me, Role.admin, uuid4())
        conn = await framework.connect(
            sm, importers, caller, source="zendesk", settings={"subdomain": "bench"},
            credentials={"token": "bench-not-a-secret"},
        )  # fmt: skip
        print(f"{slug}: {n} tickets x 4 messages{' + files' if files else ''}, pages of {PAGE}")

        run = await framework.start_run(sm, caller, conn.id, {"kinds": ["tickets"]}, sync=True)
        took, _, _ = await drain(sm, importers, "first import")
        print(f"  = {n / took:.0f} tickets/s, {4 * n / took:.0f} messages/s")
        made = await count(sm, ws, "SELECT count(*) FROM hd_ticket")
        msgs = await count(sm, ws, "SELECT count(*) FROM hd_message WHERE kind <> 'system'")
        ok = made == n and msgs == 4 * n
        print(f"  tickets {made}, messages {msgs}: {'ok' if ok else 'WRONG'}")
        failed += not ok

        # 2. The same import again: everything skipped, nothing twice.
        async with workspace_session(sm, ws) as s:
            await s.execute(
                text("UPDATE hd_import_run SET sync_on = false WHERE id = :i"), {"i": run.id}
            )
        conn2 = await framework.connect(
            sm, importers, caller, source="zendesk", settings={"subdomain": "bench"},
            credentials={"token": "bench-not-a-secret"},
        )  # fmt: skip
        again = await framework.start_run(sm, caller, conn2.id, {"kinds": ["tickets"]}, sync=True)
        took2, _, _ = await drain(sm, importers, "same import again")
        print(f"  = {n / took2:.0f} tickets/s checked")
        same = await count(sm, ws, "SELECT count(*) FROM hd_ticket") == n
        print(f"  no duplicates: {'ok' if same else 'WRONG'}")
        failed += not same

        # 3. A sync look over 1,000 changed tickets.
        src.changed_ids = list(range(0, min(n, 100_000), max(1, min(n, 100_000) // 1000)))[:1000]
        async with workspace_session(sm, ws) as s:
            await s.execute(
                text("UPDATE hd_import_run SET next_run_at = now() WHERE id = :i"), {"i": again.id}
            )
        took3, _, _ = await drain(sm, importers, "sync look, 1,000 changed")
        added = await count(
            sm, ws, "SELECT count(*) FROM hd_message WHERE body = 'One more thing.'"
        )
        print(f"  = {len(src.changed_ids) / took3:.0f} tickets/s; new messages {added}: "
              f"{'ok' if added == len(src.changed_ids) else 'WRONG'}")  # fmt: skip
        failed += added != len(src.changed_ids)

        if files:
            stored = await count(sm, ws, "SELECT count(*) FROM hd_attachment")
            print(f"  files stored {stored} (expected {n // 20 + (1 if n % 20 else 0)}), "
                  f"fetched {src.fetched}")  # fmt: skip
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"  peak memory {peak:.0f} MB")
    finally:
        async with workspace_session(owner_sm, ws) as s:
            await s.execute(text("DELETE FROM workspace WHERE id = :ws"), {"ws": ws})
        folder.cleanup()
        await app.dispose()
        await owner.dispose()
    return failed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(asyncio.run(main(int(args[0]) if args else 100_000, "--files" in sys.argv)))
