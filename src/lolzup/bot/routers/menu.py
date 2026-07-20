import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from lolzup.bot.keyboards import (
	MAIN_MENU_TEXT,
	SETTINGS_TEXT,
)
from lolzup.bot.menu import MenuSection, MenuService, menu_view

menu_router = Router(name="menu")


async def _render_for_message(
	message: Message,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	section: MenuSection,
	global_bump_enabled: bool = True,
) -> None:
	await menu_service.render(
		menu_user_id,
		message.chat.id,
		menu_view(section, global_bump_enabled),
	)


async def open_main_menu(
	message: Message,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	global_bump_enabled: bool = True,
) -> None:
	await _render_for_message(
		message,
		menu_service,
		menu_user_id,
		MenuSection.MAIN,
		global_bump_enabled,
	)


async def open_settings_menu(
	message: Message,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	await _render_for_message(
		message,
		menu_service,
		menu_user_id,
		MenuSection.SETTINGS,
	)


async def navigate_inline(
	callback: CallbackQuery,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	global_bump_enabled: bool = True,
) -> None:
	if callback.message is None or callback.data is None:
		await callback.answer()
		return
	section = {
		"menu:main": MenuSection.MAIN,
		"menu:settings": MenuSection.SETTINGS,
	}.get(callback.data)
	if section is not None:
		await menu_service.render(
			menu_user_id,
			callback.message.chat.id,
			menu_view(section, global_bump_enabled),
		)
	await callback.answer()


menu_router.message.register(open_main_menu, F.text == MAIN_MENU_TEXT)
menu_router.message.register(open_settings_menu, F.text == SETTINGS_TEXT)
menu_router.callback_query.register(
	navigate_inline,
	F.data.in_({"menu:main", "menu:settings"}),
)
