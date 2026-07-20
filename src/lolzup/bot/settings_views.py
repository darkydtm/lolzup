from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from lolzup.access import ActorRole
from lolzup.bot.menu import MenuView
from lolzup.db.migrations import EncryptionMigrationRecord
from lolzup.db.models import EncryptionMode, MigrationStatus
from lolzup.db.repositories import SettingsRecord
from lolzup.security.policy import DataCategory, EncryptionPolicy

CUSTOM_CATEGORIES = (
	DataCategory.TOPICS,
	DataCategory.SCHEDULING,
	DataCategory.HISTORY,
	DataCategory.TELEGRAM_IDENTITIES,
)

CATEGORY_LABELS = {
	DataCategory.TOPICS: "Темы",
	DataCategory.SCHEDULING: "Расписание",
	DataCategory.HISTORY: "История",
	DataCategory.TELEGRAM_IDENTITIES: "Telegram identities",
}


def settings_view(
	settings: SettingsRecord,
	role: ActorRole,
	migration: EncryptionMigrationRecord,
) -> MenuView:
	active_policy = (
		migration.target_policy
		if migration.status is MigrationStatus.IDLE
		else migration.source_policy
	)
	lines = [
		"Настройки",
		"",
		f"Автоподнятие: {_enabled(settings.global_bump_enabled)}",
		f"Глобальный интервал: {_duration(settings.global_interval_seconds)}",
		f"Повторы: {', '.join(_duration(value) for value in settings.retry_schedule)}",
		f"Уведомления об успехе: {_enabled(settings.notify_success)}",
		f"Уведомления об ошибках: {_enabled(settings.notify_errors)}",
	]
	if role is ActorRole.OWNER:
		lines.extend(
			[
				f"Шифрование: {_mode_text(active_policy.mode)}",
				f"Миграция: {_migration_text(migration.status)}",
			]
		)
	rows = []
	if role is ActorRole.OWNER:
		rows.append(
			[
				InlineKeyboardButton(
					text=(
						"Выключить автоподнятие"
						if settings.global_bump_enabled
						else "Включить автоподнятие"
					),
					callback_data="settings:global-toggle",
				)
			]
		)
	rows.extend(
		[
			[
				InlineKeyboardButton(
					text="Изменить глобальный интервал",
					callback_data="settings:global-interval",
				)
			],
			[
				InlineKeyboardButton(
					text="Настроить повторы",
					callback_data="settings:retries",
				)
			],
			[
				InlineKeyboardButton(
					text="Уведомления об успехе",
					callback_data="settings:notify-success",
				),
				InlineKeyboardButton(
					text="Уведомления об ошибках",
					callback_data="settings:notify-errors",
				),
			],
		]
	)
	if role is ActorRole.OWNER:
		rows.extend(
			[
				[
					InlineKeyboardButton(
						text="API token",
						callback_data="settings:api-token",
					),
					InlineKeyboardButton(
						text="Сменить ключ",
						callback_data="settings:change-key",
					),
				],
				[
					InlineKeyboardButton(
						text="Шифрование",
						callback_data="encryption:modes",
					),
					InlineKeyboardButton(
						text="Администраторы",
						callback_data="admins:list",
					),
				],
			]
		)
		if migration.status is MigrationStatus.FAILED:
			rows.append(
				[
					InlineKeyboardButton(
						text="Продолжить миграцию",
						callback_data="encryption:resume",
					)
				]
			)
	rows.append(
		[
			InlineKeyboardButton(
				text="Главное меню",
				callback_data="menu:main",
			)
		]
	)
	return MenuView(
		text="\n".join(lines),
		reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
	)


def encryption_modes_view(active: EncryptionPolicy) -> MenuView:
	return MenuView(
		text=(
			"Шифрование\n\n"
			f"Текущий режим: {_mode_text(active.mode)}\n\n"
			"Изменение режима запускает миграцию данных."
		),
		reply_markup=InlineKeyboardMarkup(
			inline_keyboard=[
				[
					InlineKeyboardButton(
						text="Полное",
						callback_data="encryption:apply-full",
					),
					InlineKeyboardButton(
						text="Свое",
						callback_data=(f"encryption:custom-mask:{policy_mask(active)}"),
					),
					InlineKeyboardButton(
						text="Отключить",
						callback_data="encryption:disable",
					),
				],
				[
					InlineKeyboardButton(
						text="К настройкам",
						callback_data="menu:settings",
					)
				],
			]
		),
	)


def custom_encryption_view(mask: int) -> MenuView:
	rows = []
	for index, category in enumerate(CUSTOM_CATEGORIES):
		enabled = bool(mask & (1 << index))
		next_mask = mask ^ (1 << index)
		rows.append(
			[
				InlineKeyboardButton(
					text=f"{'✓' if enabled else '○'} {CATEGORY_LABELS[category]}",
					callback_data=f"encryption:custom-mask:{next_mask}",
				)
			]
		)
	rows.extend(
		[
			[
				InlineKeyboardButton(
					text="Применить",
					callback_data=f"encryption:apply-custom:{mask}",
				)
			],
			[
				InlineKeyboardButton(
					text="Назад",
					callback_data="encryption:modes",
				)
			],
		]
	)
	return MenuView(
		text="Свое шифрование\n\nВыберите категории данных.",
		reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
	)


def disable_encryption_confirmation_view() -> MenuView:
	return MenuView(
		text=(
			"Отключить шифрование?\n\n"
			"Данные приложения будут храниться в PostgreSQL открытым текстом."
		),
		reply_markup=InlineKeyboardMarkup(
			inline_keyboard=[
				[
					InlineKeyboardButton(
						text="Отключить",
						callback_data="encryption:apply-disabled",
					),
					InlineKeyboardButton(
						text="Отмена",
						callback_data="encryption:modes",
					),
				]
			]
		),
	)


def policy_from_mask(mask: int) -> EncryptionPolicy:
	if mask < 0 or mask >= 1 << len(CUSTOM_CATEGORIES):
		raise ValueError("Custom encryption mask is invalid")
	categories = frozenset(
		category
		for index, category in enumerate(CUSTOM_CATEGORIES)
		if mask & (1 << index)
	)
	return EncryptionPolicy(EncryptionMode.CUSTOM, categories)


def policy_mask(policy: EncryptionPolicy) -> int:
	return sum(
		1 << index
		for index, category in enumerate(CUSTOM_CATEGORIES)
		if policy.encrypts(category)
	)


def _duration(seconds: int) -> str:
	if seconds % 86400 == 0:
		return f"{seconds // 86400} д."
	if seconds % 3600 == 0:
		return f"{seconds // 3600} ч."
	return f"{max(1, seconds // 60)} мин."


def _enabled(value: bool) -> str:
	return "включено" if value else "выключено"


def _mode_text(mode: EncryptionMode) -> str:
	return {
		EncryptionMode.FULL: "полное",
		EncryptionMode.CUSTOM: "свое",
		EncryptionMode.DISABLED: "отключено",
	}[mode]


def _migration_text(status: MigrationStatus) -> str:
	return {
		MigrationStatus.IDLE: "не выполняется",
		MigrationStatus.RUNNING: "выполняется",
		MigrationStatus.FAILED: "ошибка",
	}[status]
