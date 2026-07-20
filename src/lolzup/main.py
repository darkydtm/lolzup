import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import TelegramObject, User
from sqlalchemy.ext.asyncio import (
	AsyncEngine,
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)

from lolzup.access import AccessAction, AccessService
from lolzup.bot.menu import MenuService
from lolzup.bot.routers import build_routers
from lolzup.bot.timeout import InputTimeoutMiddleware
from lolzup.config import Settings
from lolzup.db.migrations import EncryptionMigrationService, load_active_policy
from lolzup.db.repositories import (
	AdminRepository,
	AttemptRepository,
	EncryptedFieldCodec,
	MenuRepository,
	SecretRepository,
	SettingsRepository,
	TopicRepository,
	UserRepository,
)
from lolzup.forum import BumpJob, BumpResult, ForumApiClient, ThreadInfo
from lolzup.logging import configure_logging
from lolzup.scheduler import SchedulerService
from lolzup.security.runtime import RuntimeVault
from lolzup.security.setup import SetupService, UnlockThrottleState
from lolzup.topics.service import TopicService

FORUM_BASE_URL = "https://prod-api.lolz.live"
UNKNOWN_MENU_USER_ID = uuid.UUID(int=0)

UpdateHandler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class SessionForumClient:
	def __init__(self, session: AsyncSession, forum: ForumApiClient) -> None:
		self._session = session
		self._forum = forum

	async def get_thread(self, thread_id: int) -> ThreadInfo:
		await self._session.commit()
		return await self._forum.get_thread(thread_id)

	async def bump_batch(self, jobs: list[BumpJob]) -> list[BumpResult]:
		await self._session.commit()
		return await self._forum.bump_batch(jobs)


class SessionNotificationSink:
	def __init__(
		self,
		session: AsyncSession,
		notifier: Callable[[str], Awaitable[None]],
	) -> None:
		self._session = session
		self._notifier = notifier

	async def __call__(self, message: str) -> None:
		await self._session.commit()
		await self._notifier(message)


class DependencyMiddleware(BaseMiddleware):
	def __init__(
		self,
		bot: Bot,
		sessions: async_sessionmaker[AsyncSession],
		vault: RuntimeVault,
		forum: ForumApiClient,
		migrations: EncryptionMigrationService,
		throttle: UnlockThrottleState,
		owner_id: int,
	) -> None:
		self._bot = bot
		self._sessions = sessions
		self._vault = vault
		self._forum = forum
		self._migrations = migrations
		self._throttle = throttle
		self._owner_id = owner_id

	async def __call__(
		self,
		handler: UpdateHandler,
		event: TelegramObject,
		data: dict[str, Any],
	) -> Any:
		async with self._sessions() as session:
			try:
				result = await self._handle(session, handler, event, data)
			except BaseException:
				await session.rollback()
				raise
			await session.commit()
			return result

	async def _handle(
		self,
		session: AsyncSession,
		handler: UpdateHandler,
		event: TelegramObject,
		data: dict[str, Any],
	) -> Any:
		policy = await load_active_policy(session)
		codec = EncryptedFieldCodec(policy, self._vault)
		users = UserRepository(session, codec)
		settings = SettingsRepository(session, codec)
		setup = SetupService(
			SecretRepository(session),
			self._vault,
			throttle_state=self._throttle,
		)
		access = AccessService(
			self._owner_id,
			users,
			AdminRepository(session),
			self._vault,
		)
		event_user = data.get("event_from_user")
		menu_user_id = await self._menu_user_id(users, event_user)
		global_enabled = False
		can_manage_global_bump = False
		if self._vault.is_unlocked:
			global_enabled = (await settings.get_or_create()).global_bump_enabled
			if isinstance(event_user, User):
				can_manage_global_bump = await access.allows(
					event_user.id,
					AccessAction.MANAGE_GLOBAL_BUMP,
				)
		data.update(
			{
				"access_service": access,
				"can_manage_global_bump": can_manage_global_bump,
				"global_bump_enabled": global_enabled,
				"menu_service": MenuService(
					self._bot,
					MenuRepository(session, codec),
				),
				"menu_user_id": menu_user_id,
				"migration_service": self._migrations,
				"runtime_vault": self._vault,
				"setup_service": setup,
				"topic_service": TopicService(
					TopicRepository(session, codec),
					settings,
					AttemptRepository(session, codec),
					SessionForumClient(session, self._forum),
					notifier=SessionNotificationSink(
						session,
						self._notify_owner,
					),
				),
			}
		)
		return await handler(event, data)

	async def _menu_user_id(
		self,
		users: UserRepository,
		event_user: object,
	) -> uuid.UUID:
		if not self._vault.is_unlocked or not isinstance(event_user, User):
			return UNKNOWN_MENU_USER_ID
		user = await users.get_by_telegram_id(event_user.id)
		return UNKNOWN_MENU_USER_ID if user is None else user.id

	async def _notify_owner(self, message: str) -> None:
		await self._bot.send_message(
			chat_id=self._owner_id,
			text=message,
		)


