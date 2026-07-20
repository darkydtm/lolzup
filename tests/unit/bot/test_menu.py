import asyncio
import uuid
from dataclasses import dataclass, field
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from lolzup.access import AccessService, ActorRole
from lolzup.bot.keyboards import default_reply_keyboard, input_reply_keyboard
from lolzup.bot.menu import MenuSection, MenuService, MenuView, menu_view
from lolzup.bot.routers.menu import toggle_global_bump
from lolzup.db.migrations import EncryptionMigrationRecord, EncryptionMigrationService
from lolzup.db.models import EncryptionMode, MigrationStatus
from lolzup.db.repositories import SettingsRecord
from lolzup.security.policy import EncryptionPolicy
from lolzup.topics.service import TopicService


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


@pytest.mark.unit
def test_main_menu_hides_global_toggle_without_permission() -> None:
	view = menu_view(MenuSection.MAIN, True, can_toggle_global=False)
	callbacks = {
		button.callback_data
		for row in view.reply_markup.inline_keyboard
		for button in row
	}

	assert "scheduler:toggle" not in callbacks


@pytest.mark.unit
def test_owner_toggles_global_bump_from_main_menu() -> None:
	async def scenario() -> None:
		incoming = Mock()
		incoming.from_user.id = 100
		incoming.message.chat.id = 500
		incoming.answer = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock(return_value=ActorRole.OWNER)
		topics = Mock(spec=TopicService)
		topics.settings = AsyncMock(
			return_value=SettingsRecord(
				True,
				72 * 3600,
				[60, 300, 900],
				False,
				True,
				False,
			)
		)
		topics.set_global_enabled = AsyncMock(
			return_value=SettingsRecord(
				False,
				72 * 3600,
				[60, 300, 900],
				False,
				True,
				False,
			)
		)
		policy = EncryptionPolicy(EncryptionMode.FULL)
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock(
			return_value=EncryptionMigrationRecord(
				MigrationStatus.IDLE,
				policy,
				policy,
				None,
				None,
				None,
			)
		)
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await toggle_global_bump(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(TopicService, topics),
			cast(EncryptionMigrationService, migrations),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		topics.set_global_enabled.assert_awaited_once_with(False)
		rendered = menu.render.await_args.args[2]
		assert rendered.text == "Главное меню\n\nАвтоподнятие: выключено"
		incoming.answer.assert_awaited_once()

	asyncio.run(scenario())
