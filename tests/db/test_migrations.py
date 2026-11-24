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


async def test_0071_to_0072_add_campaigns_and_go_back_down(owner_engine: Any) -> None:
    """The suppression list, campaign settings and the test email's HTML part (0071);
    campaigns and their recipients (0072). Going below 0071 removes them all."""
    import asyncio

    from sqlalchemy import text

    tables = ("hd_suppression", "hd_campaign_settings", "hd_campaign", "hd_campaign_recipient")
    html = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'hd_email_out' AND column_name = 'body_html'"
    )
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0070")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
        assert await conn.scalar(text(html)) == 0
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            forced = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced is True
        assert await conn.scalar(text(html)) == 1


async def test_0073_to_0077_add_the_reporting_tables_and_go_back_down(owner_engine: Any) -> None:
    """Backlog snapshots, report indexes, saved reports, dashboards and schedules; going
    below 0073 removes them all, and coming back up leaves row-level security forced."""
    import asyncio

    from sqlalchemy import text

    tables = (
        "hd_backlog_snapshot", "hd_saved_report", "hd_dashboard", "hd_dashboard_card",
        "hd_report_schedule", "hd_report_schedule_recipient",
    )  # fmt: skip
    indexes = ("hd_ticket_created_range",)
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "0072")
    async with owner_engine.connect() as conn:
        for name in (*tables, *indexes):
            assert await conn.scalar(text(f"SELECT to_regclass('{name}')")) is None
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for name in indexes:
            assert await conn.scalar(text(f"SELECT to_regclass('{name}')")) is not None
        for table in tables:
            forced = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced is True


async def test_0078_gives_the_archive_age_its_own_last_change(
    client: Any, app_sessionmaker: Any, owner_engine: Any
) -> None:
    """Before 0078 the archive age and retention wrote one "last changed", so saving the
    archive age moved retention's (staging, 23 Nov). 0078 takes each one's from its latest
    change in the audit log, and going back down keeps the newer of the two."""
    import asyncio
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy import text

    from tests.db.helpers import bearer, new_workspace, ok, sign_in

    slug = await new_workspace(app_sessionmaker)
    h = bearer(await sign_in(client, slug, "admin@example.com"))
    ws = UUID(ok(await client.get("/api/v1/me", headers=h))["workspace"]["id"])
    ok(await client.put("/api/v1/retention", headers=h, json={"closed_ticket_days": 30}))
    ok(await client.put("/api/v1/archive/settings", headers=h, json={"after_days": 200}))

    entries = {
        area: ok(await client.get("/api/v1/audit", headers=h, params={"area": area}))["entries"]
        for area in ("retention", "archive")
    }

    def changed(area: str) -> datetime:
        return datetime.fromisoformat(entries[area][0]["at"])

    cfg = alembic_config()
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0077")
        async with owner_engine.connect() as conn:
            await conn.execute(text("ALTER TABLE hd_retention NO FORCE ROW LEVEL SECURITY"))
            try:
                merged = await conn.scalar(
                    text("SELECT updated_at FROM hd_retention WHERE workspace_id = :ws"),
                    {"ws": ws},
                )
            finally:
                await conn.rollback()
        # Down, the one pair keeps the newer save: the archive age's, made after retention.
        assert merged > changed("retention")
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")

    kept = ok(await client.get("/api/v1/retention", headers=h))
    age = ok(await client.get("/api/v1/archive/settings", headers=h))
    assert datetime.fromisoformat(kept["updated_at"]) == changed("retention")
    assert datetime.fromisoformat(age["updated_at"]) == changed("archive")
    assert kept["updated_by_name"] and age["updated_by_name"]


