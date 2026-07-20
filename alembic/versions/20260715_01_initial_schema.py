"""Create the initial schema."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_table(
	inspector: Any,
	name: str,
	*elements: Any,
) -> None:
	if not inspector.has_table(name):
		op.create_table(name, *elements)


def upgrade() -> None:
	inspector = sa.inspect(op.get_bind())
	_create_table(
		inspector,
		"app_settings",
		sa.Column("id", sa.Integer(), nullable=False),
		sa.Column(
			"encryption_mode",
			sa.Enum(
				"FULL",
				"CUSTOM",
				"DISABLED",
				name="encryptionmode",
				native_enum=False,
			),
			nullable=False,
		),
		sa.Column("encryption_categories", sa.Text(), nullable=False),
		sa.Column("global_bump_enabled_plain", sa.Boolean(), nullable=True),
		sa.Column("global_bump_enabled_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("global_bump_enabled_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("global_interval_plain", sa.Integer(), nullable=True),
		sa.Column("global_interval_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("global_interval_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("retry_schedule_plain", sa.JSON(), nullable=True),
		sa.Column("retry_schedule_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("retry_schedule_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("notify_success_plain", sa.Boolean(), nullable=True),
		sa.Column("notify_success_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("notify_success_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("notify_errors_plain", sa.Boolean(), nullable=True),
		sa.Column("notify_errors_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("notify_errors_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("api_paused", sa.Boolean(), nullable=False),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
		sa.PrimaryKeyConstraint("id"),
	)
	_create_table(
		inspector,
		"secret_envelopes",
		sa.Column("id", sa.Integer(), nullable=False),
		sa.Column("salt", sa.LargeBinary(), nullable=False),
		sa.Column("argon_time_cost", sa.Integer(), nullable=False),
		sa.Column("argon_memory_cost", sa.Integer(), nullable=False),
		sa.Column("argon_parallelism", sa.Integer(), nullable=False),
		sa.Column("verifier", sa.LargeBinary(), nullable=False),
		sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
		sa.Column("wrapped_data_key_nonce", sa.LargeBinary(12), nullable=False),
		sa.Column("api_token_plain", sa.Text(), nullable=True),
		sa.Column("api_token_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("api_token_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.CheckConstraint("id = 1", name="ck_secret_envelopes_singleton"),
		sa.PrimaryKeyConstraint("id"),
	)
	_create_table(
		inspector,
		"known_users",
		sa.Column("id", sa.Uuid(), nullable=False),
		sa.Column("telegram_id_plain", sa.BigInteger(), nullable=True),
		sa.Column("telegram_id_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("telegram_id_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("telegram_id_index", sa.LargeBinary(32), nullable=False),
		sa.Column("username_plain", sa.String(64), nullable=True),
		sa.Column("username_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("username_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("username_index", sa.LargeBinary(32), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("telegram_id_index"),
		sa.UniqueConstraint("username_index"),
	)
	_create_table(
		inspector,
		"topics",
		sa.Column("id", sa.Uuid(), nullable=False),
		sa.Column("thread_id_plain", sa.BigInteger(), nullable=True),
		sa.Column("thread_id_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("thread_id_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("thread_id_index", sa.LargeBinary(32), nullable=False),
		sa.Column("title_plain", sa.Text(), nullable=True),
		sa.Column("title_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("title_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("auto_bump_enabled_plain", sa.Boolean(), nullable=True),
		sa.Column("auto_bump_enabled_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("auto_bump_enabled_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("custom_interval_enabled_plain", sa.Boolean(), nullable=True),
		sa.Column(
			"custom_interval_enabled_ciphertext",
			sa.LargeBinary(),
			nullable=True,
		),
		sa.Column(
			"custom_interval_enabled_nonce",
			sa.LargeBinary(12),
			nullable=True,
		),
		sa.Column("custom_interval_plain", sa.Integer(), nullable=True),
		sa.Column("custom_interval_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("custom_interval_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"last_success_at_plain",
			sa.DateTime(timezone=True),
			nullable=True,
		),
		sa.Column("last_success_at_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("last_success_at_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"next_bump_at_plain",
			sa.DateTime(timezone=True),
			nullable=True,
		),
		sa.Column("next_bump_at_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("next_bump_at_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
		sa.Column("last_error_plain", sa.Text(), nullable=True),
		sa.Column("last_error_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("last_error_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.CheckConstraint(
			"custom_interval_plain IS NULL OR custom_interval_plain > 0",
			name="ck_topics_positive_custom_interval",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("thread_id_index"),
	)
	_create_table(
		inspector,
		"encryption_migrations",
		sa.Column("id", sa.Integer(), nullable=False),
		sa.Column(
			"status",
			sa.Enum(
				"IDLE",
				"RUNNING",
				"FAILED",
				name="migrationstatus",
				native_enum=False,
			),
			nullable=False,
		),
		sa.Column("source_policy", sa.Text(), nullable=True),
		sa.Column("target_policy", sa.Text(), nullable=True),
		sa.Column("table_cursor", sa.String(64), nullable=True),
		sa.Column("row_cursor", sa.Uuid(), nullable=True),
		sa.Column("error", sa.Text(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.CheckConstraint("id = 1", name="ck_encryption_migrations_singleton"),
		sa.PrimaryKeyConstraint("id"),
	)
	_create_table(
		inspector,
		"administrators",
		sa.Column("id", sa.Uuid(), nullable=False),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["user_id"],
			["known_users.id"],
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("user_id"),
	)
	_create_table(
		inspector,
		"bump_attempts",
		sa.Column("id", sa.Uuid(), nullable=False),
		sa.Column("topic_id", sa.Uuid(), nullable=False),
		sa.Column("job_id", sa.String(64), nullable=False),
		sa.Column(
			"outcome",
			sa.Enum(
				"PENDING",
				"SUCCESS",
				"RETRY",
				"ERROR",
				name="attemptoutcome",
				native_enum=False,
			),
			nullable=False,
		),
		sa.Column("is_manual", sa.Boolean(), nullable=False),
		sa.Column(
			"retry_at_plain",
			sa.DateTime(timezone=True),
			nullable=True,
		),
		sa.Column("retry_at_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("retry_at_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("error_plain", sa.Text(), nullable=True),
		sa.Column("error_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("error_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["topic_id"],
			["topics.id"],
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("job_id"),
	)
	attempt_indexes = {
		index["name"]
		for index in sa.inspect(op.get_bind()).get_indexes("bump_attempts")
	}
	if "ix_bump_attempts_topic_created" not in attempt_indexes:
		op.create_index(
			"ix_bump_attempts_topic_created",
			"bump_attempts",
			["topic_id", "created_at"],
		)
	_create_table(
		inspector,
		"menu_references",
		sa.Column("id", sa.Uuid(), nullable=False),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("chat_id_plain", sa.BigInteger(), nullable=True),
		sa.Column("chat_id_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("chat_id_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column("chat_id_index", sa.LargeBinary(32), nullable=False),
		sa.Column("message_id_plain", sa.BigInteger(), nullable=True),
		sa.Column("message_id_ciphertext", sa.LargeBinary(), nullable=True),
		sa.Column("message_id_nonce", sa.LargeBinary(12), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["user_id"],
			["known_users.id"],
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint(
			"user_id",
			"chat_id_index",
			name="uq_menu_user_chat",
		),
	)


def downgrade() -> None:
	op.drop_table("menu_references")
	op.drop_index(
		"ix_bump_attempts_topic_created",
		table_name="bump_attempts",
	)
	op.drop_table("bump_attempts")
	op.drop_table("administrators")
	op.drop_table("encryption_migrations")
	op.drop_table("topics")
	op.drop_table("known_users")
	op.drop_table("secret_envelopes")
	op.drop_table("app_settings")
