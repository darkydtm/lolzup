from datetime import datetime

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from lolzup.access import (
	AccessAction,
	AccessDeniedError,
	AccessService,
	ActorRole,
)
from lolzup.bot.keyboards import default_reply_keyboard, input_reply_keyboard
from lolzup.bot.menu import MenuSection, MenuService, menu_view
from lolzup.bot.states import SetupStates, set_input_return_menu
from lolzup.db.repositories import UserRecord
from lolzup.security.setup import (
	AlreadyInitializedError,
	InvalidPasswordError,
	NotInitializedError,
	SetupService,
	UnlockThrottledError,
)

SETUP_PASSWORD_KEY = "setup_password"


async def start_bot(
	message: Message,
	state: FSMContext,
	setup_service: SetupService,
	access_service: AccessService,
	menu_service: MenuService,
) -> None:
	user = message.from_user
	if user is None:
		return
	await state.clear()
	initialized = await setup_service.is_initialized()
	role = await access_service.role_for(user.id)
	if role is ActorRole.OWNER and not initialized:
		await _start_password_input(message, state)
		return

	try:
		record = await access_service.record_user(user.id, user.username)
	except AccessDeniedError:
		if role is ActorRole.OWNER:
			await _start_unlock_input(message, state)
		else:
			await message.answer(
				"Бот заблокирован. Дождитесь разблокировки владельцем.",
				reply_markup=default_reply_keyboard(),
			)
		return

	role = await access_service.role_for(user.id)
	if role is ActorRole.DENIED:
		await message.answer(
			"Доступ запрещен.",
			reply_markup=default_reply_keyboard(),
		)
		return
	await _show_ready_menu(message, menu_service, record)


async def receive_setup_password(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _require_owner(message, access_service, AccessAction.INITIALIZE):
		await state.clear()
		return
	await _delete_secret_message(message)
	password = message.text or ""
	if not password:
		await state.clear()
		await message.answer(
			"Ключ шифрования не может быть пустым. Запустите /start снова.",
			reply_markup=default_reply_keyboard(),
		)
		return
	await state.update_data({SETUP_PASSWORD_KEY: password})
	await state.set_state(SetupStates.password_confirmation)
	await message.answer(
		"Повторите ключ шифрования.",
		reply_markup=input_reply_keyboard(),
	)


async def receive_setup_password_confirmation(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _require_owner(message, access_service, AccessAction.INITIALIZE):
		await state.clear()
		return
	await _delete_secret_message(message)
	state_data = await state.get_data()
	password = state_data.pop(SETUP_PASSWORD_KEY, None)
	state_data.clear()
	confirmation = message.text
	if not isinstance(password, str) or confirmation != password:
		await state.clear()
		await message.answer(
			"Ключи не совпадают. Запустите /start снова.",
			reply_markup=default_reply_keyboard(),
		)
		return
	await state.update_data({SETUP_PASSWORD_KEY: password})
	await state.set_state(SetupStates.api_token)
	await message.answer(
		"Отправьте API token Lolzteam.",
		reply_markup=input_reply_keyboard(),
	)


async def receive_api_token(
	message: Message,
	state: FSMContext,
	setup_service: SetupService,
	access_service: AccessService,
	menu_service: MenuService,
) -> None:
	if not await _require_owner(message, access_service, AccessAction.INITIALIZE):
		await state.clear()
		return
	await _delete_secret_message(message)
	state_data = await state.get_data()
	password = state_data.pop(SETUP_PASSWORD_KEY, None)
	state_data.clear()
	api_token = message.text or ""
	await state.clear()
	if not isinstance(password, str) or not api_token:
		await message.answer(
			"Данные настройки потеряны. Запустите /start снова.",
			reply_markup=default_reply_keyboard(),
		)
		return
	try:
		await setup_service.initialize(password, api_token)
	except AlreadyInitializedError:
		await message.answer(
			"Бот уже настроен. Запустите /start снова.",
			reply_markup=default_reply_keyboard(),
		)
		return

	user = message.from_user
	if user is None:
		return
	record = await access_service.record_user(user.id, user.username)
	await _show_ready_menu(message, menu_service, record)


async def receive_unlock_password(
	message: Message,
	state: FSMContext,
	setup_service: SetupService,
	access_service: AccessService,
	menu_service: MenuService,
) -> None:
	if not await _require_owner(message, access_service, AccessAction.UNLOCK):
		await state.clear()
		return
	await _delete_secret_message(message)
	password = message.text or ""
	await state.clear()
	try:
		await setup_service.unlock(password)
	except InvalidPasswordError:
		await message.answer(
			"Неверный ключ. Запустите /start для новой попытки.",
			reply_markup=default_reply_keyboard(),
		)
		return
	except UnlockThrottledError as error:
		retry_at = _format_datetime(error.retry_at)
		await message.answer(
			f"Слишком много попыток. Повторите после {retry_at}.",
			reply_markup=default_reply_keyboard(),
		)
		return
	except NotInitializedError:
		await message.answer(
			"Бот еще не настроен. Запустите /start снова.",
			reply_markup=default_reply_keyboard(),
		)
		return

	user = message.from_user
	if user is None:
		return
	record = await access_service.record_user(user.id, user.username)
	await _show_ready_menu(message, menu_service, record)


async def _start_password_input(message: Message, state: FSMContext) -> None:
	await state.set_state(SetupStates.password)
	await set_input_return_menu(state, MenuSection.MAIN)
	await message.answer(
		"Создайте ключ шифрования. Потерянный ключ восстановить невозможно.",
		reply_markup=input_reply_keyboard(),
	)


async def _start_unlock_input(message: Message, state: FSMContext) -> None:
	await state.set_state(SetupStates.unlock_password)
	await set_input_return_menu(state, MenuSection.MAIN)
	await message.answer(
		"Введите ключ шифрования для разблокировки.",
		reply_markup=input_reply_keyboard(),
	)


async def _show_ready_menu(
	message: Message,
	menu_service: MenuService,
	user: UserRecord,
) -> None:
	await menu_service.render(
		user.id,
		message.chat.id,
		menu_view(MenuSection.MAIN),
	)
	await message.answer(
		"Бот готов к работе.",
		reply_markup=default_reply_keyboard(),
	)


async def _require_owner(
	message: Message,
	access_service: AccessService,
	action: AccessAction,
) -> bool:
	user = message.from_user
	if user is None:
		return False
	try:
		await access_service.require(user.id, action)
	except AccessDeniedError:
		await message.answer("Доступ запрещен.")
		return False
	return True


async def _delete_secret_message(message: Message) -> None:
	try:
		await message.delete()
	except TelegramAPIError:
		pass


def _format_datetime(value: datetime) -> str:
	return value.strftime("%d.%m.%Y %H:%M:%S %Z")


def build_setup_router() -> Router:
	router = Router(name="setup")
	router.message.register(start_bot, CommandStart())
	router.message.register(
		receive_setup_password,
		SetupStates.password,
	)
	router.message.register(
		receive_setup_password_confirmation,
		SetupStates.password_confirmation,
	)
	router.message.register(
		receive_api_token,
		SetupStates.api_token,
	)
	router.message.register(
		receive_unlock_password,
		SetupStates.unlock_password,
	)
	return router


setup_router = build_setup_router()
