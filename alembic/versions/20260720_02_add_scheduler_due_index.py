"""Add the scheduler due index."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_02"
down_revision: str | None = "20260715_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	inspector = sa.inspect(op.get_bind())
	columns = {column["name"] for column in inspector.get_columns("topics")}
	if "schedule_due_at" not in columns:
		op.add_column(
			"topics",
			sa.Column("schedule_due_at", sa.DateTime(timezone=True), nullable=True),
		)
	indexes = {index["name"] for index in inspector.get_indexes("topics")}
	if "ix_topics_schedule_due" not in indexes:
		op.create_index(
			"ix_topics_schedule_due",
			"topics",
			["schedule_due_at", "lease_until"],
		)


def downgrade() -> None:
	op.drop_index("ix_topics_schedule_due", table_name="topics")
	op.drop_column("topics", "schedule_due_at")
