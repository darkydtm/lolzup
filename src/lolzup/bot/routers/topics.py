import uuid
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from lolzup.access import AccessAction, AccessDeniedError, AccessService
from lolzup.bot.keyboards import (
	TOPICS_TEXT,
	default_reply_keyboard,
	input_reply_keyboard,
)
from lolzup.bot.menu import MenuSection, MenuService
from lolzup.bot.states import TopicStates, set_input_return_menu
from lolzup.bot.topic_views import (
	parse_interval_seconds,
	topic_detail_view,
	topic_list_view,
	topic_remove_confirmation_view,
)
from lolzup.db.repositories import DuplicateTopicError
from lolzup.forum import BumpOutcome, ForumApiError
from lolzup.topics.parser import InvalidTopicReferenceError
from lolzup.topics.service import TopicNotFoundError, TopicService

TOPIC_ID_KEY = "topic_id"


async def open_topics(
	message: Message,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_message(message, access_service):
		return
	await _render_list(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		0,
	)


async def navigate_topics(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	if callback.message is None or callback.data is None:
		await callback.answer()
		return
	try:
		page = int(callback.data.rsplit(":", 1)[1])
	except ValueError:
		page = 0
	await _render_list(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		page,
	)
	await callback.answer()


async def open_topic(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	try:
		topic = await topic_service.get(topic_id)
	except TopicNotFoundError:
		await callback.answer("Тема не найдена.", show_alert=True)
		return
	settings = await topic_service.settings()
	await menu_service.render(
		menu_user_id,
		chat_id,
		topic_detail_view(topic, settings, datetime.now(UTC)),
	)
	await callback.answer()


async def begin_add_topic(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(TopicStates.reference)
	await set_input_return_menu(state, MenuSection.TOPICS)
	await callback.message.answer(
		"Отправьте ID темы или ссылку на lolz.live или zelenka.guru.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_topic_reference(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_message(message, access_service):
		await state.clear()
		return
	try:
		await topic_service.add(message.text or "")
	except InvalidTopicReferenceError:
		await message.answer(
			"Неверный ID или ссылка. Проверьте формат и повторите ввод.",
			reply_markup=input_reply_keyboard(),
		)
		return
	except DuplicateTopicError:
		await message.answer(
			"Эта тема уже добавлена.",
			reply_markup=input_reply_keyboard(),
		)
		return
	except ForumApiError:
		await message.answer(
			"Не удалось получить тему через Forum API. Проверьте доступ и повторите.",
			reply_markup=input_reply_keyboard(),
		)
		return
	await state.clear()
	await _render_list(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		0,
	)
	await message.answer(
		"Тема добавлена.",
		reply_markup=default_reply_keyboard(),
	)


async def toggle_topic_auto(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	topic = await topic_service.get(topic_id)
	updated = await topic_service.set_enabled(topic_id, not topic.auto_bump_enabled)
	await _render_detail(menu_service, menu_user_id, chat_id, topic_service, updated.id)
	await callback.answer()


async def toggle_custom_interval(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	topic = await topic_service.get(topic_id)
	if not topic.custom_interval_enabled and topic.custom_interval_seconds is None:
		await _start_interval_input(callback, state, topic_id)
		return
	updated = await topic_service.set_custom_interval(
		topic_id,
		not topic.custom_interval_enabled,
	)
	await _render_detail(menu_service, menu_user_id, chat_id, topic_service, updated.id)
	await callback.answer()


async def begin_custom_interval(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	_, topic_id = parsed
	await _start_interval_input(callback, state, topic_id)


async def receive_custom_interval(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_message(message, access_service):
		await state.clear()
		return
	state_data = await state.get_data()
	raw_topic_id = state_data.get(TOPIC_ID_KEY)
	try:
		topic_id = uuid.UUID(str(raw_topic_id))
		seconds = parse_interval_seconds(message.text or "")
	except (ValueError, TypeError):
		await message.answer(
			"Неверный интервал. Пример: 72, 90 мин, 6 ч или 3 д.",
			reply_markup=input_reply_keyboard(),
		)
		return
	await topic_service.set_custom_interval(topic_id, True, seconds)
	await state.clear()
	await _render_detail(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		topic_id,
	)
	await message.answer(
		"Индивидуальный интервал сохранен.",
		reply_markup=default_reply_keyboard(),
	)


async def bump_topic_now(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	result = await topic_service.manual_bump(topic_id)
	await _render_detail(menu_service, menu_user_id, chat_id, topic_service, topic_id)
	text = (
		"Тема поднята."
		if result.outcome is BumpOutcome.SUCCESS
		else "Поднять тему не удалось."
	)
	await callback.answer(text, show_alert=result.outcome is not BumpOutcome.SUCCESS)


async def confirm_topic_removal(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	topic = await topic_service.get(topic_id)
	await menu_service.render(
		menu_user_id,
		chat_id,
		topic_remove_confirmation_view(topic),
	)
	await callback.answer()


async def remove_topic(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_callback(callback, access_service):
		return
	parsed = _callback_topic(callback)
	if parsed is None:
		return
	chat_id, topic_id = parsed
	await topic_service.remove(topic_id)
	await _render_list(menu_service, menu_user_id, chat_id, topic_service, 0)
	await callback.answer("Тема удалена.")


async def _render_list(
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	chat_id: int,
	topic_service: TopicService,
	page: int,
) -> None:
	await menu_service.render(
		menu_user_id,
		chat_id,
		topic_list_view(await topic_service.list(), page, datetime.now(UTC)),
	)


async def _render_detail(
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	chat_id: int,
	topic_service: TopicService,
	topic_id: uuid.UUID,
) -> None:
	topic = await topic_service.get(topic_id)
	settings = await topic_service.settings()
	await menu_service.render(
		menu_user_id,
		chat_id,
		topic_detail_view(topic, settings, datetime.now(UTC)),
	)


async def _start_interval_input(
	callback: CallbackQuery,
	state: FSMContext,
	topic_id: uuid.UUID,
) -> None:
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(TopicStates.custom_interval)
	await set_input_return_menu(state, MenuSection.TOPICS)
	await state.update_data({TOPIC_ID_KEY: str(topic_id)})
	await callback.message.answer(
		"Введите интервал. Пример: 72, 90 мин, 6 ч или 3 д.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def _authorize_message(
	message: Message,
	access_service: AccessService,
) -> bool:
	if message.from_user is None:
		return False
	try:
		await access_service.require(
			message.from_user.id,
			AccessAction.MANAGE_TOPICS,
		)
	except AccessDeniedError:
		await message.answer("Доступ запрещен.")
		return False
	return True


async def _authorize_callback(
	callback: CallbackQuery,
	access_service: AccessService,
) -> bool:
	if callback.from_user is None:
		await callback.answer()
		return False
	try:
		await access_service.require(
			callback.from_user.id,
			AccessAction.MANAGE_TOPICS,
		)
	except AccessDeniedError:
		await callback.answer("Доступ запрещен.", show_alert=True)
		return False
	return True


def _callback_topic(callback: CallbackQuery) -> tuple[int, uuid.UUID] | None:
	if callback.message is None or callback.data is None:
		return None
	try:
		topic_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
	except ValueError:
		return None
	return callback.message.chat.id, topic_id


def build_topics_router() -> Router:
	router = Router(name="topics")
	router.message.register(open_topics, F.text == TOPICS_TEXT)
	router.message.register(receive_topic_reference, TopicStates.reference)
	router.message.register(receive_custom_interval, TopicStates.custom_interval)
	router.callback_query.register(navigate_topics, F.data == "menu:topics")
	router.callback_query.register(navigate_topics, F.data.startswith("topics:page:"))
	router.callback_query.register(begin_add_topic, F.data == "topics:add")
	router.callback_query.register(open_topic, F.data.startswith("topic:open:"))
	router.callback_query.register(toggle_topic_auto, F.data.startswith("topic:auto:"))
	router.callback_query.register(
		toggle_custom_interval,
		F.data.startswith("topic:custom:"),
	)
	router.callback_query.register(
		begin_custom_interval,
		F.data.startswith("topic:interval:"),
	)
	router.callback_query.register(bump_topic_now, F.data.startswith("topic:bump:"))
	router.callback_query.register(
		confirm_topic_removal,
		F.data.startswith("topic:remove:"),
	)
	router.callback_query.register(
		remove_topic,
		F.data.startswith("topic:remove-confirm:"),
	)
	return router


topics_router = build_topics_router()
