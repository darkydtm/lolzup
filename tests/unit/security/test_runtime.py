import asyncio

import pytest

from lolzup.security.crypto import generate_data_key
from lolzup.security.runtime import BotLockedError, RuntimeVault


@pytest.mark.unit
def test_runtime_vault_requires_unlock() -> None:
	vault = RuntimeVault()

	with pytest.raises(BotLockedError):
		vault.require_key()


@pytest.mark.unit
def test_runtime_vault_unlocks_and_locks() -> None:
	async def scenario() -> None:
		vault = RuntimeVault()
		data_key = generate_data_key()

		await vault.unlock(data_key)
		assert vault.is_unlocked
		assert vault.require_key() == data_key

		await vault.lock()
		assert not vault.is_unlocked
		with pytest.raises(BotLockedError):
			vault.require_key()

	asyncio.run(scenario())
