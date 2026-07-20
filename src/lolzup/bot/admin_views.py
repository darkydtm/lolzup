import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from lolzup.bot.menu import MenuView
from lolzup.db.repositories import UserRecord


def administrator_list_view(administrators: list[UserRecord]) -> MenuView:
	if administrators:
		lines = [
			f"{index}. {_identity_text(user)}"
			for index, user in enumerate(administrators, start=1)
		]
		body = "\n".join(lines)
	else:
		body = "Список администраторов пуст."
	rows = [
		[
			InlineKeyboardButton(
				text=f"Удалить {_button_identity(user)}",
				callback_data=f"admin:remove:{user.id}",
			)
		]
		for user in administrators
	]
	rows.extend(
		[
			[
				InlineKeyboardButton(
					text="Добавить администратора",
					callback_data="admins:add",
				)
			],
			[
				InlineKeyboardButton(
					text="К настройкам",
					callback_data="menu:settings",
				)
			],
		]
	)
	return MenuView(
		text=f"Администраторы\n\n{body}",
		reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
	)


def administrator_remove_confirmation_view(user: UserRecord) -> MenuView:
	return MenuView(
		text=f"Удалить администратора {_identity_text(user)}?",
		reply_markup=InlineKeyboardMarkup(
			inline_keyboard=[
				[
					InlineKeyboardButton(
						text="Удалить",
						callback_data=f"admin:remove-confirm:{user.id}",
					),
					InlineKeyboardButton(
						text="Отмена",
						callback_data="admins:list",
					),
				]
			]
		),
	)


def find_administrator(
	administrators: list[UserRecord],
	user_id: uuid.UUID,
) -> UserRecord | None:
	return next((user for user in administrators if user.id == user_id), None)


def _identity_text(user: UserRecord) -> str:
	if user.username:
		return f"@{user.username} - ID {user.telegram_id}"
	return f"ID {user.telegram_id}"


def _button_identity(user: UserRecord) -> str:
	return f"@{user.username}" if user.username else str(user.telegram_id)
