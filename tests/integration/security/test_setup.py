import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lolzup.db.models import Base, EncryptionMode, SecretEnvelope
from lolzup.db.repositories import SecretRepository
from lolzup.security.crypto import Argon2Parameters
from lolzup.security.policy import EncryptionPolicy
from lolzup.security.runtime import BotLockedError, RuntimeVault
from lolzup.security.setup import (
	AlreadyInitializedError,
	InvalidPasswordError,
	SetupService,
	UnlockThrottledError,
)


@pytest.mark.integration
def test_initialization_restart_unlock_and_throttling() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

		def clock() -> datetime:
			return now

		sessions = async_sessionmaker(engine, expire_on_commit=False)
		vault = RuntimeVault()
		async with sessions.begin() as session:
			setup = SetupService(
				SecretRepository(session),
				vault,
				argon_parameters=Argon2Parameters(
					time_cost=1,
					memory_cost=8192,
					parallelism=1,
				),
				clock=clock,
			)
			await setup.initialize("correct password", "api-secret-token")
			assert vault.is_unlocked
			assert await setup.api_token() == "api-secret-token"
			with pytest.raises(AlreadyInitializedError):
				await setup.initialize("other password", "other token")

			raw = await session.scalar(select(SecretEnvelope))
			assert raw is not None
			assert raw.api_token_plain is None
			assert raw.api_token_ciphertext is not None
			assert raw.api_token_nonce is not None
			assert raw.verifier
			assert b"api-secret-token" not in raw.api_token_ciphertext

		await vault.lock()
		with pytest.raises(BotLockedError):
			vault.require_key()

		async with sessions.begin() as session:
			restarted = SetupService(
				SecretRepository(session),
				vault,
				clock=clock,
			)
			with pytest.raises(BotLockedError):
				await restarted.api_token()
			with pytest.raises(InvalidPasswordError):
				await restarted.unlock("wrong password")
			with pytest.raises(UnlockThrottledError) as throttled:
				await restarted.unlock("correct password")
			assert throttled.value.retry_at == now + timedelta(seconds=1)

			now += timedelta(seconds=1)
			await restarted.unlock("correct password")
			assert vault.is_unlocked
			assert await restarted.api_token() == "api-secret-token"

			await restarted.change_password("correct password", "new password")
			await restarted.replace_api_token(
				"replacement-token",
				EncryptionPolicy(EncryptionMode.DISABLED),
			)
			assert await restarted.api_token() == "replacement-token"

		await vault.lock()
		async with sessions.begin() as session:
			rotated = SetupService(
				SecretRepository(session),
				vault,
				clock=clock,
			)
			with pytest.raises(InvalidPasswordError):
				await rotated.unlock("correct password")
			now += timedelta(seconds=1)
			await rotated.unlock("new password")
			assert await rotated.api_token() == "replacement-token"

		await engine.dispose()

	asyncio.run(scenario())
