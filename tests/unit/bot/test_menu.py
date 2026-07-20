import asyncio
import uuid
from dataclasses import dataclass, field
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import InlineKeyboardMarkup, Message

from lolzup.bot.keyboards import default_reply_keyboard, input_reply_keyboard
from lolzup.bot.menu import MenuService, MenuView


@dataclass
class FakeMenus:
	message_id: int | None = None
	saved: list[int] = field(default_factory=list)

	async def get(self, _: uuid.UUID, __: int) -> int | None:
		return self.message_id

	async def save(self, _: uuid.UUID, __: int, message_id: int) -> None:
		self.message_id = message_id
		self.saved.append(message_id)


def view() -> MenuView:
	return MenuView("Главное меню", InlineKeyboardMarkup(inline_keyboard=[]))


@pytest.mark.unit
def test_menu_edits_stored_message() -> None:
	async def scenario() -> None:
		bot = Mock(spec=Bot)
		edited = Mock(spec=Message)
		bot.edit_message_text = AsyncMock(return_value=edited)
		bot.send_message = AsyncMock()
		menus = FakeMenus(message_id=10)
		service = MenuService(cast(Bot, bot), menus)

		result = await service.render(uuid.uuid4(), 100, view())

		assert result is edited
		bot.edit_message_text.assert_awaited_once()
		bot.send_message.assert_not_awaited()
		assert menus.saved == []

	asyncio.run(scenario())


@pytest.mark.unit
def test_unchanged_menu_does_not_create_replacement() -> None:
	async def scenario() -> None:
		bot = Mock(spec=Bot)
		bot.edit_message_text = AsyncMock(
			side_effect=TelegramBadRequest(
				method=EditMessageText(
					chat_id=100,
					message_id=10,
					text="Главное меню",
				),
				message="Bad Request: message is not modified",
			)
		)
		bot.send_message = AsyncMock()
		menus = FakeMenus(message_id=10)
		service = MenuService(cast(Bot, bot), menus)

		assert await service.render(uuid.uuid4(), 100, view()) is None
		bot.send_message.assert_not_awaited()
		assert menus.saved == []

	asyncio.run(scenario())


@pytest.mark.unit
def test_uneditable_menu_creates_one_persisted_replacement() -> None:
	async def scenario() -> None:
		bot = Mock(spec=Bot)
		bot.edit_message_text = AsyncMock(
			side_effect=TelegramBadRequest(
				method=EditMessageText(
					chat_id=100,
					message_id=10,
					text="Главное меню",
				),
				message="Bad Request: message to edit not found",
			)
		)
		replacement = Mock(spec=Message)
		replacement.message_id = 11
		bot.send_message = AsyncMock(return_value=replacement)
		menus = FakeMenus(message_id=10)
		service = MenuService(cast(Bot, bot), menus)

		result = await service.render(uuid.uuid4(), 100, view())

		assert result is replacement
		bot.send_message.assert_awaited_once()
		assert menus.saved == [11]

	asyncio.run(scenario())


@pytest.mark.unit
def test_cancel_button_only_appears_in_input_keyboard() -> None:
	default_texts = {
		button.text for row in default_reply_keyboard().keyboard for button in row
	}
	input_texts = {
		button.text for row in input_reply_keyboard().keyboard for button in row
	}

	assert "Отмена" not in default_texts
	assert "Отмена" in input_texts
