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

	async def get(self, user_id: uuid.UUID) -> UserRecord | None:
		return next(
			(user for user in self.users.values() if user.id == user_id),
			None,
		)

	async def get_by_telegram_id(self, telegram_id: int) -> UserRecord | None:
		return self.users.get(telegram_id)

	async def get_by_username(self, username: str) -> UserRecord | None:
		return next(
			(
				user
				for user in self.users.values()
				if user.username is not None
				and user.username.casefold() == username.casefold()
			),
			None,
		)

	async def upsert(self, telegram_id: int, username: str | None) -> UserRecord:
		user = self.users.get(telegram_id)
		if user is None:
			user = UserRecord(uuid.uuid4(), telegram_id, username)
			self.users[telegram_id] = user
		return user


@dataclass
class FakeAdmins:
	admin_ids: set[uuid.UUID]

	async def add(self, user_id: uuid.UUID) -> None:
		self.admin_ids.add(user_id)

	async def remove(self, user_id: uuid.UUID) -> None:
		self.admin_ids.discard(user_id)

	async def contains(self, user_id: uuid.UUID) -> bool:
		return user_id in self.admin_ids

	async def list_user_ids(self) -> list[uuid.UUID]:
		return list(self.admin_ids)


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
			AccessAction.MANAGE_GLOBAL_BUMP,
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


@pytest.mark.unit
def test_owner_manages_administrators_by_id_and_known_username() -> None:
	async def scenario() -> None:
		access, vault, users, existing_admin = build_access()
		await vault.unlock(generate_data_key())

		numeric = await access.add_administrator(100, "300")
		known = UserRecord(uuid.uuid4(), 400, "KnownUser")
		users.users[known.telegram_id] = known
		username = await access.add_administrator(100, "@knownuser")

		assert numeric.telegram_id == 300
		assert username == known
		assert set(await access.list_administrators(100)) == {
			existing_admin,
			numeric,
			known,
		}

		await access.remove_administrator(100, numeric.id)
		assert numeric not in await access.list_administrators(100)

	asyncio.run(scenario())


@pytest.mark.unit
def test_admin_cannot_manage_other_administrators() -> None:
	async def scenario() -> None:
		access, vault, _, _ = build_access()
		await vault.unlock(generate_data_key())

		with pytest.raises(AccessDeniedError):
			await access.add_administrator(200, "300")

	asyncio.run(scenario())
