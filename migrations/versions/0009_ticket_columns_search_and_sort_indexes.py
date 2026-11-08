"""Phase 1 data model, search and list speed (md 4.1, T8, T10, 2B performance).

1. The `hd_ticket` columns from md 4.1 that later features fill in: the Cevanos handover
   (conversation_id, accepted, handover), triage (topic, sentiment, language), parent and
   merge links, and the import / inbound source. `hd_message` gets the email header
   (from, to, cc, message_id) and the channel's own message id.
   Not here: sla_policy_id (comes with the SLA tables) and message attachments (come
   with `hd_attachment`).
2. Search (T8): `search_text` on each ticket holds the subject, field values and tags,
   one per line, kept up to date by a trigger, so search reads one column instead of
   unpacking every field and tag.
   No trigram or full-text index: under forced RLS Postgres only lets an index apply a
   filter before the workspace policy when the operator is leakproof, and ILIKE and @@
   are not, so such an index is never used (checked with EXPLAIN). Measured without it:
   search p95 about 120 ms at 100k tickets (scripts/bench_ticket_list.py).
3. The ticket list's cursor pages (2B: p95 under 300 ms at 100k tickets): one index per
   sort order, on exactly the key expressions the list query orders by
   (repo/tickets.py `SORT_KEYS`). Times are turned into numbers with
   `extract(epoch FROM (t - fixed instant))`, the immutable form an index can hold.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same expressions as repo/tickets.py `SORT_KEYS`; an index serves a query only when the
# expressions match exactly. tests/db/test_ticket_speed.py checks the plans use them.
EPOCH = "extract(epoch FROM ({} - TIMESTAMPTZ '1970-01-01 00:00:00+00'))"
PRIORITY_KEYS = (
    "(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END)",
    "(CASE WHEN status <> 'resolved' "
    "THEN array_position(ARRAY['urgent', 'high', 'medium', 'low'], priority) ELSE 0 END)",
    "(CASE WHEN status = 'resolved' THEN -" + EPOCH.format("resolved_at") + " "
    "ELSE " + EPOCH.format("created_at") + " END)",
    "(CASE WHEN status = 'resolved' THEN -number ELSE number END)",
)

SEARCH_FUNCTION = """
CREATE FUNCTION hd_ticket_search_text() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_text := concat_ws(
        E'\\n',
        NEW.subject,
        (SELECT string_agg(f.value, E'\\n') FROM jsonb_each_text(NEW.fields) f),
        array_to_string(NEW.tags, E'\\n')
    );
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE hd_ticket
            ADD COLUMN conversation_id text CHECK (length(conversation_id) BETWEEN 1 AND 100),
            ADD COLUMN accepted boolean NOT NULL DEFAULT true,
            ADD COLUMN handover jsonb CHECK (jsonb_typeof(handover) = 'object'),
            ADD COLUMN topic text CHECK (length(topic) BETWEEN 1 AND 60),
            ADD COLUMN sentiment text CHECK (sentiment IN ('negative', 'neutral', 'positive')),
            ADD COLUMN language text CHECK (language ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'),
            ADD COLUMN parent_id uuid,
            ADD COLUMN merged_into_id uuid,
            ADD COLUMN source text CHECK (source ~ '^[a-z][a-z0-9_-]{0,39}$'),
            ADD COLUMN source_id text CHECK (length(source_id) BETWEEN 1 AND 200),
            ADD COLUMN search_text text NOT NULL DEFAULT '',
            ADD CONSTRAINT hd_ticket_source_id_needs_source
                CHECK (source_id IS NULL OR source IS NOT NULL),
            ADD CONSTRAINT hd_ticket_not_own_parent CHECK (parent_id <> id),
            ADD CONSTRAINT hd_ticket_not_merged_into_itself CHECK (merged_into_id <> id),
            ADD CONSTRAINT hd_ticket_parent FOREIGN KEY (workspace_id, parent_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE SET NULL (parent_id),
            ADD CONSTRAINT hd_ticket_merged_into FOREIGN KEY (workspace_id, merged_into_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE SET NULL (merged_into_id)
        """
    )
    # An imported or inbound record becomes one ticket, however often it is sent (M, C1).
    op.execute(
        "CREATE UNIQUE INDEX hd_ticket_source ON hd_ticket (workspace_id, source, source_id) "
        "WHERE source_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX hd_ticket_conversation ON hd_ticket (workspace_id, conversation_id) "
        "WHERE conversation_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX hd_ticket_children ON hd_ticket (workspace_id, parent_id) "
        "WHERE parent_id IS NOT NULL"
    )

    op.execute(
        """
        ALTER TABLE hd_message
            ADD COLUMN email jsonb CHECK (jsonb_typeof(email) = 'object'),
            ADD COLUMN channel_message_id text
                CHECK (length(channel_message_id) BETWEEN 1 AND 200)
        """
    )

    # Search text, kept up to date by a trigger, filled in for existing tickets.
    op.execute(SEARCH_FUNCTION)
    op.execute(
        "CREATE TRIGGER hd_ticket_search_text BEFORE INSERT OR UPDATE OF subject, fields, tags "
        "ON hd_ticket FOR EACH ROW EXECUTE FUNCTION hd_ticket_search_text()"
    )
    op.execute("ALTER TABLE hd_ticket NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE hd_ticket SET subject = subject")  # fires the trigger for every row
    op.execute("ALTER TABLE hd_ticket FORCE ROW LEVEL SECURITY")

    # One index per ticket-list sort order.
    op.execute(
        "CREATE INDEX hd_ticket_sort_priority ON hd_ticket (workspace_id, "
        + ", ".join(PRIORITY_KEYS)
        + ")"
    )
    op.execute(
        "CREATE INDEX hd_ticket_sort_created ON hd_ticket "
        f"(workspace_id, ({EPOCH.format('created_at')}), number)"
    )
    op.execute(
        "CREATE INDEX hd_ticket_sort_updated ON hd_ticket "
        f"(workspace_id, ({EPOCH.format('updated_at')}), number)"
    )


def downgrade() -> None:
    for index in (
        "hd_ticket_sort_updated",
        "hd_ticket_sort_created",
        "hd_ticket_sort_priority",
        "hd_ticket_children",
        "hd_ticket_conversation",
        "hd_ticket_source",
    ):
        op.execute(f"DROP INDEX {index}")
    op.execute("DROP TRIGGER hd_ticket_search_text ON hd_ticket")
    op.execute("DROP FUNCTION hd_ticket_search_text()")
    op.execute("ALTER TABLE hd_message DROP COLUMN email, DROP COLUMN channel_message_id")
    op.execute(
        """
        ALTER TABLE hd_ticket
            DROP CONSTRAINT hd_ticket_parent,
            DROP CONSTRAINT hd_ticket_merged_into,
            DROP COLUMN conversation_id,
            DROP COLUMN accepted,
            DROP COLUMN handover,
            DROP COLUMN topic,
            DROP COLUMN sentiment,
            DROP COLUMN language,
            DROP COLUMN parent_id,
            DROP COLUMN merged_into_id,
            DROP COLUMN source,
            DROP COLUMN source_id,
            DROP COLUMN search_text
        """
    )
