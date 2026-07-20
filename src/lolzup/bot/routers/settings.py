import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from lolzup.access import AccessAction, AccessDeniedError, AccessService, ActorRole
from lolzup.bot.keyboards import (
	SETTINGS_TEXT,
	default_reply_keyboard,
	input_reply_keyboard,
)
from lolzup.bot.menu import MenuSection, MenuService
from lolzup.bot.settings_views import (
	custom_encryption_view,
	disable_encryption_confirmation_view,
	encryption_modes_view,
	policy_from_mask,
	settings_view,
)
from lolzup.bot.states import SettingsStates, set_input_return_menu
from lolzup.bot.topic_views import parse_interval_seconds
from lolzup.db.migrations import (
	EncryptionMigrationRecord,
	EncryptionMigrationService,
	MigrationBatchError,
	MigrationInProgressError,
)
from lolzup.db.models import EncryptionMode, MigrationStatus
from lolzup.security.policy import EncryptionPolicy
from lolzup.security.setup import InvalidPasswordError, SetupService
from lolzup.topics.service import TopicService

CURRENT_PASSWORD_KEY = "current_password"
NEW_PASSWORD_KEY = "new_password"


async def open_settings_message(
	message: Message,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	role = await _authorize_settings_message(message, access_service)
	if role is None:
		return
	await _render_settings(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		migration_service,
		role,
	)


async def open_settings_callback(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	role = await _authorize_settings_callback(callback, access_service)
	if role is None:
		return
	if callback.message is None:
		await callback.answer()
		return
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		role,
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
	if not await _authorize_owner_callback(
		callback,
		access_service,
		AccessAction.MANAGE_GLOBAL_BUMP,
	) or not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	settings = await topic_service.settings()
	await topic_service.set_global_enabled(not settings.global_bump_enabled)
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)
	await callback.answer()


async def begin_global_interval(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
) -> None:
	if await _authorize_settings_callback(
		callback, access_service
	) is None or not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(SettingsStates.global_interval)
	await set_input_return_menu(state, MenuSection.SETTINGS)
	await callback.message.answer(
		"Введите глобальный интервал. Пример: 72, 6 ч или 3 д.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_global_interval(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	role = await _authorize_settings_message(message, access_service)
	if role is None:
		await state.clear()
		return
	if (await migration_service.status()).status is not MigrationStatus.IDLE:
		await state.clear()
		await message.answer("Настройки временно недоступны во время миграции.")
		return
	try:
		seconds = parse_interval_seconds(message.text or "")
	except ValueError:
		await message.answer(
			"Неверный интервал. Пример: 72, 6 ч или 3 д.",
			reply_markup=input_reply_keyboard(),
		)
		return
	await topic_service.set_global_interval(seconds)
	await state.clear()
	await _render_settings(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		migration_service,
		role,
	)
	await message.answer(
		"Глобальный интервал сохранен.",
		reply_markup=default_reply_keyboard(),
	)


async def begin_retry_schedule(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
) -> None:
	if await _authorize_settings_callback(
		callback, access_service
	) is None or not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(SettingsStates.retry_schedule)
	await set_input_return_menu(state, MenuSection.SETTINGS)
	await callback.message.answer(
		"Введите интервалы повторов через запятую. Пример: 1 мин, 5 мин, 15 мин.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_retry_schedule(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	role = await _authorize_settings_message(message, access_service)
	if role is None:
		await state.clear()
		return
	if (await migration_service.status()).status is not MigrationStatus.IDLE:
		await state.clear()
		await message.answer("Настройки временно недоступны во время миграции.")
		return
	try:
		schedule = [
			parse_interval_seconds(part)
			for part in (message.text or "").split(",")
			if part.strip()
		]
		if not schedule:
			raise ValueError
	except ValueError:
		await message.answer(
			"Неверный список. Пример: 1 мин, 5 мин, 15 мин.",
			reply_markup=input_reply_keyboard(),
		)
		return
	await topic_service.set_retry_schedule(schedule)
	await state.clear()
	await _render_settings(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		migration_service,
		role,
	)
	await message.answer(
		"Интервалы повторов сохранены.",
		reply_markup=default_reply_keyboard(),
	)


async def toggle_notification(
	callback: CallbackQuery,
	access_service: AccessService,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	role = await _authorize_settings_callback(callback, access_service)
	if role is None or callback.data is None:
		return
	if not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	settings = await topic_service.settings()
	if callback.data == "settings:notify-success":
		await topic_service.set_notifications(success=not settings.notify_success)
	else:
		await topic_service.set_notifications(errors=not settings.notify_errors)
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		role,
	)
	await callback.answer()


async def begin_api_token(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
) -> None:
	if not await _authorize_owner_callback(
		callback,
		access_service,
		AccessAction.MANAGE_API_TOKEN,
	) or not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(SettingsStates.api_token)
	await set_input_return_menu(state, MenuSection.SETTINGS)
	await callback.message.answer(
		"Отправьте новый API token Lolzteam.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_api_token(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	setup_service: SetupService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	topic_service: TopicService,
) -> None:
	if not await _authorize_owner_message(
		message,
		access_service,
		AccessAction.MANAGE_API_TOKEN,
	):
		await state.clear()
		return
	await _delete_secret_message(message)
	status = await migration_service.status()
	if status.status is not MigrationStatus.IDLE:
		await state.clear()
		await message.answer("Дождитесь завершения миграции.")
		return
	token = message.text or ""
	await state.clear()
	if not token:
		await message.answer("API token не может быть пустым.")
		return
	await setup_service.replace_api_token(token, status.target_policy)
	await topic_service.clear_api_pause()
	await _render_settings(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)
	await message.answer(
		"API token заменен.",
		reply_markup=default_reply_keyboard(),
	)


async def begin_key_change(
	callback: CallbackQuery,
	state: FSMContext,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
) -> None:
	if not await _authorize_owner_callback(
		callback, access_service
	) or not await _require_idle(callback, migration_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await state.set_state(SettingsStates.current_encryption_password)
	await set_input_return_menu(state, MenuSection.SETTINGS)
	await callback.message.answer(
		"Введите текущий ключ шифрования.",
		reply_markup=input_reply_keyboard(),
	)
	await callback.answer()


async def receive_current_password(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _authorize_owner_message(message, access_service):
		await state.clear()
		return
	await _delete_secret_message(message)
	password = message.text or ""
	if not password:
		await state.clear()
		await message.answer("Текущий ключ не может быть пустым.")
		return
	await state.update_data({CURRENT_PASSWORD_KEY: password})
	await state.set_state(SettingsStates.encryption_password)
	await message.answer(
		"Введите новый ключ шифрования.",
		reply_markup=input_reply_keyboard(),
	)


async def receive_new_password(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
) -> None:
	if not await _authorize_owner_message(message, access_service):
		await state.clear()
		return
	await _delete_secret_message(message)
	password = message.text or ""
	if not password:
		await state.clear()
		await message.answer("Новый ключ не может быть пустым.")
		return
	await state.update_data({NEW_PASSWORD_KEY: password})
	await state.set_state(SettingsStates.encryption_password_confirmation)
	await message.answer(
		"Повторите новый ключ шифрования.",
		reply_markup=input_reply_keyboard(),
	)


async def receive_new_password_confirmation(
	message: Message,
	state: FSMContext,
	access_service: AccessService,
	setup_service: SetupService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	topic_service: TopicService,
) -> None:
	if not await _authorize_owner_message(message, access_service):
		await state.clear()
		return
	await _delete_secret_message(message)
	state_data = await state.get_data()
	current_password = state_data.pop(CURRENT_PASSWORD_KEY, None)
	new_password = state_data.pop(NEW_PASSWORD_KEY, None)
	state_data.clear()
	confirmation = message.text
	await state.clear()
	if (await migration_service.status()).status is not MigrationStatus.IDLE:
		await message.answer(
			"Дождитесь завершения миграции.",
			reply_markup=default_reply_keyboard(),
		)
		return
	if (
		not isinstance(current_password, str)
		or not isinstance(new_password, str)
		or confirmation != new_password
	):
		await message.answer(
			"Новые ключи не совпадают. Начните смену ключа снова.",
			reply_markup=default_reply_keyboard(),
		)
		return
	try:
		await setup_service.change_password(current_password, new_password)
	except InvalidPasswordError:
		await message.answer(
			"Текущий ключ указан неверно.",
			reply_markup=default_reply_keyboard(),
		)
		return
	await _render_settings(
		menu_service,
		menu_user_id,
		message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)
	await message.answer(
		"Ключ шифрования изменен.",
		reply_markup=default_reply_keyboard(),
	)


async def open_encryption_modes(
	callback: CallbackQuery,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	status = await migration_service.status()
	if status.status is not MigrationStatus.IDLE:
		await callback.answer(
			"Настройки шифрования недоступны во время миграции.",
			show_alert=True,
		)
		return
	await menu_service.render(
		menu_user_id,
		callback.message.chat.id,
		encryption_modes_view(status.target_policy),
	)
	await callback.answer()


async def open_custom_encryption(
	callback: CallbackQuery,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None or callback.data is None:
		await callback.answer()
		return
	if not await _require_idle(callback, migration_service):
		return
	try:
		mask = int(callback.data.rsplit(":", 1)[1])
		policy_from_mask(mask)
	except ValueError:
		await callback.answer("Неверные настройки.", show_alert=True)
		return
	await menu_service.render(
		menu_user_id,
		callback.message.chat.id,
		custom_encryption_view(mask),
	)
	await callback.answer()


async def confirm_disable_encryption(
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
	await menu_service.render(
		menu_user_id,
		callback.message.chat.id,
		disable_encryption_confirmation_view(),
	)
	await callback.answer()


async def apply_encryption_policy(
	callback: CallbackQuery,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None or callback.data is None:
		await callback.answer()
		return
	if not await _require_idle(callback, migration_service):
		return
	try:
		policy = _policy_from_callback(callback.data)
		await migration_service.start(policy)
	except (MigrationInProgressError, ValueError):
		await callback.answer("Неверные настройки.", show_alert=True)
		return
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)
	await callback.answer("Миграция запущена.")
	await _finish_migration(migration_service)
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)


async def resume_encryption_migration(
	callback: CallbackQuery,
	access_service: AccessService,
	migration_service: EncryptionMigrationService,
	topic_service: TopicService,
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
) -> None:
	if not await _authorize_owner_callback(callback, access_service):
		return
	if callback.message is None:
		await callback.answer()
		return
	await callback.answer("Миграция продолжена.")
	await _finish_migration(migration_service)
	await _render_settings(
		menu_service,
		menu_user_id,
		callback.message.chat.id,
		topic_service,
		migration_service,
		ActorRole.OWNER,
	)


async def _finish_migration(
	migration_service: EncryptionMigrationService,
) -> EncryptionMigrationRecord:
	try:
		while True:
			status = await migration_service.status()
			if status.status is MigrationStatus.IDLE:
				return status
			await migration_service.resume()
	except MigrationBatchError:
		return await migration_service.status()


async def _render_settings(
	menu_service: MenuService,
	menu_user_id: uuid.UUID,
	chat_id: int,
	topic_service: TopicService,
	migration_service: EncryptionMigrationService,
	role: ActorRole,
) -> None:
	await menu_service.render(
		menu_user_id,
		chat_id,
		settings_view(
			await topic_service.settings(),
			role,
			await migration_service.status(),
		),
	)


async def _require_idle(
	callback: CallbackQuery,
	migration_service: EncryptionMigrationService,
) -> bool:
	if (await migration_service.status()).status is MigrationStatus.IDLE:
		return True
	await callback.answer(
		"Настройки временно недоступны во время миграции.",
		show_alert=True,
	)
	return False


async def _authorize_settings_message(
	message: Message,
	access_service: AccessService,
) -> ActorRole | None:
	if message.from_user is None:
		return None
	try:
		return await access_service.require(
			message.from_user.id,
			AccessAction.MANAGE_SCHEDULER,
		)
	except AccessDeniedError:
		await message.answer("Доступ запрещен.")
		return None


async def _authorize_settings_callback(
	callback: CallbackQuery,
	access_service: AccessService,
) -> ActorRole | None:
	if callback.from_user is None:
		await callback.answer()
		return None
	try:
		return await access_service.require(
			callback.from_user.id,
			AccessAction.MANAGE_SCHEDULER,
		)
	except AccessDeniedError:
		await callback.answer("Доступ запрещен.", show_alert=True)
		return None


async def _authorize_owner_message(
	message: Message,
	access_service: AccessService,
	action: AccessAction = AccessAction.MANAGE_ENCRYPTION,
) -> bool:
	if message.from_user is None:
		return False
	try:
		await access_service.require(
			message.from_user.id,
			action,
		)
	except AccessDeniedError:
		await message.answer("Доступ запрещен.")
		return False
	return True


async def _authorize_owner_callback(
	callback: CallbackQuery,
	access_service: AccessService,
	action: AccessAction = AccessAction.MANAGE_ENCRYPTION,
) -> bool:
	if callback.from_user is None:
		await callback.answer()
		return False
	try:
		await access_service.require(
			callback.from_user.id,
			action,
		)
	except AccessDeniedError:
		await callback.answer("Доступ запрещен.", show_alert=True)
		return False
	return True


async def _delete_secret_message(message: Message) -> None:
	try:
		await message.delete()
	except TelegramAPIError:
		pass


def _policy_from_callback(data: str) -> EncryptionPolicy:
	if data == "encryption:apply-full":
		return EncryptionPolicy(EncryptionMode.FULL)
	if data == "encryption:apply-disabled":
		return EncryptionPolicy(EncryptionMode.DISABLED)
	if data.startswith("encryption:apply-custom:"):
		return policy_from_mask(int(data.rsplit(":", 1)[1]))
	raise ValueError("Encryption callback is invalid")


def build_settings_router() -> Router:
	router = Router(name="settings")
	router.message.register(open_settings_message, F.text == SETTINGS_TEXT)
	router.message.register(
		receive_global_interval,
		SettingsStates.global_interval,
	)
	router.message.register(
		receive_retry_schedule,
		SettingsStates.retry_schedule,
	)
	router.message.register(receive_api_token, SettingsStates.api_token)
	router.message.register(
		receive_current_password,
		SettingsStates.current_encryption_password,
	)
	router.message.register(
		receive_new_password,
		SettingsStates.encryption_password,
	)
	router.message.register(
		receive_new_password_confirmation,
		SettingsStates.encryption_password_confirmation,
	)
	router.callback_query.register(open_settings_callback, F.data == "menu:settings")
	router.callback_query.register(
		toggle_global_bump,
		F.data == "settings:global-toggle",
	)
	router.callback_query.register(
		begin_global_interval,
		F.data == "settings:global-interval",
	)
	router.callback_query.register(
		begin_retry_schedule,
		F.data == "settings:retries",
	)
	router.callback_query.register(
		toggle_notification,
		F.data.in_({"settings:notify-success", "settings:notify-errors"}),
	)
	router.callback_query.register(
		begin_api_token,
		F.data == "settings:api-token",
	)
	router.callback_query.register(
		begin_key_change,
		F.data == "settings:change-key",
	)
	router.callback_query.register(
		open_encryption_modes,
		F.data == "encryption:modes",
	)
	router.callback_query.register(
		open_custom_encryption,
		F.data.startswith("encryption:custom-mask:"),
	)
	router.callback_query.register(
		confirm_disable_encryption,
		F.data == "encryption:disable",
	)
	router.callback_query.register(
		apply_encryption_policy,
		F.data.in_({"encryption:apply-full", "encryption:apply-disabled"}),
	)
	router.callback_query.register(
		apply_encryption_policy,
		F.data.startswith("encryption:apply-custom:"),
	)
	router.callback_query.register(
		resume_encryption_migration,
		F.data == "encryption:resume",
	)
	return router


settings_router = build_settings_router()