async def test_0083_adds_help_center_languages_and_wont_go_down_while_translations_exist(
    owner_engine: Any,
) -> None:
    """Dropping the translations would lose them: the downgrade refuses and changes nothing;
    once they are gone it goes down and back up with row-level security forced."""
    import asyncio

    from sqlalchemy import text

    cfg = alembic_config()
    await empty_archive_async()
    tables = ("hd_help_center_settings", "hd_article_translation")
    async with owner_engine.begin() as conn:
        for table in ("workspace", "hd_article", "hd_article_translation"):
            await conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('P4') RETURNING id"))
        article = await conn.scalar(
            text("INSERT INTO hd_article (workspace_id, title) VALUES (:ws, 'A') RETURNING id"),
            {"ws": ws},
        )
        await conn.execute(
            text(
                "INSERT INTO hd_article_translation (workspace_id, article_id, locale, title) "
                "VALUES (:ws, :a, 'hi', 'ए')"
            ),
            {"ws": ws, "a": article},
        )
        for table in ("workspace", "hd_article", "hd_article_translation"):
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    with pytest.raises(RuntimeError, match="holds 1 translations"):
        await asyncio.to_thread(command.downgrade, cfg, "0082")
    async with owner_engine.connect() as conn:  # rolled back: still there, still forced
        for table in tables:
            forced = "SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"
            assert await conn.scalar(text(forced), {"t": table}) is True
    await empty_archive_async()
    await asyncio.to_thread(command.downgrade, cfg, "0082")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
        columns = (
            "SELECT count(*) FROM information_schema.columns WHERE "
            "(table_name = 'hd_article_category' AND column_name = 'name_translations') OR "
            "(table_name = 'hd_article_vote' AND column_name = 'locale')"
        )
        assert await conn.scalar(text(columns)) == 0
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            forced = "SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"
            assert await conn.scalar(text(forced), {"t": table}) is True
        assert await conn.scalar(text(columns)) == 2


