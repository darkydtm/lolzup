import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lolzup.db.models import (
	AppSettings,
	BumpAttempt,
	EncryptionMigration,
	EncryptionMode,
	KnownUser,
	MenuReference,
	MigrationStatus,
	SecretEnvelope,
	Topic,
)
from lolzup.db.repositories import EncryptedFieldCodec
from lolzup.security.crypto import CryptoEnvelope, decrypt, encrypt
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault
from lolzup.security.setup import API_TOKEN_CONTEXT

DEFAULT_MIGRATION_BATCH_SIZE = 100


class MigrationInProgressError(RuntimeError):
	pass


class MigrationBatchError(RuntimeError):
	pass


@dataclass(frozen=True, slots=True)
class EncryptionMigrationRecord:
	status: MigrationStatus
	source_policy: EncryptionPolicy
	target_policy: EncryptionPolicy
	table_cursor: str | None
	row_cursor: uuid.UUID | None
	error: str | None


@dataclass(frozen=True, slots=True)
class FieldSpec:
	category: DataCategory
	name: str


MIGRATION_TABLES = (
	"secret_envelopes",
	"app_settings",
	"known_users",
	"topics",
	"bump_attempts",
	"menu_references",
)

MODEL_FIELDS: dict[str, tuple[type[Any], tuple[FieldSpec, ...]]] = {
	"app_settings": (
		AppSettings,
		(
			FieldSpec(DataCategory.SCHEDULING, "global_bump_enabled"),
			FieldSpec(DataCategory.SCHEDULING, "global_interval"),
			FieldSpec(DataCategory.SCHEDULING, "retry_schedule"),
			FieldSpec(DataCategory.SCHEDULING, "notify_success"),
			FieldSpec(DataCategory.SCHEDULING, "notify_errors"),
		),
	),
	"known_users": (
		KnownUser,
		(
			FieldSpec(DataCategory.TELEGRAM_IDENTITIES, "telegram_id"),
			FieldSpec(DataCategory.TELEGRAM_IDENTITIES, "username"),
		),
	),
	"topics": (
		Topic,
		(
			FieldSpec(DataCategory.TOPICS, "thread_id"),
			FieldSpec(DataCategory.TOPICS, "title"),
			FieldSpec(DataCategory.SCHEDULING, "auto_bump_enabled"),
			FieldSpec(DataCategory.SCHEDULING, "custom_interval_enabled"),
			FieldSpec(DataCategory.SCHEDULING, "custom_interval"),
			FieldSpec(DataCategory.HISTORY, "last_success_at"),
			FieldSpec(DataCategory.SCHEDULING, "next_bump_at"),
			FieldSpec(DataCategory.HISTORY, "last_error"),
		),
	),
	"bump_attempts": (
		BumpAttempt,
		(
			FieldSpec(DataCategory.HISTORY, "retry_at"),
			FieldSpec(DataCategory.HISTORY, "error"),
		),
	),
	"menu_references": (
		MenuReference,
		(
			FieldSpec(DataCategory.TELEGRAM_IDENTITIES, "chat_id"),
			FieldSpec(DataCategory.TELEGRAM_IDENTITIES, "message_id"),
		),
	),
}


