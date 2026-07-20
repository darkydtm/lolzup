import enum
import uuid
from typing import Protocol

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from lolzup.db.repositories import UserRecord
from lolzup.security.runtime import RuntimeVault


class ActorRole(enum.StrEnum):
	OWNER = "owner"
	ADMIN = "admin"
	DENIED = "denied"


class AccessAction(enum.StrEnum):
	INITIALIZE = "initialize"
	UNLOCK = "unlock"
	MANAGE_API_TOKEN = "manage_api_token"
	MANAGE_ENCRYPTION = "manage_encryption"
	MANAGE_ADMINS = "manage_admins"
	MANAGE_TOPICS = "manage_topics"
	MANAGE_SCHEDULER = "manage_scheduler"
	MANAGE_RETRIES = "manage_retries"
	MANAGE_NOTIFICATIONS = "manage_notifications"


class AccessDeniedError(PermissionError):
	pass


class UserLookup(Protocol):
	async def get_by_telegram_id(self, telegram_id: int) -> UserRecord | None: ...

	async def upsert(self, telegram_id: int, username: str | None) -> UserRecord: ...


class AdminLookup(Protocol):
	async def contains(self, user_id: uuid.UUID) -> bool: ...


OWNER_ACTIONS = frozenset(AccessAction)
ADMIN_ACTIONS = frozenset(
	{
		AccessAction.MANAGE_TOPICS,
		AccessAction.MANAGE_SCHEDULER,
		AccessAction.MANAGE_RETRIES,
		AccessAction.MANAGE_NOTIFICATIONS,
	}
)


class AccessService:
	def __init__(
		self,
		owner_id: int,
		users: UserLookup,
		admins: AdminLookup,
		vault: RuntimeVault,
	) -> None:
		if owner_id <= 0:
			raise ValueError("Owner ID must be positive")
		self._owner_id = owner_id
		self._users = users
		self._admins = admins
		self._vault = vault

	async def role_for(self, telegram_id: int) -> ActorRole:
		if telegram_id == self._owner_id:
			return ActorRole.OWNER
		if not self._vault.is_unlocked:
			return ActorRole.DENIED
		user = await self._users.get_by_telegram_id(telegram_id)
		if user is not None and await self._admins.contains(user.id):
			return ActorRole.ADMIN
		return ActorRole.DENIED

	async def record_user(self, telegram_id: int, username: str | None) -> UserRecord:
		if not self._vault.is_unlocked:
			raise AccessDeniedError("The bot must be unlocked before recording users")
		return await self._users.upsert(telegram_id, username)

	async def allows(self, telegram_id: int, action: AccessAction) -> bool:
		role = await self.role_for(telegram_id)
		if role is ActorRole.OWNER:
			return action in OWNER_ACTIONS
		if role is ActorRole.ADMIN:
			return action in ADMIN_ACTIONS
		return False

	async def require(self, telegram_id: int, action: AccessAction) -> ActorRole:
		role = await self.role_for(telegram_id)
		allowed = (
			action in OWNER_ACTIONS
			if role is ActorRole.OWNER
			else role is ActorRole.ADMIN and action in ADMIN_ACTIONS
		)
		if not allowed:
			raise AccessDeniedError("The actor is not allowed to perform this action")
		return role


class _RoleFilter(BaseFilter):
	def __init__(self, access: AccessService, action: AccessAction) -> None:
		self._access = access
		self._action = action

	async def __call__(self, event: Message | CallbackQuery) -> bool:
		user = event.from_user
		return user is not None and await self._access.allows(user.id, self._action)


class _UnlockedFilter(BaseFilter):
	def __init__(self, vault: RuntimeVault) -> None:
		self._vault = vault

	async def __call__(self, _: Message | CallbackQuery) -> bool:
		return self._vault.is_unlocked


def require_owner(access: AccessService, action: AccessAction) -> BaseFilter:
	if action in ADMIN_ACTIONS:
		raise ValueError("Owner filter requires an owner-only action")
	return _RoleFilter(access, action)


def require_admin(
	access: AccessService,
	action: AccessAction = AccessAction.MANAGE_TOPICS,
) -> BaseFilter:
	if action not in ADMIN_ACTIONS:
		raise ValueError("Admin filter requires an administrator action")
	return _RoleFilter(access, action)


def require_unlocked(vault: RuntimeVault) -> BaseFilter:
	return _UnlockedFilter(vault)
