import uuid

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from lolzup.bot.keyboards import CANCEL_TEXT, default_reply_keyboard
from lolzup.bot.menu import MenuSection, MenuService, menu_view
from lolzup.bot.states import RETURN_MENU_KEY

cancel_router = Router(name="global-cancel")


async def cancel_active_input(
	message: Message,
	state: FSMContext,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	global_bump_enabled: bool = True,
) -> None:
	state_data = await state.get_data()
	raw_section = state_data.get(RETURN_MENU_KEY, MenuSection.MAIN.value)
	state_data.clear()
	await state.clear()
	try:
		section = MenuSection(raw_section)
	except ValueError:
		section = MenuSection.MAIN
	await menu_service.render(
		menu_user_id,
		message.chat.id,
		menu_view(section, global_bump_enabled),
	)
	await message.answer(
		"Действие отменено.",
		reply_markup=default_reply_keyboard(),
	)


cancel_router.message.register(
	cancel_active_input,
	StateFilter("*"),
	Command("cancel"),
)
cancel_router.message.register(
	cancel_active_input,
	StateFilter("*"),
	F.text == CANCEL_TEXT,
)
