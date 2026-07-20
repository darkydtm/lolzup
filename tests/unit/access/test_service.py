import asyncio
import uuid
from dataclasses import dataclass

import pytest

from lolzup.access import AccessAction, AccessDeniedError, AccessService, ActorRole
from lolzup.db.repositories import UserRecord
from lolzup.security.crypto import generate_data_key
from lolzup.security.runtime import RuntimeVault


@dataclass
class FakeUsers:
	users: dict[int, UserRecord]

	async def get_by_telegram_id(self, telegram_id: int) -> UserRecord | None:
		return self.users.get(telegram_id)

	async def upsert(self, telegram_id: int, username: str | None) -> UserRecord:
		user = self.users.get(telegram_id)
		if user is None:
			user = UserRecord(uuid.uuid4(), telegram_id, username)
			self.users[telegram_id] = user
		return user


@dataclass
class FakeAdmins:
	admin_ids: set[uuid.UUID]

	async def contains(self, user_id: uuid.UUID) -> bool:
		return user_id in self.admin_ids


def build_access() -> tuple[AccessService, RuntimeVault, FakeUsers, UserRecord]:
	admin = UserRecord(uuid.uuid4(), 200, "Admin")
	users = FakeUsers({200: admin})
	vault = RuntimeVault()
	access = AccessService(100, users, FakeAdmins({admin.id}), vault)
	return access, vault, users, admin


@pytest.mark.unit
def test_owner_is_resolved_while_locked() -> None:
	async def scenario() -> None:
		access, _, _, _ = build_access()

		assert await access.role_for(100) is ActorRole.OWNER
		assert await access.allows(100, AccessAction.UNLOCK)
		assert await access.allows(100, AccessAction.MANAGE_TOPICS)

	asyncio.run(scenario())


@pytest.mark.unit
def test_admin_has_only_non_privileged_actions_when_unlocked() -> None:
	async def scenario() -> None:
		access, vault, _, _ = build_access()
		await vault.unlock(generate_data_key())

		assert await access.role_for(200) is ActorRole.ADMIN
		for action in (
			AccessAction.MANAGE_TOPICS,
			AccessAction.MANAGE_SCHEDULER,
			AccessAction.MANAGE_RETRIES,
			AccessAction.MANAGE_NOTIFICATIONS,
		):
			assert await access.allows(200, action)
		for action in (
			AccessAction.INITIALIZE,
			AccessAction.UNLOCK,
			AccessAction.MANAGE_API_TOKEN,
			AccessAction.MANAGE_ENCRYPTION,
			AccessAction.MANAGE_ADMINS,
		):
			assert not await access.allows(200, action)

	asyncio.run(scenario())


@pytest.mark.unit
def test_admin_and_unknown_user_are_denied_while_locked() -> None:
	async def scenario() -> None:
		access, _, _, _ = build_access()

		assert await access.role_for(200) is ActorRole.DENIED
		assert await access.role_for(300) is ActorRole.DENIED
		with pytest.raises(AccessDeniedError):
			await access.require(200, AccessAction.MANAGE_TOPICS)

	asyncio.run(scenario())


@pytest.mark.unit
def test_interacting_user_is_recorded_case_preserving() -> None:
	async def scenario() -> None:
		access, vault, users, _ = build_access()
		await vault.unlock(generate_data_key())

		user = await access.record_user(300, "DisplayName")

		assert user.username == "DisplayName"
		assert users.users[300] == user

	asyncio.run(scenario())
