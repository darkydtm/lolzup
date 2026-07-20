import asyncio
import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from lolzup.bot.menu import MenuSection, MenuService
from lolzup.bot.routers.cancel import cancel_active_input
from lolzup.bot.states import RETURN_MENU_KEY


@pytest.mark.unit
def test_global_cancel_clears_state_and_returns_to_relevant_menu() -> None:
	async def scenario() -> None:
		state = Mock(spec=FSMContext)
		state.get_data = AsyncMock(
			return_value={RETURN_MENU_KEY: MenuSection.TOPICS.value, "secret": "value"}
		)
		state.clear = AsyncMock()
		message = Mock()
		message.chat.id = 100
		message.answer = AsyncMock()
		menu_service = Mock(spec=MenuService)
		menu_service.render = AsyncMock()
		user_id = uuid.uuid4()

		await cancel_active_input(
			cast(Message, message),
			cast(FSMContext, state),
			cast(MenuService, menu_service),
			user_id,
		)

		state.clear.assert_awaited_once()
		menu_service.render.assert_awaited_once()
		render_call = menu_service.render.await_args
		assert render_call.args[:2] == (user_id, 100)
		assert render_call.args[2].text.startswith("Темы")
		message.answer.assert_awaited_once()
		call = message.answer.await_args
		reply_markup = cast(Any, call.kwargs["reply_markup"])
		texts = {button.text for row in reply_markup.keyboard for button in row}
		assert "Отмена" not in texts
		assert call.args[0] == "Действие отменено."

	asyncio.run(scenario())
