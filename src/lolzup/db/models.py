import enum
import uuid
from datetime import datetime

from sqlalchemy import (
	BigInteger,
	Boolean,
	CheckConstraint,
	DateTime,
	Enum,
	ForeignKey,
	Index,
	Integer,
	LargeBinary,
	String,
	Text,
	UniqueConstraint,
	Uuid,
	func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class EncryptionMode(enum.StrEnum):
	FULL = "full"
	CUSTOM = "custom"
	DISABLED = "disabled"


class AttemptOutcome(enum.StrEnum):
	PENDING = "pending"
	SUCCESS = "success"
	RETRY = "retry"
	ERROR = "error"


class MigrationStatus(enum.StrEnum):
	IDLE = "idle"
	RUNNING = "running"
	FAILED = "failed"


class Base(DeclarativeBase):
	pass


class TimestampMixin:
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), server_default=func.now(), nullable=False
	)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		onupdate=func.now(),
		nullable=False,
	)


class AppSettings(TimestampMixin, Base):
	__tablename__ = "app_settings"
	__table_args__ = (CheckConstraint("id = 1", name="ck_app_settings_singleton"),)

	id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
	encryption_mode: Mapped[EncryptionMode] = mapped_column(
		Enum(EncryptionMode, native_enum=False),
		default=EncryptionMode.FULL,
		nullable=False,
	)
	encryption_categories: Mapped[str] = mapped_column(
		Text, default="[]", nullable=False
	)
	global_bump_enabled_plain: Mapped[bool | None] = mapped_column(Boolean)
	global_bump_enabled_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	global_bump_enabled_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	global_interval_plain: Mapped[int | None] = mapped_column(Integer)
	global_interval_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	global_interval_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	retry_schedule_plain: Mapped[str | None] = mapped_column(Text)
	retry_schedule_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	retry_schedule_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	notify_success_plain: Mapped[bool | None] = mapped_column(Boolean)
	notify_success_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	notify_success_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	notify_errors_plain: Mapped[bool | None] = mapped_column(Boolean)
	notify_errors_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	notify_errors_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	api_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SecretEnvelope(TimestampMixin, Base):
	__tablename__ = "secret_envelopes"
	__table_args__ = (CheckConstraint("id = 1", name="ck_secret_envelopes_singleton"),)

	id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
	salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
	argon_time_cost: Mapped[int] = mapped_column(Integer, nullable=False)
	argon_memory_cost: Mapped[int] = mapped_column(Integer, nullable=False)
	argon_parallelism: Mapped[int] = mapped_column(Integer, nullable=False)
	verifier: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
	wrapped_data_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
	wrapped_data_key_nonce: Mapped[bytes] = mapped_column(
		LargeBinary(12), nullable=False
	)
	api_token_plain: Mapped[str | None] = mapped_column(Text)
	api_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	api_token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))


class KnownUser(TimestampMixin, Base):
	__tablename__ = "known_users"

	id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
	telegram_id_plain: Mapped[int | None] = mapped_column(BigInteger)
	telegram_id_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	telegram_id_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	telegram_id_index: Mapped[bytes] = mapped_column(
		LargeBinary(32), nullable=False, unique=True
	)
	username_plain: Mapped[str | None] = mapped_column(String(64))
	username_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	username_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	username_index: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)


class Administrator(TimestampMixin, Base):
	__tablename__ = "administrators"

	id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("known_users.id", ondelete="CASCADE"), nullable=False, unique=True
	)
	user: Mapped[KnownUser] = relationship()


class Topic(TimestampMixin, Base):
	__tablename__ = "topics"
	__table_args__ = (
		CheckConstraint(
			"custom_interval_plain IS NULL OR custom_interval_plain > 0",
			name="ck_topics_positive_custom_interval",
		),
	)

	id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
	thread_id_plain: Mapped[int | None] = mapped_column(BigInteger)
	thread_id_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	thread_id_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	thread_id_index: Mapped[bytes] = mapped_column(
		LargeBinary(32), nullable=False, unique=True
	)
	title_plain: Mapped[str | None] = mapped_column(Text)
	title_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	title_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	auto_bump_enabled_plain: Mapped[bool | None] = mapped_column(Boolean)
	auto_bump_enabled_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	auto_bump_enabled_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	custom_interval_enabled_plain: Mapped[bool | None] = mapped_column(Boolean)
	custom_interval_enabled_ciphertext: Mapped[bytes | None] = mapped_column(
		LargeBinary
	)
	custom_interval_enabled_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	custom_interval_plain: Mapped[int | None] = mapped_column(Integer)
	custom_interval_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	custom_interval_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	last_success_at_plain: Mapped[datetime | None] = mapped_column(
		DateTime(timezone=True)
	)
	last_success_at_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	last_success_at_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	next_bump_at_plain: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
	next_bump_at_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	next_bump_at_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
	last_error_plain: Mapped[str | None] = mapped_column(Text)
	last_error_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	last_error_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	attempts: Mapped[list["BumpAttempt"]] = relationship(
		back_populates="topic", cascade="all, delete-orphan"
	)


class BumpAttempt(TimestampMixin, Base):
	__tablename__ = "bump_attempts"
	__table_args__ = (
		Index("ix_bump_attempts_topic_created", "topic_id", "created_at"),
	)

	id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
	topic_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
	)
	job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
	outcome: Mapped[AttemptOutcome] = mapped_column(
		Enum(AttemptOutcome, native_enum=False),
		default=AttemptOutcome.PENDING,
		nullable=False,
	)
	is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
	retry_at_plain: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
	retry_at_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	retry_at_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	error_plain: Mapped[str | None] = mapped_column(Text)
	error_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	error_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	topic: Mapped[Topic] = relationship(back_populates="attempts")


class EncryptionMigration(TimestampMixin, Base):
	__tablename__ = "encryption_migrations"
	__table_args__ = (
		CheckConstraint("id = 1", name="ck_encryption_migrations_singleton"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
	status: Mapped[MigrationStatus] = mapped_column(
		Enum(MigrationStatus, native_enum=False),
		default=MigrationStatus.IDLE,
		nullable=False,
	)
	source_policy: Mapped[str | None] = mapped_column(Text)
	target_policy: Mapped[str | None] = mapped_column(Text)
	table_cursor: Mapped[str | None] = mapped_column(String(64))
	row_cursor: Mapped[uuid.UUID | None] = mapped_column(Uuid)
	error: Mapped[str | None] = mapped_column(Text)


class MenuReference(TimestampMixin, Base):
	__tablename__ = "menu_references"
	__table_args__ = (
		UniqueConstraint("user_id", "chat_id_index", name="uq_menu_user_chat"),
	)

	id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("known_users.id", ondelete="CASCADE"), nullable=False
	)
	chat_id_plain: Mapped[int | None] = mapped_column(BigInteger)
	chat_id_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	chat_id_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
	chat_id_index: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
	message_id_plain: Mapped[int | None] = mapped_column(BigInteger)
	message_id_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
	message_id_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
