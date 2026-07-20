import asyncio
import uuid
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from lolzup.access import (
	AccessDeniedError,
	AccessService,
	UnknownAdministratorError,
)
from lolzup.bot.admin_views import administrator_list_view
from lolzup.bot.menu import MenuService
from lolzup.bot.routers.admins import (
	begin_add_administrator,
	receive_administrator_identity,
	remove_administrator,
)
from lolzup.bot.states import AdministratorStates
from lolzup.db.repositories import UserRecord


def administrator(
	telegram_id: int = 200,
	username: str | None = "Admin",
) -> UserRecord:
	return UserRecord(uuid.uuid4(), telegram_id, username)


def callback(data: str, telegram_id: int = 100) -> Mock:
	result = Mock()
	result.data = data
	result.from_user.id = telegram_id
	result.message.chat.id = 500
	result.message.answer = AsyncMock()
	result.answer = AsyncMock()
	return result


def message(text: str, telegram_id: int = 100) -> Mock:
	result = Mock()
	result.text = text
	result.from_user.id = telegram_id
	result.chat.id = 500
	result.answer = AsyncMock()
	return result


@pytest.mark.unit
def test_administrator_list_shows_username_and_id() -> None:
	user = administrator()

	view = administrator_list_view([user])

	assert "@Admin - ID 200" in view.text
	assert view.reply_markup.inline_keyboard[0][0].callback_data == (
		f"admin:remove:{user.id}"
	)


@pytest.mark.unit
def test_add_administrator_flow_sets_input_state() -> None:
	async def scenario() -> None:
		incoming = callback("admins:add")
		state = Mock(spec=FSMContext)
		state.set_state = AsyncMock()
		state.update_data = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock()

		await begin_add_administrator(
			cast(CallbackQuery, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
		)

		state.set_state.assert_awaited_once_with(AdministratorStates.identity)
		assert incoming.message.answer.await_args.args[0].startswith(
			"Отправьте Telegram ID"
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_owner_adds_administrator_by_known_username() -> None:
	async def scenario() -> None:
		user = administrator()
		incoming = message("@Admin")
		state = Mock(spec=FSMContext)
		state.clear = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		access.add_administrator = AsyncMock(return_value=user)
		access.list_administrators = AsyncMock(return_value=[user])
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await receive_administrator_identity(
			cast(Message, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		access.add_administrator.assert_awaited_once_with(100, "@Admin")
		state.clear.assert_awaited_once()
		menu.render.assert_awaited_once()
		assert "@Admin" in incoming.answer.await_args.args[0]

	asyncio.run(scenario())


@pytest.mark.unit
def test_unknown_username_keeps_input_flow_active() -> None:
	async def scenario() -> None:
		incoming = message("@Missing")
		state = Mock(spec=FSMContext)
		state.clear = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		access.add_administrator = AsyncMock(side_effect=UnknownAdministratorError)
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await receive_administrator_identity(
			cast(Message, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		state.clear.assert_not_awaited()
		menu.render.assert_not_awaited()
		assert "должен сначала открыть бота" in incoming.answer.await_args.args[0]

	asyncio.run(scenario())


@pytest.mark.unit
def test_confirmed_removal_updates_list() -> None:
	async def scenario() -> None:
		user = administrator()
		incoming = callback(f"admin:remove-confirm:{user.id}")
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		access.remove_administrator = AsyncMock()
		access.list_administrators = AsyncMock(return_value=[])
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await remove_administrator(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		access.remove_administrator.assert_awaited_once_with(100, user.id)
		menu.render.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_admin_cannot_change_administrator_list() -> None:
	async def scenario() -> None:
		user = administrator()
		incoming = callback(
			f"admin:remove-confirm:{user.id}",
			telegram_id=200,
		)
		access = Mock(spec=AccessService)
		access.require = AsyncMock(side_effect=AccessDeniedError)
		access.remove_administrator = AsyncMock()
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await remove_administrator(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		access.remove_administrator.assert_not_awaited()
		menu.render.assert_not_awaited()
		incoming.answer.assert_awaited_once_with(
			"Доступ запрещен.",
			show_alert=True,
		)

	asyncio.run(scenario())
