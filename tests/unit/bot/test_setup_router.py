import asyncio
import uuid
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from lolzup.access import AccessDeniedError, AccessService, ActorRole
from lolzup.bot.menu import MenuService
from lolzup.bot.routers.setup import (
	SETUP_PASSWORD_KEY,
	receive_api_token,
	receive_setup_password,
	receive_setup_password_confirmation,
	receive_unlock_password,
	start_bot,
)
from lolzup.bot.states import SetupStates
from lolzup.db.repositories import UserRecord
from lolzup.security.setup import (
	InvalidPasswordError,
	SetupService,
	UnlockThrottledError,
)


def message(
	*,
	telegram_id: int = 100,
	username: str | None = "Owner",
	text: str | None = None,
) -> Mock:
	result = Mock()
	result.from_user.id = telegram_id
	result.from_user.username = username
	result.chat.id = 500
	result.text = text
	result.answer = AsyncMock()
	result.delete = AsyncMock()
	return result


def state(data: dict[str, object] | None = None) -> Mock:
	result = Mock(spec=FSMContext)
	result.clear = AsyncMock()
	result.set_state = AsyncMock()
	result.update_data = AsyncMock()
	result.get_data = AsyncMock(return_value={} if data is None else data)
	return result


def services() -> tuple[Mock, Mock, Mock]:
	setup = Mock(spec=SetupService)
	setup.is_initialized = AsyncMock(return_value=True)
	setup.initialize = AsyncMock()
	setup.unlock = AsyncMock()
	access = Mock(spec=AccessService)
	access.role_for = AsyncMock(return_value=ActorRole.OWNER)
	access.require = AsyncMock(return_value=ActorRole.OWNER)
	access.record_user = AsyncMock(return_value=UserRecord(uuid.uuid4(), 100, "Owner"))
	menu = Mock(spec=MenuService)
	menu.render = AsyncMock()
	return setup, access, menu


@pytest.mark.unit
def test_start_begins_initial_setup_for_owner() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		setup.is_initialized = AsyncMock(return_value=False)
		incoming = message()
		fsm = state()

		await start_bot(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		fsm.set_state.assert_awaited_once_with(SetupStates.password)
		assert any(
			button.text == "Отмена"
			for row in incoming.answer.await_args.kwargs["reply_markup"].keyboard
			for button in row
		)
		menu.render.assert_not_awaited()

	asyncio.run(scenario())


@pytest.mark.unit
def test_start_begins_unlock_for_locked_owner() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		access.record_user = AsyncMock(side_effect=AccessDeniedError)
		incoming = message()
		fsm = state()

		await start_bot(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		fsm.set_state.assert_awaited_once_with(SetupStates.unlock_password)
		menu.render.assert_not_awaited()

	asyncio.run(scenario())


@pytest.mark.unit
def test_start_records_unlocked_admin_and_opens_main_menu() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		admin = UserRecord(uuid.uuid4(), 200, "Admin")
		access.role_for = AsyncMock(side_effect=[ActorRole.ADMIN, ActorRole.ADMIN])
		access.record_user = AsyncMock(return_value=admin)
		incoming = message(telegram_id=200, username="Admin")
		fsm = state()

		await start_bot(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		access.record_user.assert_awaited_once_with(200, "Admin")
		menu.render.assert_awaited_once()
		assert incoming.answer.await_args.args[0] == "Бот готов к работе."

	asyncio.run(scenario())


@pytest.mark.unit
def test_start_records_unknown_user_without_granting_access() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		unknown = UserRecord(uuid.uuid4(), 300, "Unknown")
		access.role_for = AsyncMock(side_effect=[ActorRole.DENIED, ActorRole.DENIED])
		access.record_user = AsyncMock(return_value=unknown)
		incoming = message(telegram_id=300, username="Unknown")
		fsm = state()

		await start_bot(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		access.record_user.assert_awaited_once_with(300, "Unknown")
		menu.render.assert_not_awaited()
		assert incoming.answer.await_args.args[0] == "Доступ запрещен."

	asyncio.run(scenario())


@pytest.mark.unit
def test_initialization_deletes_secrets_and_clears_fsm() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		password_message = message(text="secret password")
		password_state = state()

		await receive_setup_password(
			cast(Message, password_message),
			cast(FSMContext, password_state),
			cast(AccessService, access),
		)
		password_message.delete.assert_awaited_once()
		password_state.update_data.assert_awaited_once_with(
			{SETUP_PASSWORD_KEY: "secret password"}
		)

		confirmation_message = message(text="secret password")
		confirmation_state = state({SETUP_PASSWORD_KEY: "secret password"})
		await receive_setup_password_confirmation(
			cast(Message, confirmation_message),
			cast(FSMContext, confirmation_state),
			cast(AccessService, access),
		)
		confirmation_message.delete.assert_awaited_once()
		confirmation_state.set_state.assert_awaited_once_with(SetupStates.api_token)

		token_message = message(text="api-token")
		token_state = state({SETUP_PASSWORD_KEY: "secret password"})
		await receive_api_token(
			cast(Message, token_message),
			cast(FSMContext, token_state),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)
		token_message.delete.assert_awaited_once()
		token_state.clear.assert_awaited_once()
		setup.initialize.assert_awaited_once_with("secret password", "api-token")
		menu.render.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_failed_unlock_clears_secret_state() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		setup.unlock = AsyncMock(side_effect=InvalidPasswordError)
		incoming = message(text="wrong password")
		fsm = state()

		await receive_unlock_password(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		incoming.delete.assert_awaited_once()
		fsm.clear.assert_awaited_once()
		menu.render.assert_not_awaited()
		assert "Неверный ключ" in incoming.answer.await_args.args[0]

	asyncio.run(scenario())


@pytest.mark.unit
def test_throttle_datetime_is_formatted_without_exposing_password() -> None:
	async def scenario() -> None:
		setup, access, menu = services()
		setup.unlock = AsyncMock(
			side_effect=UnlockThrottledError(datetime(2026, 7, 20, 18, 30, tzinfo=UTC))
		)
		incoming = message(text="secret password")
		fsm = state()

		await receive_unlock_password(
			cast(Message, incoming),
			cast(FSMContext, fsm),
			cast(SetupService, setup),
			cast(AccessService, access),
			cast(MenuService, menu),
		)

		response = incoming.answer.await_args.args[0]
		assert "20.07.2026 18:30:00 UTC" in response
		assert "secret password" not in response

	asyncio.run(scenario())
