from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from lolzup.bot.menu import MenuSection

RETURN_MENU_KEY = "return_menu"


async def set_input_return_menu(
	state: FSMContext,
	section: MenuSection,
) -> None:
	await state.update_data({RETURN_MENU_KEY: section.value})


class SetupStates(StatesGroup):
	password = State()
	password_confirmation = State()
	api_token = State()
	unlock_password = State()


class TopicStates(StatesGroup):
	reference = State()
	custom_interval = State()


class AdministratorStates(StatesGroup):
	identity = State()


class SettingsStates(StatesGroup):
	global_interval = State()
	retry_schedule = State()
	api_token = State()
	current_encryption_password = State()
	encryption_password = State()
	encryption_password_confirmation = State()
