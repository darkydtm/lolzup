import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from aiogram import Bot
from aiogram.types import TelegramObject
from pydantic import PostgresDsn, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from lolzup.config import Settings
from lolzup.db.migrations import EncryptionMigrationService
from lolzup.forum import BumpJob, BumpOutcome, BumpResult, ForumApiClient, ThreadInfo
from lolzup.main import (
	FORUM_BASE_URL,
	DependencyMiddleware,
	SessionForumClient,
	build_application,
)
from lolzup.security.runtime import RuntimeVault
from lolzup.security.setup import UnlockThrottleState


def settings() -> Settings:
	return Settings(
		bot_token=SecretStr("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"),
		owner_id=100,
		database_url=PostgresDsn("postgresql+asyncpg://bot:bot@localhost/lolzup"),
		scheduler_poll_seconds=60,
	)


@pytest.mark.unit
def test_build_application_wires_locked_shared_resources_and_router_order() -> None:
	application = build_application(settings())

	assert not application.vault.is_unlocked
	assert str(application.http_client.base_url) == FORUM_BASE_URL
	assert [router.name for router in application.dispatcher.sub_routers] == [
		"global-cancel",
		"setup",
		"topics",
		"settings",
		"administrators",
		"menu",
	]

	asyncio.run(application.close())


@pytest.mark.unit
def test_close_is_idempotent_and_closes_http_resources() -> None:
	async def scenario() -> None:
		application = build_application(settings())
		bot_close = AsyncMock()
		cast(Any, application.bot.session).close = bot_close

		await application.close()
		await application.close()

		assert application.http_client.is_closed
		bot_close.assert_awaited_once()
		assert not application.vault.is_unlocked

	asyncio.run(scenario())


@pytest.mark.unit
def test_session_forum_client_commits_before_http_requests() -> None:
	async def scenario() -> None:
		session = Mock(spec=AsyncSession)
		session.commit = AsyncMock()
		forum = Mock(spec=ForumApiClient)
		forum.get_thread = AsyncMock(return_value=ThreadInfo(5523020, "Topic"))
		forum.bump_batch = AsyncMock(
			return_value=[
				BumpResult("manual-1", 5523020, BumpOutcome.SUCCESS),
			]
		)
		client = SessionForumClient(
			cast(AsyncSession, session),
			cast(ForumApiClient, forum),
		)

		await client.get_thread(5523020)
		await client.bump_batch([BumpJob("manual-1", 5523020)])

		assert session.commit.await_count == 2
		forum.get_thread.assert_awaited_once_with(5523020)
		forum.bump_batch.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_dependency_middleware_rolls_back_failed_handler() -> None:
	async def scenario() -> None:
		application = build_application(settings())
		middleware = DependencyMiddleware(
			application.bot,
			application.sessions,
			application.vault,
			application.forum,
			EncryptionMigrationService(application.sessions, application.vault),
			UnlockThrottleState(),
			100,
		)
		session = Mock(spec=AsyncSession)
		session.rollback = AsyncMock()
		handler = AsyncMock(side_effect=RuntimeError("failed"))
		middleware._sessions = Mock()
		cast(Any, middleware)._handle = AsyncMock(side_effect=RuntimeError("failed"))
		session_context = AsyncMock()
		session_context.__aenter__.return_value = session
		session_context.__aexit__.return_value = None
		middleware._sessions.return_value = session_context

		with pytest.raises(RuntimeError, match="failed"):
			await middleware(
				handler,
				Mock(spec=TelegramObject),
				{},
			)

		session.rollback.assert_awaited_once()
		await application.close()

	asyncio.run(scenario())


@pytest.mark.unit
def test_run_starts_scheduler_and_polling_with_signal_handling() -> None:
	async def scenario() -> None:
		application = build_application(settings())
		scheduler_started = asyncio.Event()

		async def scheduler_loop() -> None:
			scheduler_started.set()

		async def start_polling(*_: object, **kwargs: object) -> None:
			await scheduler_started.wait()
			assert kwargs["close_bot_session"] is False
			assert kwargs["handle_signals"] is True
			assert application._scheduler_task is not None

		close = AsyncMock()
		cast(Any, application)._scheduler_loop = scheduler_loop
		cast(Any, application.dispatcher).start_polling = AsyncMock(
			side_effect=start_polling
		)
		cast(Any, application).close = close

		await application.run()

		close.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_close_cancels_scheduler_before_closing_resources() -> None:
	async def scenario() -> None:
		application = build_application(settings())
		order: list[str] = []
		bot = Mock()
		bot.session = Mock()
		bot.session.close = AsyncMock(side_effect=lambda: order.append("bot"))
		http_client = Mock(spec=httpx.AsyncClient)
		http_client.aclose = AsyncMock(side_effect=lambda: order.append("http"))
		engine = Mock(spec=AsyncEngine)
		engine.dispose = AsyncMock(side_effect=lambda: order.append("engine"))
		vault = Mock(spec=RuntimeVault)
		vault.lock = AsyncMock(side_effect=lambda: order.append("vault"))
		application.bot = cast(Bot, bot)
		application.http_client = cast(httpx.AsyncClient, http_client)
		application.engine = cast(AsyncEngine, engine)
		application.vault = cast(RuntimeVault, vault)

		async def scheduler() -> None:
			try:
				await asyncio.Event().wait()
			finally:
				order.append("scheduler")

		application._scheduler_task = asyncio.create_task(scheduler())
		await asyncio.sleep(0)

		await application.close()

		assert order == ["scheduler", "bot", "http", "engine", "vault"]
		assert application._scheduler_task is None

	asyncio.run(scenario())


@pytest.mark.unit
def test_scheduler_notifications_are_sent_to_owner() -> None:
	async def scenario() -> None:
		application = build_application(settings())
		send_message = AsyncMock()
		cast(Any, application.bot).send_message = send_message

		await application._notify_owner("Scheduler message")

		send_message.assert_awaited_once_with(
			chat_id=100,
			text="Scheduler message",
		)
		await application.close()

	asyncio.run(scenario())
