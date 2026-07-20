import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import TelegramObject

from lolzup.bot.states import SetupStates
from lolzup.bot.timeout import InputTimeoutMiddleware


@pytest.mark.unit
def test_input_timeout_clears_fsm_and_restores_default_keyboard() -> None:
	async def scenario() -> None:
		bot = Mock(spec=Bot)
		bot.send_message = AsyncMock()
		state = Mock(spec=FSMContext)
		state.key = StorageKey(bot_id=1, chat_id=500, user_id=100)
		state.get_state = AsyncMock(return_value=SetupStates.api_token.state)
		state.clear = AsyncMock()
		sleep = AsyncMock()
		middleware = InputTimeoutMiddleware(
			cast(Bot, bot),
			timeout_seconds=1,
			sleep=sleep,
		)
		handler = AsyncMock()

		await middleware(
			handler,
			Mock(spec=TelegramObject),
			{"state": cast(FSMContext, state)},
		)
		await asyncio.sleep(0)

		sleep.assert_awaited_once_with(1)
		state.clear.assert_awaited_once()
		bot.send_message.assert_awaited_once()
		call = bot.send_message.await_args
		assert call.kwargs["chat_id"] == 500
		assert call.kwargs["text"] == "Время ввода истекло."
		keyboard = cast(Any, call.kwargs["reply_markup"])
		assert all(
			button.text != "Отмена" for row in keyboard.keyboard for button in row
		)
		await middleware.close()

	asyncio.run(scenario())


@pytest.mark.unit
def test_input_timeout_is_cancelled_when_flow_finishes() -> None:
	async def scenario() -> None:
		bot = Mock(spec=Bot)
		bot.send_message = AsyncMock()
		state = Mock(spec=FSMContext)
		state.key = StorageKey(bot_id=1, chat_id=500, user_id=100)
		state.get_state = AsyncMock(return_value=None)
		state.clear = AsyncMock()
		middleware = InputTimeoutMiddleware(cast(Bot, bot))

		await middleware(
			AsyncMock(),
			Mock(spec=TelegramObject),
			{"state": cast(FSMContext, state)},
		)

		assert middleware._tasks == {}
		state.clear.assert_not_awaited()
		bot.send_message.assert_not_awaited()
		await middleware.close()

	asyncio.run(scenario())
