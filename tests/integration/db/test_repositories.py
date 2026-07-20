import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lolzup.db.models import Base, EncryptionMode, KnownUser, Topic
from lolzup.db.repositories import (
	AdminRepository,
	DuplicateTopicError,
	EncryptedFieldCodec,
	MenuRepository,
	SettingsRepository,
	TopicRepository,
	UserRepository,
)
from lolzup.security.crypto import generate_data_key
from lolzup.security.policy import EncryptionPolicy
from lolzup.security.runtime import RuntimeVault


@pytest.mark.integration
def test_repositories_hide_encrypted_values_and_support_indexes() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(EncryptionPolicy(EncryptionMode.FULL), vault)
		sessions = async_sessionmaker(engine, expire_on_commit=False)

		async with sessions.begin() as session:
			users = UserRepository(session, codec)
			admins = AdminRepository(session)
			topics = TopicRepository(session, codec)
			settings = SettingsRepository(session, codec)
			menus = MenuRepository(session, codec)

			user = await users.upsert(123456, "TestUser")
			assert await users.get_by_telegram_id(123456) == user
			assert await users.get_by_username("testuser") == user

			await admins.add(user.id)
			assert await admins.contains(user.id)

			topic = await topics.add(5523020, "Encrypted topic")
			assert await topics.get(topic.id) == topic
			assert await topics.list() == [topic]
			with pytest.raises(DuplicateTopicError):
				await topics.add(5523020, "Duplicate")
			assert await topics.list() == [topic]

			app_settings = await settings.get_or_create()
			assert app_settings.global_interval_seconds == 72 * 3600
			assert app_settings.retry_schedule == [60, 300, 900]

			await menus.save(user.id, 987654, 10)
			assert await menus.get(user.id, 987654) == 10
			await menus.save(user.id, 987654, 11)
			assert await menus.get(user.id, 987654) == 11

			raw_user = await session.scalar(select(KnownUser))
			assert raw_user is not None
			assert raw_user.telegram_id_plain is None
			assert raw_user.telegram_id_ciphertext is not None

			raw_topic = await session.scalar(select(Topic))
			assert raw_topic is not None
			assert raw_topic.thread_id_plain is None
			assert raw_topic.thread_id_ciphertext is not None
			assert raw_topic.schedule_due_at == topic.next_bump_at

		await engine.dispose()

	asyncio.run(scenario())
