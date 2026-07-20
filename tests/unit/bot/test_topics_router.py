import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from lolzup.access import AccessDeniedError, AccessService
from lolzup.bot.menu import MenuService
from lolzup.bot.routers.topics import (
	begin_add_topic,
	remove_topic,
	toggle_topic_auto,
)
from lolzup.bot.states import TopicStates
from lolzup.bot.topic_views import (
	parse_interval_seconds,
	topic_detail_view,
	topic_list_view,
)
from lolzup.db.repositories import SettingsRecord, TopicRecord
from lolzup.topics.service import TopicService

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def topic(
	*,
	title: str = "Test topic",
	enabled: bool = True,
	last_success_at: datetime | None = None,
	next_bump_at: datetime | None = None,
	error: str | None = None,
) -> TopicRecord:
	return TopicRecord(
		id=uuid.uuid4(),
		thread_id=5523020,
		title=title,
		auto_bump_enabled=enabled,
		custom_interval_enabled=False,
		custom_interval_seconds=None,
		last_success_at=last_success_at,
		next_bump_at=next_bump_at,
		last_error=error,
	)


def settings() -> SettingsRecord:
	return SettingsRecord(True, 72 * 3600, [60, 300, 900], False, True, False)


def callback(data: str, telegram_id: int = 100) -> Mock:
	result = Mock()
	result.data = data
	result.from_user.id = telegram_id
	result.message.chat.id = 500
	result.message.answer = AsyncMock()
	result.answer = AsyncMock()
	return result


@pytest.mark.unit
def test_topic_list_shows_previous_bump_and_remaining_time() -> None:
	record = topic(
		last_success_at=NOW - timedelta(hours=2),
		next_bump_at=NOW + timedelta(hours=70),
	)

	view = topic_list_view([record], 0, NOW)

	assert "20.07.2026 10:00 UTC" in view.text
	assert "через 2 д. 22 ч." in view.text
	assert view.reply_markup.inline_keyboard[0][0].text == record.title


@pytest.mark.unit
def test_topic_list_paginates_and_formats_states() -> None:
	records = [topic(title=f"Topic {index}") for index in range(6)]
	records[5] = topic(title="Disabled", enabled=False)

	first = topic_list_view(records, 0, NOW)
	second = topic_list_view(records, 1, NOW)

	assert "страница 1/2" in first.text
	assert first.reply_markup.inline_keyboard[-3][0].text == "›"
	assert "страница 2/2" in second.text
	assert "отключено" in second.text


@pytest.mark.unit
def test_topic_detail_contains_id_schedule_and_latest_error() -> None:
	record = topic(
		next_bump_at=NOW + timedelta(hours=72),
		error="Forum API returned status 404",
	)

	view = topic_detail_view(record, settings(), NOW)

	assert "ID темы: 5523020" in view.text
	assert "3 д. (глобальный)" in view.text
	assert "ошибка: Forum API returned status 404" in view.text


@pytest.mark.unit
@pytest.mark.parametrize(
	("value", "seconds"),
	[
		("72", 72 * 3600),
		("90 мин", 90 * 60),
		("6 ч", 6 * 3600),
		("3 д", 3 * 86400),
	],
)
def test_interval_parser_accepts_supported_units(value: str, seconds: int) -> None:
	assert parse_interval_seconds(value) == seconds


@pytest.mark.unit
def test_add_flow_sets_state_and_shows_cancel_keyboard() -> None:
	async def scenario() -> None:
		incoming = callback("topics:add")
		state = Mock(spec=FSMContext)
		state.set_state = AsyncMock()
		state.update_data = AsyncMock()
		access = Mock(spec=AccessService)
		access.require = AsyncMock()

		await begin_add_topic(
			cast(CallbackQuery, incoming),
			cast(FSMContext, state),
			cast(AccessService, access),
		)

		state.set_state.assert_awaited_once_with(TopicStates.reference)
		reply_markup = incoming.message.answer.await_args.kwargs["reply_markup"]
		assert any(
			button.text == "Отмена" for row in reply_markup.keyboard for button in row
		)

	asyncio.run(scenario())


@pytest.mark.unit
def test_topic_actions_use_service_and_edit_menu() -> None:
	async def scenario() -> None:
		record = topic(enabled=True)
		incoming = callback(f"topic:auto:{record.id}")
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		service = Mock(spec=TopicService)
		service.get = AsyncMock(side_effect=[record, record])
		service.set_enabled = AsyncMock(return_value=record)
		service.settings = AsyncMock(return_value=settings())
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()
		menu_user_id = uuid.uuid4()

		await toggle_topic_auto(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(TopicService, service),
			cast(MenuService, menu),
			menu_user_id,
		)

		service.set_enabled.assert_awaited_once_with(record.id, False)
		menu.render.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_remove_requires_confirmation_callback_and_deletes_topic() -> None:
	async def scenario() -> None:
		record = topic()
		incoming = callback(f"topic:remove-confirm:{record.id}")
		access = Mock(spec=AccessService)
		access.require = AsyncMock()
		service = Mock(spec=TopicService)
		service.remove = AsyncMock()
		service.list = AsyncMock(return_value=[])
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await remove_topic(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(TopicService, service),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		service.remove.assert_awaited_once_with(record.id)
		menu.render.assert_awaited_once()

	asyncio.run(scenario())


@pytest.mark.unit
def test_unauthorized_callback_makes_no_topic_changes() -> None:
	async def scenario() -> None:
		record = topic()
		incoming = callback(f"topic:auto:{record.id}", telegram_id=300)
		access = Mock(spec=AccessService)
		access.require = AsyncMock(side_effect=AccessDeniedError)
		service = Mock(spec=TopicService)
		service.get = AsyncMock()
		service.set_enabled = AsyncMock()
		menu = Mock(spec=MenuService)
		menu.render = AsyncMock()

		await toggle_topic_auto(
			cast(CallbackQuery, incoming),
			cast(AccessService, access),
			cast(TopicService, service),
			cast(MenuService, menu),
			uuid.uuid4(),
		)

		service.get.assert_not_awaited()
		service.set_enabled.assert_not_awaited()
		menu.render.assert_not_awaited()
		incoming.answer.assert_awaited_once_with(
			"Доступ запрещен.",
			show_alert=True,
		)

	asyncio.run(scenario())