class EncryptionMigrationService:
	def __init__(
		self,
		sessions: async_sessionmaker[AsyncSession],
		vault: RuntimeVault,
		*,
		batch_size: int = DEFAULT_MIGRATION_BATCH_SIZE,
	) -> None:
		if batch_size <= 0:
			raise ValueError("Migration batch size must be positive")
		self._sessions = sessions
		self._vault = vault
		self._batch_size = batch_size

	async def start(
		self,
		target_policy: EncryptionPolicy,
	) -> EncryptionMigrationRecord:
		async with self._sessions.begin() as session:
			settings = await self._settings(session)
			source_policy = self._active_policy(settings)
			model = await self._migration(session)
			if model.status is not MigrationStatus.IDLE:
				raise MigrationInProgressError
			model.status = MigrationStatus.RUNNING
			model.source_policy = source_policy.serialize()
			model.target_policy = target_policy.serialize()
			model.table_cursor = MIGRATION_TABLES[0]
			model.row_cursor = None
			model.error = None
			await session.flush()
			return self._record(model)

	async def resume(self) -> EncryptionMigrationRecord:
		failure: Exception | None = None
		async with self._sessions.begin() as session:
			model = await self._migration(session)
			if model.status is MigrationStatus.IDLE:
				return self._record(model)
			if model.source_policy is None or model.target_policy is None:
				raise ValueError("Migration policies are missing")
			model.status = MigrationStatus.RUNNING
			model.error = None
			source = EncryptionPolicy.deserialize(model.source_policy)
			target = EncryptionPolicy.deserialize(model.target_policy)
			try:
				await self._resume_batch(session, model, source, target)
			except Exception as error:
				model.status = MigrationStatus.FAILED
				model.error = "Migration batch failed"
				failure = error
			await session.flush()
			record = self._record(model)
		if failure is not None:
			raise MigrationBatchError from failure
		return record

	async def status(self) -> EncryptionMigrationRecord:
		async with self._sessions() as session:
			return self._record(await self._migration(session))

	async def _resume_batch(
		self,
		session: AsyncSession,
		migration: EncryptionMigration,
		source: EncryptionPolicy,
		target: EncryptionPolicy,
	) -> None:
		table = migration.table_cursor
		if table is None:
			await self._complete(session, migration, target)
			return
		if table == "secret_envelopes":
			await self._migrate_secret(session, target)
			self._advance_table(migration, table)
			return
		if table == "app_settings":
			await self._migrate_singleton_fields(
				await self._settings(session),
				MODEL_FIELDS[table][1],
				source,
				target,
			)
			self._advance_table(migration, table)
			return

		model_type, fields = MODEL_FIELDS[table]
		query = select(model_type).order_by(model_type.id).limit(self._batch_size)
		if migration.row_cursor is not None:
			query = query.where(model_type.id > migration.row_cursor)
		rows = list(await session.scalars(query))
		source_codec = EncryptedFieldCodec(source, self._vault)
		target_codec = EncryptedFieldCodec(target, self._vault)
		for row in rows:
			self._migrate_fields(row, fields, source_codec, target_codec)
		if len(rows) == self._batch_size:
			last_id = rows[-1].id
			migration.row_cursor = last_id if isinstance(last_id, uuid.UUID) else None
			return
		self._advance_table(migration, table)

	async def _migrate_secret(
		self,
		session: AsyncSession,
		target: EncryptionPolicy,
	) -> None:
		model = await session.get(SecretEnvelope, 1)
		if model is None:
			return
		value: str | None
		if model.api_token_ciphertext is not None:
			if model.api_token_nonce is None:
				raise ValueError("Encrypted API token is missing its nonce")
			value = decrypt(
				self._vault.require_key(),
				CryptoEnvelope(
					model.api_token_ciphertext,
					model.api_token_nonce,
				),
				API_TOKEN_CONTEXT,
			).decode()
		else:
			value = model.api_token_plain
		if value is None:
			return
		if target.encrypts(DataCategory.SECRETS):
			stored = encrypt(
				self._vault.require_key(),
				value.encode(),
				API_TOKEN_CONTEXT,
			)
			model.api_token_plain = None
			model.api_token_ciphertext = stored.ciphertext
			model.api_token_nonce = stored.nonce
		else:
			model.api_token_plain = value
			model.api_token_ciphertext = None
			model.api_token_nonce = None

	async def _migrate_singleton_fields(
		self,
		model: Any,
		fields: tuple[FieldSpec, ...],
		source: EncryptionPolicy,
		target: EncryptionPolicy,
	) -> None:
		self._migrate_fields(
			model,
			fields,
			EncryptedFieldCodec(source, self._vault),
			EncryptedFieldCodec(target, self._vault),
		)

	def _migrate_fields(
		self,
		model: Any,
		fields: tuple[FieldSpec, ...],
		source: EncryptedFieldCodec,
		target: EncryptedFieldCodec,
	) -> None:
		for field in fields:
			context = f"{model.__tablename__}:{model.id}:{field.name}"
			value = source.decode(
				field.category,
				context,
				getattr(model, f"{field.name}_plain"),
				getattr(model, f"{field.name}_ciphertext"),
				getattr(model, f"{field.name}_nonce"),
			)
			stored = target.encode(field.category, context, value)
			setattr(model, f"{field.name}_plain", stored.plain)
			setattr(model, f"{field.name}_ciphertext", stored.ciphertext)
			setattr(model, f"{field.name}_nonce", stored.nonce)

	async def _complete(
		self,
		session: AsyncSession,
		migration: EncryptionMigration,
		target: EncryptionPolicy,
	) -> None:
		settings = await self._settings(session)
		settings.encryption_mode = target.mode
		settings.encryption_categories = target.serialize()
		migration.status = MigrationStatus.IDLE
		migration.table_cursor = None
		migration.row_cursor = None
		migration.error = None

	@staticmethod
	def _advance_table(migration: EncryptionMigration, current: str) -> None:
		index = MIGRATION_TABLES.index(current)
		migration.table_cursor = (
			MIGRATION_TABLES[index + 1] if index + 1 < len(MIGRATION_TABLES) else None
		)
		migration.row_cursor = None

	@staticmethod
	async def _settings(session: AsyncSession) -> AppSettings:
		settings = await session.get(AppSettings, 1)
		if settings is None:
			settings = AppSettings(
				id=1,
				encryption_mode=EncryptionMode.FULL,
				encryption_categories="[]",
			)
			session.add(settings)
			await session.flush()
		return settings

	@staticmethod
	async def _migration(session: AsyncSession) -> EncryptionMigration:
		model = await session.get(EncryptionMigration, 1)
		if model is None:
			settings = await EncryptionMigrationService._settings(session)
			policy = EncryptionMigrationService._active_policy(settings)
			model = EncryptionMigration(
				id=1,
				status=MigrationStatus.IDLE,
				source_policy=policy.serialize(),
				target_policy=policy.serialize(),
			)
			session.add(model)
			await session.flush()
		return model

	@staticmethod
	def _active_policy(settings: AppSettings) -> EncryptionPolicy:
		return policy_from_settings(settings)

	@staticmethod
	def _record(model: EncryptionMigration) -> EncryptionMigrationRecord:
		if model.source_policy is None or model.target_policy is None:
			raise ValueError("Migration policy metadata is missing")
		return EncryptionMigrationRecord(
			status=model.status,
			source_policy=EncryptionPolicy.deserialize(model.source_policy),
			target_policy=EncryptionPolicy.deserialize(model.target_policy),
			table_cursor=model.table_cursor,
			row_cursor=model.row_cursor,
			error=model.error,
		)


async def load_active_policy(session: AsyncSession) -> EncryptionPolicy:
	settings = await session.get(AppSettings, 1)
	if settings is None:
		return EncryptionPolicy(EncryptionMode.FULL)
	return policy_from_settings(settings)


def policy_from_settings(settings: AppSettings) -> EncryptionPolicy:
	if settings.encryption_categories.startswith("{"):
		stored = EncryptionPolicy.deserialize(settings.encryption_categories)
		return EncryptionPolicy(settings.encryption_mode, stored.categories)
	return EncryptionPolicy(settings.encryption_mode)
