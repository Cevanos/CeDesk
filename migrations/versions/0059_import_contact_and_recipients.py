"""Imports keep more of what the source knows: a contact's phone and organization, and a
message's recipients (to, cc, bcc).

- `hd_contact.phone` (up to 50 characters) and `hd_contact.organization` (up to 200): filled
  by an import only when blank, never over what an agent set; erasure clears both.
- `hd_message.recipients`: `{"to": [...], "cc": [...], "bcc": [...]}`, what the source says
  the message was addressed to. Kept apart from `hd_message.email`, which the reply and
  threading code reads: an imported message's recipients never change who a new reply goes
  to.

Downgrade drops the three columns (the values are lost; the import can be run again).

Revision ID: 0059
Revises: 0058
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_contact "
        "ADD COLUMN phone text CHECK (length(phone) <= 50), "
        "ADD COLUMN organization text CHECK (length(organization) <= 200)"
    )
    op.execute(
        "ALTER TABLE hd_message ADD COLUMN recipients jsonb "
        "CHECK (recipients IS NULL OR jsonb_typeof(recipients) = 'object')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_message DROP COLUMN recipients")
    op.execute("ALTER TABLE hd_contact DROP COLUMN phone, DROP COLUMN organization")
