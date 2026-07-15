import asyncio
import secrets

from lolzup.security.crypto import AES_KEY_BYTES


class BotLockedError(RuntimeError):
	pass


class RuntimeVault:
	def __init__(self) -> None:
		self._data_key: bytes | None = None
		self._lock = asyncio.Lock()

	@property
	def is_unlocked(self) -> bool:
		return self._data_key is not None

	async def unlock(self, data_key: bytes) -> None:
		if len(data_key) != AES_KEY_BYTES:
			raise ValueError("Data key must contain 32 bytes")
		async with self._lock:
			self._data_key = bytes(data_key)

	async def lock(self) -> None:
		async with self._lock:
			if self._data_key is not None:
				self._data_key = secrets.token_bytes(len(self._data_key))
				self._data_key = None

	def require_key(self) -> bytes:
		if self._data_key is None:
			raise BotLockedError
		return self._data_key
