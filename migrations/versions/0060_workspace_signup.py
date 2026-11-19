"""Self-service sign-up: a workspace created by anyone who proves their email.

Two global tables (no row-level security: there is no workspace yet to scope them to; the
tenancy test lists them in GLOBAL_TABLES):

- `workspace_signup`: one row per sign-up started from the sign-in page. It holds what the
  person typed (address, names, email), the Argon2 hash of their password until the
  workspace is created, and the 6-digit code only as an HMAC. The client IP is kept only
  as an HMAC, to count sign-ups per address. One pending sign-up per email and requester
  (a new one from the same address supersedes the old; a stranger's can't replace yours).
  Rows are deleted two days after they were made.
- `auth_email_out`: the code emails, apart from the per-workspace `hd_email_out`, so the
  worker sends them on their own lane and no other job (a slow mailbox, a big import, a
  stuck webhook) can delay a code. The text is wiped once sent, failed, or cancelled (a
  newer code for the same sign-up was queued, so an old one never arrives after it).

Nothing existing is changed. Downgrade drops both tables (pending sign-ups are lost;
workspaces already created stay).

Revision ID: 0060
Revises: 0059
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace_signup (
            id               uuid PRIMARY KEY,
            slug             text NOT NULL
                             CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
            workspace_name   text NOT NULL CHECK (length(workspace_name) BETWEEN 1 AND 120),
            email            text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
            admin_name       text NOT NULL CHECK (length(admin_name) BETWEEN 1 AND 120),
            password_hash    text,
            code_hash        bytea NOT NULL CHECK (length(code_hash) = 32),
            code_tries       integer NOT NULL DEFAULT 0 CHECK (code_tries >= 0),
            code_expires_at  timestamptz NOT NULL,
            ip_hash          bytea CHECK (length(ip_hash) = 32),
            status           text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'superseded', 'completed')),
            workspace_id     uuid REFERENCES workspace(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            completed_at     timestamptz,
            CHECK (status <> 'pending' OR password_hash IS NOT NULL),
            CHECK (status <> 'completed' OR completed_at IS NOT NULL)
        )
        """
    )
    # One pending sign-up per email and requester: two from one place can't both wait.
    op.execute(
        "CREATE UNIQUE INDEX workspace_signup_one_pending ON workspace_signup "
        "(email, coalesce(ip_hash, ''::bytea)) WHERE status = 'pending'"
    )
    # The per-address limits, and the sweep of old rows.
    op.execute(
        "CREATE INDEX workspace_signup_ip_age ON workspace_signup (ip_hash, created_at) "
        "WHERE ip_hash IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX workspace_signup_ip_done ON workspace_signup (ip_hash, completed_at) "
        "WHERE status = 'completed'"
    )
    op.execute("CREATE INDEX workspace_signup_age ON workspace_signup (created_at)")

    op.execute(
        """
        CREATE TABLE auth_email_out (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            signup_id        uuid NOT NULL REFERENCES workspace_signup(id) ON DELETE CASCADE,
            to_address       text NOT NULL
                             CHECK (to_address = lower(to_address) AND length(to_address) <= 254),
            subject          text NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
            body_text        text NOT NULL,
            status           text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
            attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at  timestamptz NOT NULL DEFAULT now(),
            last_error       text CHECK (length(last_error) <= 500),
            created_at       timestamptz NOT NULL DEFAULT now(),
            sent_at          timestamptz
        )
        """
    )
    # What the lane reads: emails still to send, due first.
    op.execute(
        "CREATE INDEX auth_email_out_due ON auth_email_out (next_attempt_at) "
        "WHERE status = 'pending'"
    )
    # Code emails per address per hour, and the resend wait of one sign-up.
    op.execute("CREATE INDEX auth_email_out_to_age ON auth_email_out (to_address, created_at)")
    op.execute("CREATE INDEX auth_email_out_signup ON auth_email_out (signup_id, created_at)")
    op.execute("CREATE INDEX auth_email_out_age ON auth_email_out (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE auth_email_out")
    op.execute("DROP TABLE workspace_signup")
