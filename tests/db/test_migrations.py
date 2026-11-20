"""Guard: migrations have one head and survive a full down-and-up cycle."""

from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from tests.conftest import alembic_config, empty_archive, empty_archive_async

pytestmark = pytest.mark.db


def test_there_is_exactly_one_head() -> None:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"Two people added migrations in parallel: {heads}"


def test_downgrade_to_base_and_back_up(migrated_db: None) -> None:
    cfg = alembic_config()
    empty_archive()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_members_from_before_0012_stay_available(
    app_sessionmaker: Any, owner_engine: Any
) -> None:
    """0012 makes new agents start Offline; members who existed before keep routing as
    Available (a forced-RLS table once hid them from this copy)."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0011")
        # A member as 0011 stored them (plain SQL: today's code expects today's columns).
        # It has no routing row yet.
        async with owner_engine.begin() as conn:
            for table in ("workspace", "member"):
                await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            ws = await conn.scalar(
                text("INSERT INTO workspace (name) VALUES ('Before 0012') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO member (workspace_id, email, name, role, password_hash) "
                    "VALUES (:ws, 'old@example.com', 'Old', 'agent', 'x')"
                ),
                {"ws": ws},
            )
            for table in ("workspace", "member"):
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        await conn.execute(text("ALTER TABLE member NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE hd_agent_state NO FORCE ROW LEVEL SECURITY"))
        try:
            missing = await conn.scalar(
                text(
                    "SELECT count(*) FROM member m LEFT JOIN hd_agent_state s "
                    "ON s.member_id = m.id WHERE m.role <> 'light_agent' "
                    "AND s.status IS DISTINCT FROM 'available'"
                )
            )
        finally:
            await conn.rollback()
    assert missing == 0


async def test_0049_wont_go_down_while_tickets_are_archived(owner_engine: Any) -> None:
    """Dropping the archive would lose its tickets: the downgrade refuses and changes
    nothing; once the archive is empty it goes down and back up."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_ticket_archive"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('O3') RETURNING id"))
        await conn.execute(
            text(
                "INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, "
                "priority, channel, created_at, ticket, messages) VALUES (:ws, gen_random_uuid(), "
                "1, 'Old', 'resolved', 'low', 'web', now(), '{}', '[]')"
            ),
            {"ws": ws},
        )
        for table in ("workspace", "hd_ticket_archive"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    with pytest.raises(RuntimeError, match="holds 1 archived tickets"):
        await asyncio.to_thread(command.downgrade, cfg, "0048")
    async with owner_engine.connect() as conn:  # rolled back: still there, still forced
        assert await conn.scalar(text("SELECT to_regclass('hd_ticket_archive')")) is not None
        forced = "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'hd_ticket_archive'"
        assert await conn.scalar(text(forced)) is True
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0048")
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_0053_wont_go_down_while_exports_or_holds_exist(owner_engine: Any) -> None:
    """Going below 0053 would lose retention exports and Governance hold approvals: the
    downgrade refuses and changes nothing; once they are gone it goes down and back up."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_retention_export"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('O2') RETURNING id"))
        await conn.execute(
            text(
                "INSERT INTO hd_retention_export (workspace_id, object_key, tickets, size, "
                "expires_at) VALUES (:ws, 'k.json', 1, 2, now())"
            ),
            {"ws": ws},
        )
        for table in ("workspace", "hd_retention_export"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    with pytest.raises(RuntimeError, match="1 retention exports exist"):
        await asyncio.to_thread(command.downgrade, cfg, "0052")
    async with owner_engine.connect() as conn:  # rolled back: still there
        assert await conn.scalar(text("SELECT to_regclass('hd_retention_export')")) is not None
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0052")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('hd_retention_export')")) is None
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_0052_upgrades_an_archive_from_0049s_first_version(owner_engine: Any) -> None:
    """Servers ran 0049 as first written. A ticket archived then: 0049 still refuses to go
    down while it is there, and 0052 fills `source_id` and `file_bytes` from what it kept
    (its `related` and `email_ids` start empty: that version didn't keep them)."""
    import asyncio
    import json

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0051")
    try:
        async with owner_engine.begin() as conn:
            for table in ("workspace", "hd_ticket_archive"):
                await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            ws = await conn.scalar(
                text("INSERT INTO workspace (name) VALUES ('0052') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO hd_ticket_archive (workspace_id, id, number, subject, status, "
                    "priority, channel, source, created_at, ticket, messages, files) VALUES "
                    "(:ws, gen_random_uuid(), 7, 'Old', 'resolved', 'low', 'web', 'zendesk', "
                    "now(), CAST(:ticket AS jsonb), '[]', CAST(:files AS jsonb))"
                ),
                {
                    "ws": ws,
                    "ticket": json.dumps({"source_id": "z-9"}),
                    "files": json.dumps([{"name": "a", "size": 100}, {"name": "b", "size": 23}]),
                },
            )
            for table in ("workspace", "hd_ticket_archive"):
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        with pytest.raises(RuntimeError, match="downgrading 0049 would"):
            await asyncio.to_thread(command.downgrade, cfg, "0048")

        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with owner_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE hd_ticket_archive NO FORCE ROW LEVEL SECURITY"))
            row = (
                await conn.execute(
                    text(
                        "SELECT source_id, file_bytes, related, email_ids FROM "
                        "hd_ticket_archive WHERE workspace_id = :ws"
                    ),
                    {"ws": ws},
                )
            ).one()
            await conn.execute(text("ALTER TABLE hd_ticket_archive FORCE ROW LEVEL SECURITY"))
        assert tuple(row) == ("z-9", 123, {}, [])
    finally:
        await empty_archive_async()
        await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_0054_goes_down_and_back_up(owner_engine: Any) -> None:
    """0054 adds the import failures table and the run's retry count; going down drops both
    (only notes are lost, nothing imported), and going up brings them back with RLS on."""
    import asyncio

    from sqlalchemy import text

    retry_column = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'hd_import_run' AND column_name = 'retry_attempts'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0053")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('hd_import_failure')")) is None
        assert await conn.scalar(text(retry_column)) == 0
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('hd_import_failure')")) is not None
        assert await conn.scalar(text(retry_column)) == 1
        forced = await conn.scalar(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'hd_import_failure'")
        )
        assert forced is True