class Application:
	def __init__(
		self,
		settings: Settings,
		engine: AsyncEngine,
		sessions: async_sessionmaker[AsyncSession],
		bot: Bot,
		dispatcher: Dispatcher,
		http_client: httpx.AsyncClient,
		vault: RuntimeVault,
		forum: ForumApiClient,
		input_timeout: InputTimeoutMiddleware,
	) -> None:
		self.settings = settings
		self.engine = engine
		self.sessions = sessions
		self.bot = bot
		self.dispatcher = dispatcher
		self.http_client = http_client
		self.vault = vault
		self.forum = forum
		self.input_timeout = input_timeout
		self._scheduler_task: asyncio.Task[None] | None = None
		self._closed = False

	async def run(self) -> None:
		self._scheduler_task = asyncio.create_task(
			self._scheduler_loop(),
			name="lolzup-scheduler",
		)
		try:
			await self.dispatcher.start_polling(
				self.bot,
				close_bot_session=False,
				handle_signals=True,
			)
		finally:
			await self.close()

	async def close(self) -> None:
		if self._closed:
			return
		self._closed = True
		if self._scheduler_task is not None:
			self._scheduler_task.cancel()
			await asyncio.gather(self._scheduler_task, return_exceptions=True)
			self._scheduler_task = None
		await self.input_timeout.close()
		await self.bot.session.close()
		await self.http_client.aclose()
		await self.engine.dispose()
		await self.vault.lock()

	async def _scheduler_loop(self) -> None:
		while True:
			try:
				async with self.sessions() as session:
					policy = await load_active_policy(session)
				scheduler = SchedulerService(
					self.sessions,
					EncryptedFieldCodec(policy, self.vault),
					self.forum,
					self.vault,
					notifier=self._notify_owner,
				)
				await scheduler.run_cycle(datetime.now(UTC))
			except asyncio.CancelledError:
				raise
			except Exception:
				logging.getLogger(__name__).exception("Scheduler cycle failed")
			await asyncio.sleep(self.settings.scheduler_poll_seconds)

	async def _notify_owner(self, message: str) -> None:
		await self.bot.send_message(
			chat_id=int(self.settings.owner_id),
			text=message,
		)


def build_application(settings: Settings) -> Application:
	bot_token = settings.bot_token.get_secret_value()
	configure_logging(settings.log_level, secrets=[bot_token])
	engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
	sessions = async_sessionmaker(engine, expire_on_commit=False)
	bot = Bot(bot_token)
	dispatcher = Dispatcher()
	http_client = httpx.AsyncClient(base_url=FORUM_BASE_URL)
	vault = RuntimeVault()
	throttle = UnlockThrottleState()
	migrations = EncryptionMigrationService(sessions, vault)
	input_timeout = InputTimeoutMiddleware(bot)

	async def token_provider() -> str:
		async with sessions.begin() as session:
			return await SetupService(
				SecretRepository(session),
				vault,
				throttle_state=throttle,
			).api_token()

	forum = ForumApiClient(http_client, token_provider)
	dispatcher.update.outer_middleware(
		DependencyMiddleware(
			bot,
			sessions,
			vault,
			forum,
			migrations,
			throttle,
			int(settings.owner_id),
		)
	)
	dispatcher.update.outer_middleware(input_timeout)
	dispatcher.include_routers(*build_routers())
	return Application(
		settings,
		engine,
		sessions,
		bot,
		dispatcher,
		http_client,
		vault,
		forum,
		input_timeout,
	)


def main() -> None:
	asyncio.run(build_application(Settings()).run())  # type: ignore[call-arg]


if __name__ == "__main__":
	main()
