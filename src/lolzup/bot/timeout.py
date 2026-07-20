import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import TelegramObject

from lolzup.bot.keyboards import default_reply_keyboard

DEFAULT_INPUT_TIMEOUT_SECONDS = 300

UpdateHandler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]


class InputTimeoutMiddleware(BaseMiddleware):
	def __init__(
		self,
		bot: Bot,
		*,
		timeout_seconds: int = DEFAULT_INPUT_TIMEOUT_SECONDS,
		sleep: Sleep = asyncio.sleep,
	) -> None:
		if timeout_seconds <= 0:
			raise ValueError("Input timeout must be positive")
		self._bot = bot
		self._timeout_seconds = timeout_seconds
		self._sleep = sleep
		self._tasks: dict[StorageKey, asyncio.Task[None]] = {}

	async def __call__(
		self,
		handler: UpdateHandler,
		event: TelegramObject,
		data: dict[str, Any],
	) -> Any:
		state = data.get("state")
		if not isinstance(state, FSMContext):
			return await handler(event, data)
		self._cancel(state.key)
		try:
			return await handler(event, data)
		finally:
			if await state.get_state() is not None:
				self._tasks[state.key] = asyncio.create_task(
					self._expire(state),
					name="lolzup-input-timeout",
				)

	async def close(self) -> None:
		tasks = list(self._tasks.values())
		self._tasks.clear()
		for task in tasks:
			task.cancel()
		if tasks:
			await asyncio.gather(*tasks, return_exceptions=True)

	async def _expire(self, state: FSMContext) -> None:
		task = asyncio.current_task()
		try:
			await self._sleep(self._timeout_seconds)
			await state.clear()
			await self._bot.send_message(
				chat_id=state.key.chat_id,
				text="Время ввода истекло.",
				reply_markup=default_reply_keyboard(),
			)
		except asyncio.CancelledError:
			raise
		finally:
			if task is not None and self._tasks.get(state.key) is task:
				self._tasks.pop(state.key, None)

	def _cancel(self, key: StorageKey) -> None:
		task = self._tasks.pop(key, None)
		if task is not None:
			task.cancel()