async def test_0055_adds_the_resumed_event_and_goes_back_down(owner_engine: Any) -> None:
    """Going below 0055 deletes the `resumed` history events (the audit log keeps its own)
    and puts the old list of kinds back; going up allows them again."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config()
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_import_connection", "hd_import_event"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('R5') RETURNING id"))
        cid = await conn.scalar(
            text(
                "INSERT INTO hd_import_connection (workspace_id, source, label) "
                "VALUES (:ws, 'zendesk', 'acme') RETURNING id"
            ),
            {"ws": ws},
        )
        await conn.execute(
            text(
                "INSERT INTO hd_import_event (workspace_id, connection_id, kind) "
                "VALUES (:ws, :c, 'resumed')"
            ),
            {"ws": ws, "c": cid},
        )
        for table in ("workspace", "hd_import_connection", "hd_import_event"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    await asyncio.to_thread(command.downgrade, cfg, "0054")
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE hd_import_event NO FORCE ROW LEVEL SECURITY"))
        left = "SELECT count(*) FROM hd_import_event WHERE kind = 'resumed'"
        assert await conn.scalar(text(left)) == 0
        with pytest.raises(IntegrityError):
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "INSERT INTO hd_import_event (workspace_id, connection_id, kind) "
                        "VALUES (:ws, :c, 'resumed')"
                    ),
                    {"ws": ws, "c": cid},
                )
        await conn.execute(text("ALTER TABLE hd_import_event FORCE ROW LEVEL SECURITY"))
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE workspace NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("DELETE FROM workspace WHERE id = :ws"), {"ws": ws})
        await conn.execute(text("ALTER TABLE workspace FORCE ROW LEVEL SECURITY"))


async def test_0056_adds_the_lease_token_and_goes_back_down(owner_engine: Any) -> None:
    import asyncio

    from sqlalchemy import text

    column = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'hd_import_run' AND column_name = 'lease_token'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0055")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(column)) == 0
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(column)) == 1


async def test_0057_adds_the_seen_table_and_goes_back_down(owner_engine: Any) -> None:
    import asyncio

    from sqlalchemy import text

    table = "SELECT to_regclass('hd_import_seen') IS NOT NULL"
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0056")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(table)) is False
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(table)) is True


async def test_0058_adds_the_credentials_updated_event_and_goes_back_down(
    owner_engine: Any,
) -> None:
    import asyncio

    from sqlalchemy import text

    check = (
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'hd_import_event_kind_check'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0057")
    async with owner_engine.connect() as conn:
        assert "credentials_updated" not in await conn.scalar(text(check))
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert "credentials_updated" in await conn.scalar(text(check))


async def test_0059_adds_contact_details_and_recipients_and_goes_back_down(
    owner_engine: Any,
) -> None:
    import asyncio

    from sqlalchemy import text

    cols = (
        "SELECT table_name || '.' || column_name FROM information_schema.columns "
        "WHERE (table_name, column_name) IN (('hd_contact', 'phone'), "
        "('hd_contact', 'organization'), ('hd_message', 'recipients')) ORDER BY 1"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0058")
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text(cols))).all() == []
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert [r[0] for r in (await conn.execute(text(cols))).all()] == [
            "hd_contact.organization", "hd_contact.phone", "hd_message.recipients"
        ]  # fmt: skip


async def test_0061_to_0064_add_the_inbox_tools_and_go_back_down(owner_engine: Any) -> None:
    """Snooze column and trigger, saved views, auto-close setting and followers; going
    below 0061 removes them all (and the two new notification kinds)."""
    import asyncio

    from sqlalchemy import text

    tables = ("hd_saved_view", "hd_auto_close", "hd_ticket_follower")
    column = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'hd_ticket' AND column_name = 'snoozed_until'"
    )
    kinds = (
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'hd_notification_kind_check'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0060")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
        assert await conn.scalar(text(column)) == 0
        assert "snooze_ended" not in await conn.scalar(text(kinds))
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            forced = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced is True
        assert await conn.scalar(text(column)) == 1
        assert "following" in await conn.scalar(text(kinds))


async def test_0065_to_0068_add_the_contact_tables_and_go_back_down(owner_engine: Any) -> None:
    """Contact notes, fields and values, merge (other addresses, history) and segments;
    going below 0065 removes them all and the contact's attributes column."""
    import asyncio

    from sqlalchemy import text

    tables = (
        "hd_contact_note", "hd_contact_field", "hd_contact_alias", "hd_contact_merge",
        "hd_segment",
    )  # fmt: skip
    column = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'hd_contact' AND column_name = 'attributes'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0064")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
        assert await conn.scalar(text(column)) == 0
        assert await conn.scalar(text("SELECT to_regclass('hd_contact_name')")) is None
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            forced = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced is True
        assert await conn.scalar(text(column)) == 1


async def test_0069_to_0070_add_automation_and_go_back_down(owner_engine: Any) -> None:
    """The run log, the rule limits and the snooze_ended event (0069); workflows and
    their versions (0070). Going below 0069 removes them all."""
    import asyncio

    from sqlalchemy import text

    tables = ("hd_automation_run", "hd_flow", "hd_flow_version")
    limits = (
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'hd_rule' "
        "AND column_name IN ('max_runs_per_hour', 'max_runs_per_day')"
    )
    event_check = (
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'hd_rule'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) LIKE '%customer_replied%'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0068")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
        assert await conn.scalar(text(limits)) == 0
        assert "snooze_ended" not in await conn.scalar(text(event_check))
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            forced = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced is True
        assert await conn.scalar(text(limits)) == 2
        assert "snooze_ended" in await conn.scalar(text(event_check))
