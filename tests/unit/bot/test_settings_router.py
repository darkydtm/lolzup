import asyncio
import uuid
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from lolzup.access import AccessDeniedError, AccessService, ActorRole
from lolzup.bot.menu import MenuService
from lolzup.bot.routers.settings import (
	CURRENT_PASSWORD_KEY,
	NEW_PASSWORD_KEY,
	begin_api_token,
	receive_api_token,
	receive_new_password_confirmation,
	toggle_global_bump,
)
from lolzup.bot.settings_views import policy_from_mask, settings_view
from lolzup.db.migrations import (
	EncryptionMigrationRecord,
	EncryptionMigrationService,
)
from lolzup.db.models import EncryptionMode, MigrationStatus
from lolzup.db.repositories import SettingsRecord
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.setup import SetupService
from lolzup.topics.service import TopicService


def settings() -> SettingsRecord:
	return SettingsRecord(True, 72 * 3600, [60, 300, 900], False, True, False)


def migration(
	status: MigrationStatus = MigrationStatus.IDLE,
) -> EncryptionMigrationRecord:
	policy = EncryptionPolicy(EncryptionMode.FULL)
	return EncryptionMigrationRecord(status, policy, policy, None, None, None)


def callback(data: str, telegram_id: int = 100) -> Mock:
	result = Mock()
	result.data = data
	result.from_user.id = telegram_id
	result.message.chat.id = 500
	result.message.answer = AsyncMock()
	result.answer = AsyncMock()
	return result


def message(text: str) -> Mock:
	result = Mock()
	result.text = text
	result.from_user.id = 100
	result.chat.id = 500
	result.answer = AsyncMock()
	result.delete = AsyncMock()
	return result


@pytest.mark.unit
def test_owner_view_contains_security_controls_but_admin_view_does_not() -> None:
	owner = settings_view(settings(), ActorRole.OWNER, migration())
	admin = settings_view(settings(), ActorRole.ADMIN, migration())
	owner_callbacks = {
		button.callback_data
		for row in owner.reply_markup.inline_keyboard
		for button in row
	}
	admin_callbacks = {
		button.callback_data
		for row in admin.reply_markup.inline_keyboard
		for button in row
	}

	assert "settings:api-token" in owner_callbacks
	assert "settings:change-key" in owner_callbacks
	assert "encryption:modes" in owner_callbacks
	assert "admins:list" in owner_callbacks
	assert "settings:api-token" not in admin_callbacks
	assert "encryption:modes" not in admin_callbacks
	assert "settings:global-toggle" not in admin_callbacks


@pytest.mark.unit
def test_custom_mask_selects_only_requested_categories() -> None:
	policy = policy_from_mask(0b0101)

	assert policy.mode is EncryptionMode.CUSTOM
	assert policy.categories == frozenset({DataCategory.TOPICS, DataCategory.HISTORY})
	assert policy.encrypts(DataCategory.SECRETS)


@pytest.mark.unit
def test_admin_cannot_toggle_global_bump() -> None:
	async def scenario() -> None:
		incoming = callback("settings:global-toggle", telegram_id=200)
		access = Mock(spec=AccessService)
		access.require = AsyncMock(side_effect=AccessDeniedError)
		topics = Mock(spec=TopicService)
		topics.set_global_enabled = AsyncMock()
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock()
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

		topics.set_global_enabled.assert_not_awaited()
		migrations.status.assert_not_awaited()
		menu.render.assert_not_awaited()
		incoming.answer.assert_awaited_once_with(
			"Доступ запрещен.",
			show_alert=True,
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_migration_blocks_scheduler_setting_changes() -> None:
	async def scenario() -> None:
		incoming = callback("settings:global-toggle")
		access = Mock(spec=AccessService)
		access.require = AsyncMock(return_value=ActorRole.OWNER)
		topics = Mock(spec=TopicService)
		topics.set_global_enabled = AsyncMock()
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock(return_value=migration(MigrationStatus.RUNNING))
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

		topics.set_global_enabled.assert_not_awaited()
		incoming.answer.assert_awaited_once_with(
			"Настройки временно недоступны во время миграции.",
			show_alert=True,
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_admin_cannot_begin_api_token_replacement() -> None:
	async def scenario() -> None:
		incoming = callback("settings:api-token", telegram_id=200)
		state = Mock(spec=FSMContext)
		state.set_state = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock(side_effect=AccessDeniedError)
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock()

		await begin_api_token(
			cast(CallbackQuery, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
			cast(EncryptionMigrationService, migrations),
		)

		state.set_state.assert_not_awaited()
		migrations.status.assert_not_awaited()
		incoming.answer.assert_awaited_once_with(
			"Доступ запрещен.",
			show_alert=True,
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_api_token_replacement_resumes_scheduler() -> None:
	async def scenario() -> None:
		incoming = message("replacement-token")
		state = Mock(spec=FSMContext)
		state.clear = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock(return_value=ActorRole.OWNER)
		setup = Mock(spec=SetupService)
		setup.replace_api_token = AsyncMock()
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock(return_value=migration())
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()
		topics = Mock(spec=TopicService)
		topics.clear_api_pause = AsyncMock()
		topics.settings = AsyncMock(return_value=settings())

		await receive_api_token(
			cast(Message, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
			cast(SetupService, setup),
			cast(EncryptionMigrationService, migrations),
			cast(MenuService, menu),
			uuid.uuid4(),
			cast(TopicService, topics),
		)

		setup.replace_api_token.assert_awaited_once()
		topics.clear_api_pause.assert_awaited_once()
		incoming.answer.assert_awaited_once_with(
			"API token заменен.",
			reply_markup=incoming.answer.await_args.kwargs["reply_markup"],
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_key_confirmation_clears_state_and_rotates_secret() -> None:
	async def scenario() -> None:
		incoming = message("new password")
		state = Mock(spec=FSMContext)
		state.get_data = AsyncMock(
			return_value={
				CURRENT_PASSWORD_KEY: "current password",
				NEW_PASSWORD_KEY: "new password",
			}
		)
		state.clear = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		setup = Mock(spec=SetupService)
		setup.change_password = AsyncMock()
		migrations = Mock(spec=EncryptionMigrationService)
		migrations.status = AsyncMock(return_value=migration())
		topics = Mock(spec=TopicService)
		topics.settings = AsyncMock(return_value=settings())
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await receive_new_password_confirmation(
			cast(Message, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
			cast(SetupService, setup),
			cast(EncryptionMigrationService, migrations),
			cast(MenuService, menu),
			uuid.uuid4(),
			cast(TopicService, topics),
		)

		incoming.delete.assert_awaited_once()
		state.clear.assert_awaited_once()
		setup.change_password.assert_awaited_once_with(
			"current password",
			"new password",
		)
		assert "new password" not in incoming.answer.await_args.args[0]

	asyncio.run(scenario())
