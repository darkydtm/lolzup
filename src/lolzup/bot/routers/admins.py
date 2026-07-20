import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from lolzup.access import (
	AccessAction,
	AccessDeniedError,
	AccessService,
	AlreadyAdministratorError,
	InvalidAdministratorIdentityError,
	UnknownAdministratorError,
)
from lolzup.bot.admin_views import (
	administrator_list_view,
	administrator_remove_confirmation_view,
	find_administrator,
)
from lolzup.bot.keyboards import default_reply_keyboard, input_reply_keyboard
from lolzup.bot.menu import MenuSection, MenuService
from lolzup.bot.states import AdministratorStates, set_input_return_menu

admins_router = Router(name="administrators")


async def open_administrators(
	callback: CallbackQuery,
	access_service: AccessService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await _render_administrators(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		access_service,
		callback.from_user.id,
	)
	await callback.answer()


async def begin_add_administrator(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(AdministratorStates.identity)
	await set_input_return_menu(state, MenuSection.SETTINGS)
	await callback.message.answer(
		"Отправьте Telegram ID или username администратора.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_administrator_identity(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_message(message, access_service):
		await state.clear()
		return
	user = message.from_user
	if user is None:
		await state.clear()
		return
	try:
		administrator = await access_service.add_administrator(
			user.id,
			message.text or "",
		)
	except InvalidAdministratorIdentityError:
		await message.answer(
			"Неверный ID или username. Повторите ввод.",
			reply_markup=input_reply_keyboard(),
		)
		return
	except UnknownAdministratorError:
		await message.answer(
			"Username не найден. Пользователь должен сначала открыть бота.",
			reply_markup=input_reply_keyboard(),
		)
		return
	except AlreadyAdministratorError:
		await message.answer(
			"Этот пользователь уже является администратором.",
			reply_markup=input_reply_keyboard(),
		)
		return
	await state.clear()
	await _render_administrators(
		menu_service,
		menu_user_id,
		message.chat.id,
		access_service,
		user.id,
	)
	identity = (
		f"@{administrator.username}"
		if administrator.username
		else str(administrator.telegram_id)
	)
	await message.answer(
		f"Администратор {identity} добавлен.",
		reply_markup=default_reply_keyboard(),
	)


async def confirm_administrator_removal(
	callback: CallbackQuery,
	access_service: AccessService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	parsed = _callback_user(callback)
	if parsed is None:
		await callback.answer("Администратор не найден.", show_alert=True)
		return
	chat_id, user_id = parsed
	administrators = await access_service.list_administrators(callback.from_user.id)
	administrator = find_administrator(administrators, user_id)
	if administrator is None:
		await callback.answer("Администратор не найден.", show_alert=True)
		return
	await menu_service.render(
		menu_user_id,
		chat_id,
		administrator_remove_confirmation_view(administrator),
	)
	await callback.answer()


async def remove_administrator(
	callback: CallbackQuery,
	access_service: AccessService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	parsed = _callback_user(callback)
	if parsed is None:
		await callback.answer("Администратор не найден.", show_alert=True)
		return
	chat_id, user_id = parsed
	try:
		await access_service.remove_administrator(callback.from_user.id, user_id)
	except UnknownAdministratorError:
		await callback.answer("Администратор не найден.", show_alert=True)
		return
	await _render_administrators(
		menu_service,
		menu_user_id,
		chat_id,
		access_service,
		callback.from_user.id,
	)
	await callback.answer("Администратор удален.")


async def _render_administrators(
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	chat_id: int,
	access_service: AccessService,
	actor_telegram_id: int,
) -> None:
	administrators = await access_service.list_administrators(actor_telegram_id)
	await menu_service.render(
		menu_user_id,
		chat_id,
		administrator_list_view(administrators),
	)


async def _authorize_owner_callback(
	callback: CallbackQuery,
	access_service: AccessService,
) -> bool:
	if callback.from_user is None:
		await callback.answer()
		return False
	try:
		await access_service.require(
			callback.from_user.id,
			AccessAction.MANAGE_ADMINS,
		)
	except AccessDeniedError:
		await callback.answer("Доступ запрещен.", show_alert=True)
		return False
	return True


async def _authorize_owner_message(
	message: Message,
	access_service: AccessService,
) -> bool:
	if message.from_user is None:
		return False
	try:
		await access_service.require(
			message.from_user.id,
			AccessAction.MANAGE_ADMINS,
		)
	except AccessDeniedError:
		await message.answer("Доступ запрещен.")
		return False
	return True


def _callback_user(callback: CallbackQuery) -> tuple[int, uuid.UUID] | None:
	if callback.message is None or callback.data is None:
		return None
	try:
		user_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
	except ValueError:
		return None
	return callback.message.chat.id, user_id


admins_router.message.register(
	receive_administrator_identity,
	AdministratorStates.identity,
)
admins_router.callback_query.register(open_administrators, F.data == "admins:list")
admins_router.callback_query.register(
	begin_add_administrator,
	F.data == "admins:add",
)
admins_router.callback_query.register(
	confirm_administrator_removal,
	F.data.startswith("admin:remove:"),
)
admins_router.callback_query.register(
	remove_administrator,
	F.data.startswith("admin:remove-confirm:"),
)
