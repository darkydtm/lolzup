import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from lolzup.access import AccessAction, AccessDeniedError, AccessService
from lolzup.bot.keyboards import (
	MAIN_MENU_TEXT,
)
from lolzup.bot.menu import MenuSection, MenuService, menu_view
from lolzup.db.migrations import EncryptionMigrationService
from lolzup.db.models import MigrationStatus
from lolzup.topics.service import TopicService


async def _render_for_message(
	message: Message,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	section: MenuSection,
	global_bump_enabled: bool = True,
	can_manage_global_bump: bool = True,
) -> None:
	await menu_service.render(
		menu_user_id,
		message.chat.id,
		menu_view(section, global_bump_enabled, can_manage_global_bump),
	)


async def open_main_menu(
	message: Message,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	global_bump_enabled: bool = True,
	can_manage_global_bump: bool = True,
) -> None:
	await _render_for_message(
		message,
		menu_service,
		menu_user_id,
		MenuSection.MAIN,
		global_bump_enabled,
		can_manage_global_bump,
	)


async def navigate_inline(
	callback: CallbackQuery,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	global_bump_enabled: bool = True,
	can_manage_global_bump: bool = True,
) -> None:
	if callback.message is None or callback.data is None:
		await callback.answer()
		return
	section = {
		"menu:main": MenuSection.MAIN,
	}.get(callback.data)
	if section is not None:
		await menu_service.render(
			menu_user_id,
			callback.message.chat.id,
			menu_view(section, global_bump_enabled, can_manage_global_bump),
		)
	await callback.answer()


async def toggle_global_bump(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	try:
		await access_service.require(
			callback.from_user.id,
			AccessAction.MANAGE_GLOBAL_BUMP,
		)
	except AccessDeniedError:
		await callback.answer("Доступ запрещен.", show_alert=True)
		return
	if (await migration_service.status()).status is not MigrationStatus.IDLE:
		await callback.answer(
			"Настройки временно недоступны во время миграции.",
			show_alert=True,
		)
		return
	if callback.message is None:
		await callback.answer()
		return
	settings = await topic_service.settings()
	updated = await topic_service.set_global_enabled(not settings.global_bump_enabled)
	await menu_service.render(
		menu_user_id,
		callback.message.chat.id,
		menu_view(MenuSection.MAIN, updated.global_bump_enabled),
	)
	await callback.answer()


def build_menu_router() -> Router:
	router = Router(name="menu")
	router.message.register(open_main_menu, F.text == MAIN_MENU_TEXT)
	router.callback_query.register(
		navigate_inline,
		F.data == "menu:main",
	)
	router.callback_query.register(
		toggle_global_bump,
		F.data == "scheduler:toggle",
	)
	return router


menu_router = build_menu_router()
