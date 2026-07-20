import uuid

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from lolzup.bot.keyboards import CANCEL_TEXT, default_reply_keyboard
from lolzup.bot.menu import MenuSection, MenuService, menu_view
from lolzup.bot.states import RETURN_MENU_KEY
from lolzup.security.runtime import RuntimeVault


async def cancel_active_input(
	message: Message,
	state: FSMContext,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	runtime_vault: RuntimeVault,
	global_bump_enabled: bool = True,
	can_manage_global_bump: bool = True,
) -> None:
	state_data = await state.get_data()
	raw_section = state_data.get(RETURN_MENU_KEY, MenuSection.MAIN.value)
	state_data.clear()
	await state.clear()
	try:
		section = MenuSection(raw_section)
	except ValueError:
		section = MenuSection.MAIN
	if runtime_vault.is_unlocked:
		await menu_service.render(
			menu_user_id,
			message.chat.id,
			menu_view(section, global_bump_enabled, can_manage_global_bump),
		)
	await message.answer(
		"Действие отменено.",
		reply_markup=default_reply_keyboard(),
	)


def build_cancel_router() -> Router:
	router = Router(name="global-cancel")
	router.message.register(
		cancel_active_input,
		StateFilter("*"),
		Command("cancel"),
	)
	router.message.register(
		cancel_active_input,
		StateFilter("*"),
		F.text == CANCEL_TEXT,
	)
	return router


cancel_router = build_cancel_router()
