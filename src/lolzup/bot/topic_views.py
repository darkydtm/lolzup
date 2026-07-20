import math
from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from lolzup.bot.menu import MenuView
from lolzup.db.repositories import SettingsRecord, TopicRecord
from lolzup.topics.schedule import TopicTimingState, format_topic_timing

TOPICS_PER_PAGE = 5


def topic_list_view(
	topics: list[TopicRecord],
	page: int,
	now: datetime,
) -> MenuView:
	page_count = max(1, math.ceil(len(topics) / TOPICS_PER_PAGE))
	current_page = min(max(page, 0), page_count - 1)
	start = current_page * TOPICS_PER_PAGE
	page_topics = topics[start : start + TOPICS_PER_PAGE]

	if page_topics:
		blocks = [
			_format_topic_row(start + index + 1, topic, now)
			for index, topic in enumerate(page_topics)
		]
		body = "\n\n".join(blocks)
	else:
		body = "Список тем пуст."

	text = f"Темы - страница {current_page + 1}/{page_count}\n\n{body}"
	rows = [
		[
			InlineKeyboardButton(
				text=_truncate(topic.title, 48),
				callback_data=f"topic:open:{topic.id}",
			)
		]
		for topic in page_topics
	]
	navigation: list[InlineKeyboardButton] = []
	if current_page > 0:
		navigation.append(
			InlineKeyboardButton(
				text="‹",
				callback_data=f"topics:page:{current_page - 1}",
			)
		)
	if current_page + 1 < page_count:
		navigation.append(
			InlineKeyboardButton(
				text="›",
				callback_data=f"topics:page:{current_page + 1}",
			)
		)
	if navigation:
		rows.append(navigation)
	rows.extend(
		[
			[
				InlineKeyboardButton(
					text="Добавить тему",
					callback_data="topics:add",
				)
			],
			[
				InlineKeyboardButton(
					text="Главное меню",
					callback_data="menu:main",
				)
			],
		]
	)
	return MenuView(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def topic_detail_view(
	topic: TopicRecord,
	settings: SettingsRecord,
	now: datetime,
) -> MenuView:
	timing = format_topic_timing(
		now,
		topic.last_success_at,
		topic.next_bump_at,
		topic.auto_bump_enabled,
		topic.last_error,
	)
	interval_seconds = (
		topic.custom_interval_seconds
		if topic.custom_interval_enabled
		else settings.global_interval_seconds
	)
	interval_text = (
		"не задан" if interval_seconds is None else _format_duration(interval_seconds)
	)
	interval_source = (
		"индивидуальный" if topic.custom_interval_enabled else "глобальный"
	)
	latest_result = (
		f"ошибка: {topic.last_error}"
		if topic.last_error
		else "успешно"
		if topic.last_success_at is not None
		else "нет данных"
	)
	text = "\n".join(
		[
			topic.title,
			"",
			f"ID темы: {topic.thread_id}",
			f"Автоподнятие: {_enabled_text(topic.auto_bump_enabled)}",
			f"Интервал: {interval_text} ({interval_source})",
			f"Предыдущее поднятие: {_format_datetime(topic.last_success_at)}",
			f"Следующее поднятие: {_format_datetime(topic.next_bump_at)}",
			f"Осталось: {_timing_text(timing.state, timing.remaining_seconds)}",
			f"Последний результат: {latest_result}",
		]
	)
	auto_text = (
		"Выключить автоподнятие" if topic.auto_bump_enabled else "Включить автоподнятие"
	)
	custom_text = (
		"Отключить свой интервал"
		if topic.custom_interval_enabled
		else "Включить свой интервал"
	)
	return MenuView(
		text=text,
		reply_markup=InlineKeyboardMarkup(
			inline_keyboard=[
				[
					InlineKeyboardButton(
						text=auto_text,
						callback_data=f"topic:auto:{topic.id}",
					)
				],
				[
					InlineKeyboardButton(
						text=custom_text,
						callback_data=f"topic:custom:{topic.id}",
					)
				],
				[
					InlineKeyboardButton(
						text="Изменить интервал",
						callback_data=f"topic:interval:{topic.id}",
					)
				],
				[
					InlineKeyboardButton(
						text="Поднять сейчас",
						callback_data=f"topic:bump:{topic.id}",
					)
				],
				[
					InlineKeyboardButton(
						text="Удалить",
						callback_data=f"topic:remove:{topic.id}",
					)
				],
				[
					InlineKeyboardButton(
						text="К списку",
						callback_data="topics:page:0",
					)
				],
			]
		),
	)


def topic_remove_confirmation_view(topic: TopicRecord) -> MenuView:
	return MenuView(
		text=f"Удалить тему «{topic.title}» из бота?\n\nID темы: {topic.thread_id}",
		reply_markup=InlineKeyboardMarkup(
			inline_keyboard=[
				[
					InlineKeyboardButton(
						text="Удалить",
						callback_data=f"topic:remove-confirm:{topic.id}",
					),
					InlineKeyboardButton(
						text="Отмена",
						callback_data=f"topic:open:{topic.id}",
					),
				]
			]
		),
	)


def parse_interval_seconds(value: str) -> int:
	text = value.strip().casefold()
	units = {
		"м": 60,
		"мин": 60,
		"m": 60,
		"ч": 3600,
		"час": 3600,
		"часа": 3600,
		"часов": 3600,
		"h": 3600,
		"д": 86400,
		"день": 86400,
		"дня": 86400,
		"дней": 86400,
		"d": 86400,
	}
	parts = text.split()
	if len(parts) == 1:
		number = parts[0]
		multiplier = 3600
	elif len(parts) == 2 and parts[1] in units:
		number = parts[0]
		multiplier = units[parts[1]]
	else:
		raise ValueError("Interval format is invalid")
	if not number.isdecimal() or int(number) <= 0:
		raise ValueError("Interval must be positive")
	seconds = int(number) * multiplier
	if seconds > 365 * 86400:
		raise ValueError("Interval must not exceed 365 days")
	return seconds


def _format_topic_row(position: int, topic: TopicRecord, now: datetime) -> str:
	timing = format_topic_timing(
		now,
		topic.last_success_at,
		topic.next_bump_at,
		topic.auto_bump_enabled,
		topic.last_error,
	)
	return "\n".join(
		[
			f"{position}. {topic.title}",
			f"Предыдущее: {_format_datetime(topic.last_success_at)}",
			f"Следующее: {_timing_text(timing.state, timing.remaining_seconds)}",
		]
	)


def _timing_text(
	state: TopicTimingState,
	remaining_seconds: int | None,
) -> str:
	if state is TopicTimingState.DISABLED:
		return "отключено"
	if state is TopicTimingState.ERROR:
		return "ошибка"
	if state is TopicTimingState.PENDING:
		return "ожидает планирования"
	if state is TopicTimingState.OVERDUE:
		return "просрочено"
	if remaining_seconds is None:
		return "неизвестно"
	return f"через {_format_duration(remaining_seconds)}"


def _format_duration(seconds: int) -> str:
	minutes = max(1, math.ceil(seconds / 60))
	days, remainder = divmod(minutes, 24 * 60)
	hours, minutes = divmod(remainder, 60)
	parts = []
	if days:
		parts.append(f"{days} д.")
	if hours:
		parts.append(f"{hours} ч.")
	if minutes and len(parts) < 2:
		parts.append(f"{minutes} мин.")
	return " ".join(parts or ["1 мин."])


def _format_datetime(value: datetime | None) -> str:
	if value is None:
		return "никогда"
	return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _enabled_text(enabled: bool) -> str:
	return "включено" if enabled else "выключено"


def _truncate(value: str, limit: int) -> str:
	if len(value) <= limit:
		return value
	return f"{value[: limit - 3]}..."
