import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)

from lolzup.db.migrations import (
	EncryptionMigrationService,
	FieldSpec,
	MigrationBatchError,
)
from lolzup.db.models import (
	AppSettings,
	Base,
	EncryptionMode,
	MigrationStatus,
	SecretEnvelope,
	Topic,
)
from lolzup.db.repositories import (
	EncryptedFieldCodec,
	SecretRepository,
	SettingsRepository,
	TopicRepository,
	UserRepository,
)
from lolzup.scheduler.repository import SchedulerRepository
from lolzup.security.crypto import Argon2Parameters
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault
from lolzup.security.setup import SetupService


class FailingMigrationService(EncryptionMigrationService):
	def __init__(
		self,
		sessions: async_sessionmaker[AsyncSession],
		vault: RuntimeVault,
	) -> None:
		super().__init__(sessions, vault, batch_size=1)
		self._fail_once = True

	def _migrate_fields(
		self,
		model: Any,
		fields: tuple[FieldSpec, ...],
		source: EncryptedFieldCodec,
		target: EncryptedFieldCodec,
	) -> None:
		super()._migrate_fields(model, fields, source, target)
		if self._fail_once and model.__tablename__ == "topics":
			self._fail_once = False
			raise RuntimeError("Injected migration failure")


@pytest.mark.integration
def test_encryption_migration_is_batched_and_activates_policy_last() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		full = EncryptionPolicy(EncryptionMode.FULL)
		disabled = EncryptionPolicy(EncryptionMode.DISABLED)
		sessions = async_sessionmaker(engine, expire_on_commit=False)

		async with sessions.begin() as session:
			await SetupService(
				SecretRepository(session),
				vault,
				argon_parameters=Argon2Parameters(
					time_cost=1,
					memory_cost=8192,
					parallelism=1,
				),
			).initialize("password", "api-token")
			full_codec = EncryptedFieldCodec(full, vault)
			await SettingsRepository(session, full_codec).get_or_create()
			await UserRepository(session, full_codec).upsert(100, "Owner")
			await TopicRepository(session, full_codec).add(
				5523020,
				"First",
				datetime(2026, 7, 23, tzinfo=UTC),
			)
			await TopicRepository(session, full_codec).add(
				5523021,
				"Second",
				datetime(2026, 7, 24, tzinfo=UTC),
			)

		migration = FailingMigrationService(sessions, vault)
		started = await migration.start(disabled)
		assert started.status is MigrationStatus.RUNNING

		async with sessions() as session:
			settings = await session.get(AppSettings, 1)
			assert settings is not None
			assert settings.encryption_mode is EncryptionMode.FULL
			assert await SchedulerRepository(session, full_codec).migration_running()

		failed = False
		while (await migration.status()).status is not MigrationStatus.IDLE:
			try:
				await migration.resume()
			except MigrationBatchError:
				failed = True
				status = await migration.status()
				assert status.status is MigrationStatus.FAILED
				async with sessions() as session:
					settings = await session.get(AppSettings, 1)
					assert settings is not None
					assert settings.encryption_mode is EncryptionMode.FULL
					assert await SchedulerRepository(
						session,
						full_codec,
					).migration_running()
		assert failed

		async with sessions() as session:
			topics = list(await session.scalars(select(Topic)))
			assert all(topic.thread_id_plain is not None for topic in topics)
			assert all(topic.thread_id_ciphertext is None for topic in topics)
			settings = await session.get(AppSettings, 1)
			assert settings is not None
			assert settings.encryption_mode is EncryptionMode.DISABLED
			secret = await session.get(SecretEnvelope, 1)
			assert secret is not None
			assert secret.api_token_plain == "api-token"
			assert secret.api_token_ciphertext is None
			assert not await SchedulerRepository(
				session,
				EncryptedFieldCodec(disabled, vault),
			).migration_running()

		custom = EncryptionPolicy(
			EncryptionMode.CUSTOM,
			frozenset({DataCategory.TOPICS}),
		)
		await migration.start(custom)
		while (await migration.status()).status is not MigrationStatus.IDLE:
			await migration.resume()

		async with sessions() as session:
			topics = list(await session.scalars(select(Topic)))
			assert all(topic.thread_id_plain is None for topic in topics)
			assert all(topic.thread_id_ciphertext is not None for topic in topics)
			assert all(topic.auto_bump_enabled_plain is not None for topic in topics)
			secret = await session.get(SecretEnvelope, 1)
			assert secret is not None
			assert secret.api_token_plain is None
			assert secret.api_token_ciphertext is not None

		await migration.start(disabled)
		while (await migration.status()).status is not MigrationStatus.IDLE:
			await migration.resume()
		await migration.start(full)
		while (await migration.status()).status is not MigrationStatus.IDLE:
			await migration.resume()

		async with sessions() as session:
			topics = list(await session.scalars(select(Topic)))
			assert all(topic.thread_id_ciphertext is not None for topic in topics)
			assert all(
				topic.auto_bump_enabled_ciphertext is not None for topic in topics
			)
			settings = await session.get(AppSettings, 1)
			assert settings is not None
			assert settings.encryption_mode is EncryptionMode.FULL

		await engine.dispose()

	asyncio.run(scenario())
