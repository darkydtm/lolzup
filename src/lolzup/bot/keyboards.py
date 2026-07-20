from aiogram.types import (
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	KeyboardButton,
	ReplyKeyboardMarkup,
)

MAIN_MENU_TEXT = "Главное меню"
TOPICS_TEXT = "Темы"
SETTINGS_TEXT = "Настройки"
CANCEL_TEXT = "Отмена"


def default_reply_keyboard() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text=MAIN_MENU_TEXT),
				KeyboardButton(text=TOPICS_TEXT),
			],
			[KeyboardButton(text=SETTINGS_TEXT)],
		],
		resize_keyboard=True,
		is_persistent=True,
	)


def input_reply_keyboard() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text=MAIN_MENU_TEXT),
				KeyboardButton(text=TOPICS_TEXT),
			],
			[KeyboardButton(text=SETTINGS_TEXT)],
			[KeyboardButton(text=CANCEL_TEXT)],
		],
		resize_keyboard=True,
		is_persistent=True,
	)


def main_inline_keyboard(
	global_enabled: bool,
	can_toggle_global: bool = True,
) -> InlineKeyboardMarkup:
	status = "Выключить автоподнятие" if global_enabled else "Включить автоподнятие"
	rows = [
		[
			InlineKeyboardButton(text=TOPICS_TEXT, callback_data="menu:topics"),
			InlineKeyboardButton(
				text=SETTINGS_TEXT,
				callback_data="menu:settings",
			),
		]
	]
	if can_toggle_global:
		rows.append(
			[
				InlineKeyboardButton(
					text=status,
					callback_data="scheduler:toggle",
				)
			]
		)
	return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		inline_keyboard=[
			[
				InlineKeyboardButton(
					text=MAIN_MENU_TEXT,
					callback_data="menu:main",
				)
			]
		]
	)


def settings_inline_keyboard() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		inline_keyboard=[
			[
				InlineKeyboardButton(
					text=MAIN_MENU_TEXT,
					callback_data="menu:main",
				)
			],
		]
	)