async def test_0084_adds_help_center_seo_columns_and_goes_back_down(owner_engine: Any) -> None:
    """Slug, meta text and canonical address on articles and translations (with checks that
    keep them tidy), and the indexable switch, off by default."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config()
    columns = (
        "SELECT count(*) FROM information_schema.columns WHERE column_name IN "
        "('slug', 'meta_title', 'meta_description', 'canonical_url') "
        "AND table_name IN ('hd_article', 'hd_article_translation')"
    )
    switch = (
        "SELECT count(*) FROM information_schema.columns WHERE "
        "table_name = 'hd_help_center_settings' AND column_name = 'seo_indexable'"
    )
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(columns)) == 8
        assert await conn.scalar(text(switch)) == 1
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE workspace NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE hd_article NO FORCE ROW LEVEL SECURITY"))
        ws = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('P4S') RETURNING id"))
        art = await conn.scalar(
            text("INSERT INTO hd_article (workspace_id, title) VALUES (:ws, 'A') RETURNING id"),
            {"ws": ws},
        )
    try:
        for column, bad in (
            ("slug", "Bad Slug"), ("slug", "ab"), ("slug", "double--hyphen"),
            ("meta_title", ""), ("meta_description", "x" * 321),
            ("canonical_url", "http://example.com/"), ("canonical_url", "https://exa mple.com/"),
        ):  # fmt: skip
            with pytest.raises(IntegrityError):
                async with owner_engine.begin() as conn:
                    await conn.execute(
                        text(f"UPDATE hd_article SET {column} = :v WHERE id = :id"),  # noqa: S608
                        {"v": bad, "id": art},
                    )
        async with owner_engine.begin() as conn:  # a good one is accepted
            await conn.execute(
                text("UPDATE hd_article SET slug = 'refund-policy' WHERE id = :id"), {"id": art}
            )
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("DELETE FROM hd_article WHERE id = :id"), {"id": art})
            await conn.execute(text("DELETE FROM workspace WHERE id = :id"), {"id": ws})
            await conn.execute(text("ALTER TABLE hd_article FORCE ROW LEVEL SECURITY"))
            await conn.execute(text("ALTER TABLE workspace FORCE ROW LEVEL SECURITY"))
    await asyncio.to_thread(command.downgrade, cfg, "0083")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(columns)) == 0
        assert await conn.scalar(text(switch)) == 0
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text(columns)) == 8


async def test_0085_adds_help_domains_and_a_trigger_keeps_the_directory_in_step(
    owner_engine: Any,
) -> None:
    """One domain per workspace; only a verified one is in the global directory, which the app
    role can read but not write; two workspaces can both hold a domain as pending, only one can
    have it verified; going down drops both tables."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config()
    tables = ("hd_help_domain", "help_domain_directory")
    async with owner_engine.connect() as conn:
        forced = "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'hd_help_domain'"
        assert await conn.scalar(text(forced)) is True
        for table, right, expected in (
            ("help_domain_directory", "SELECT", True),
            ("help_domain_directory", "INSERT", False),
            ("help_domain_directory", "UPDATE", False),
            ("help_domain_directory", "DELETE", False),
            ("hd_help_domain", "INSERT", True),
        ):
            query = "SELECT has_table_privilege('cedesk_app', CAST(:t AS text), CAST(:r AS text))"
            assert await conn.scalar(text(query), {"t": table, "r": right}) is expected
    token = "t" * 24

    async def directory(conn: Any) -> list[tuple[str, Any]]:
        rows = await conn.execute(text("SELECT domain, workspace_id FROM help_domain_directory"))
        return [(r[0], r[1]) for r in rows]

    async with owner_engine.begin() as conn:
        await conn.execute(text("TRUNCATE hd_help_domain, help_domain_directory"))
        await conn.execute(text("ALTER TABLE workspace NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE hd_help_domain NO FORCE ROW LEVEL SECURITY"))
        one = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('P4a') RETURNING id"))
        two = await conn.scalar(text("INSERT INTO workspace (name) VALUES ('P4b') RETURNING id"))
    insert = (
        "INSERT INTO hd_help_domain (workspace_id, domain, verify_token) "
        "VALUES (:ws, 'help.example.com', :t)"
    )
    verify = (
        "UPDATE hd_help_domain SET status = 'verified', verified_at = now() "
        "WHERE workspace_id = :ws"
    )
    try:
        async with owner_engine.begin() as conn:  # pending: not in the directory
            await conn.execute(text(insert), {"ws": one, "t": token})
            await conn.execute(text(insert), {"ws": two, "t": token + "2"})  # a second claim
            assert await directory(conn) == []
        async with owner_engine.begin() as conn:  # verified: in it
            await conn.execute(text(verify), {"ws": one})
            assert await directory(conn) == [("help.example.com", one)]
        with pytest.raises(IntegrityError):  # the other cannot verify the same domain
            async with owner_engine.begin() as conn:
                await conn.execute(text(verify), {"ws": two})
        async with owner_engine.begin() as conn:
            assert await directory(conn) == [("help.example.com", one)]  # nothing changed
            await conn.execute(  # a domain changed to another and pending: out of it
                text("UPDATE hd_help_domain SET status = 'pending', verified_at = NULL "
                     "WHERE workspace_id = :ws"), {"ws": one})  # fmt: skip
            assert await directory(conn) == []
            await conn.execute(text(verify), {"ws": two})  # now the other can have it
            assert await directory(conn) == [("help.example.com", two)]
            await conn.execute(text("DELETE FROM hd_help_domain WHERE workspace_id = :ws"),
                               {"ws": two})  # fmt: skip
            assert await directory(conn) == []
        with pytest.raises(IntegrityError):  # status and verified_at must agree
            async with owner_engine.begin() as conn:
                await conn.execute(
                    text("UPDATE hd_help_domain SET status = 'verified' WHERE workspace_id = :ws"),
                    {"ws": one},
                )
        with pytest.raises(IntegrityError):  # one domain per workspace
            async with owner_engine.begin() as conn:
                await conn.execute(text(insert), {"ws": one, "t": token})
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("DELETE FROM hd_help_domain"))
            await conn.execute(text("DELETE FROM workspace WHERE id IN (:a, :b)"),
                               {"a": one, "b": two})  # fmt: skip
            await conn.execute(text("ALTER TABLE hd_help_domain FORCE ROW LEVEL SECURITY"))
            await conn.execute(text("ALTER TABLE workspace FORCE ROW LEVEL SECURITY"))
    await asyncio.to_thread(command.downgrade, cfg, "0084")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is None
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with owner_engine.connect() as conn:
        for table in tables:
            assert await conn.scalar(text(f"SELECT to_regclass('{table}')")) is not None
