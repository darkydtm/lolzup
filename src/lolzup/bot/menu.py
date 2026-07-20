import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from lolzup.bot.keyboards import (
	back_to_main_keyboard,
	main_inline_keyboard,
	settings_inline_keyboard,
)


class MenuSection(StrEnum):
	MAIN = "main"
	TOPICS = "topics"
	SETTINGS = "settings"


class MenuStore(Protocol):
	async def get(self, user_id: uuid.UUID, chat_id: int) -> int | None: ...

	async def save(
		self,
		user_id: uuid.UUID,
		chat_id: int,
		message_id: int,
	) -> None: ...


@dataclass(frozen=True, slots=True)
class MenuView:
	text: str
	reply_markup: InlineKeyboardMarkup


def menu_view(
	section: MenuSection,
	global_enabled: bool = True,
	can_toggle_global: bool = True,
) -> MenuView:
	if section is MenuSection.TOPICS:
		return MenuView(
			text="Темы\n\nСписок тем пуст.",
			reply_markup=back_to_main_keyboard(),
		)
	if section is MenuSection.SETTINGS:
		return MenuView(
			text="Настройки",
			reply_markup=settings_inline_keyboard(),
		)

	status = "включено" if global_enabled else "выключено"
	return MenuView(
		text=f"Главное меню\n\nАвтоподнятие: {status}",
		reply_markup=main_inline_keyboard(global_enabled, can_toggle_global),
	)


class MenuService:
	def __init__(self, bot: Bot, menus: MenuStore) -> None:
		self._bot = bot
		self._menus = menus

	async def render(
		self,
		user_id: uuid.UUID,
		chat_id: int,
		view: MenuView,
	) -> Message | None:
		message_id = await self._menus.get(user_id, chat_id)
		if message_id is not None:
			try:
				edited = await self._bot.edit_message_text(
					chat_id=chat_id,
					message_id=message_id,
					text=view.text,
					reply_markup=view.reply_markup,
				)
			except TelegramBadRequest as error:
				if "message is not modified" in str(error).casefold():
					return None
			else:
				return edited if isinstance(edited, Message) else None

		replacement = await self._bot.send_message(
			chat_id=chat_id,
			text=view.text,
			reply_markup=view.reply_markup,
		)
		await self._menus.save(user_id, chat_id, replacement.message_id)
		return replacement
