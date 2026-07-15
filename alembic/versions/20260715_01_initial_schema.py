"""Create the initial schema."""

from collections.abc import Sequence

from alembic import op
from lolzup.db.models import Base

revision: str = "20260715_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	bind = op.get_bind()
	Base.metadata.create_all(bind=bind)


def downgrade() -> None:
	bind = op.get_bind()
	Base.metadata.drop_all(bind=bind)
