import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from lolzup.db.repositories import SecretEnvelopeRecord, SecretRepository
from lolzup.security.crypto import (
	Argon2Parameters,
	CryptoEnvelope,
	decrypt,
	derive_kek,
	derive_password_verifier,
	encrypt,
	generate_data_key,
	unwrap_data_key,
	wrap_data_key,
)
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault

DATA_KEY_CONTEXT = b"secret_envelopes:1:data_key"
API_TOKEN_CONTEXT = b"secret_envelopes:1:api_token"
SALT_BYTES = 16

Clock = Callable[[], datetime]


@dataclass(slots=True)
class UnlockThrottleState:
	failed_attempts: int = 0
	next_attempt_at: datetime | None = None


class AlreadyInitializedError(RuntimeError):
	pass


class NotInitializedError(RuntimeError):
	pass


class InvalidPasswordError(RuntimeError):
	pass


class UnlockThrottledError(RuntimeError):
	def __init__(self, retry_at: datetime) -> None:
		super().__init__("Unlock attempts are temporarily throttled")
		self.retry_at = retry_at


class SetupService:
	def __init__(
		self,
		secrets_repository: SecretRepository,
		vault: RuntimeVault,
		*,
		argon_parameters: Argon2Parameters | None = None,
		clock: Clock | None = None,
		max_throttle_seconds: int = 60,
		throttle_state: UnlockThrottleState | None = None,
	) -> None:
		if max_throttle_seconds <= 0:
			raise ValueError("Maximum throttle delay must be positive")
		self._repository = secrets_repository
		self._vault = vault
		self._parameters = argon_parameters or Argon2Parameters()
		self._clock = clock or (lambda: datetime.now(UTC))
		self._max_throttle_seconds = max_throttle_seconds
		self._throttle = throttle_state or UnlockThrottleState()

	async def is_initialized(self) -> bool:
		return await self._repository.get() is not None

	async def initialize(self, password: str, api_token: str) -> None:
		if await self.is_initialized():
			raise AlreadyInitializedError
		if not api_token:
			raise ValueError("Forum API token must not be empty")

		salt = secrets.token_bytes(SALT_BYTES)
		kek = derive_kek(password, salt, self._parameters)
		data_key = generate_data_key()
		wrapped_key = wrap_data_key(kek, data_key, DATA_KEY_CONTEXT)
		encrypted_token = encrypt(data_key, api_token.encode(), API_TOKEN_CONTEXT)
		record = SecretEnvelopeRecord(
			salt=salt,
			argon_time_cost=self._parameters.time_cost,
			argon_memory_cost=self._parameters.memory_cost,
			argon_parallelism=self._parameters.parallelism,
			verifier=derive_password_verifier(kek),
			wrapped_data_key=wrapped_key.ciphertext,
			wrapped_data_key_nonce=wrapped_key.nonce,
			api_token_plain=None,
			api_token_ciphertext=encrypted_token.ciphertext,
			api_token_nonce=encrypted_token.nonce,
		)
		try:
			await self._repository.create(record)
		except ValueError as error:
			raise AlreadyInitializedError from error
		await self._vault.unlock(data_key)
		self._reset_throttle()

	async def unlock(self, password: str) -> None:
		now = self._as_utc(self._clock())
		if (
			self._throttle.next_attempt_at is not None
			and now < self._throttle.next_attempt_at
		):
			raise UnlockThrottledError(self._throttle.next_attempt_at)

		record = await self._repository.get()
		if record is None:
			raise NotInitializedError
		parameters = Argon2Parameters(
			time_cost=record.argon_time_cost,
			memory_cost=record.argon_memory_cost,
			parallelism=record.argon_parallelism,
		)
		kek = derive_kek(password, record.salt, parameters)
		if not hmac.compare_digest(
			derive_password_verifier(kek),
			record.verifier,
		):
			self._record_failure(now)
			raise InvalidPasswordError

		data_key = unwrap_data_key(
			kek,
			CryptoEnvelope(
				ciphertext=record.wrapped_data_key,
				nonce=record.wrapped_data_key_nonce,
			),
			DATA_KEY_CONTEXT,
		)
		await self._vault.unlock(data_key)
		self._reset_throttle()

	async def api_token(self) -> str:
		data_key = self._vault.require_key()
		record = await self._repository.get()
		if record is None:
			raise NotInitializedError
		if record.api_token_ciphertext is None:
			if record.api_token_plain is None:
				raise ValueError("Forum API token is missing")
			return record.api_token_plain
		if record.api_token_nonce is None:
			raise ValueError("Encrypted Forum API token is missing its nonce")
		plaintext = decrypt(
			data_key,
			CryptoEnvelope(
				ciphertext=record.api_token_ciphertext,
				nonce=record.api_token_nonce,
			),
			API_TOKEN_CONTEXT,
		)
		return plaintext.decode()

	async def change_password(
		self,
		current_password: str,
		new_password: str,
	) -> None:
		record = await self._repository.get()
		if record is None:
			raise NotInitializedError
		current_parameters = Argon2Parameters(
			time_cost=record.argon_time_cost,
			memory_cost=record.argon_memory_cost,
			parallelism=record.argon_parallelism,
		)
		current_kek = derive_kek(
			current_password,
			record.salt,
			current_parameters,
		)
		if not hmac.compare_digest(
			derive_password_verifier(current_kek),
			record.verifier,
		):
			raise InvalidPasswordError
		data_key = unwrap_data_key(
			current_kek,
			CryptoEnvelope(
				record.wrapped_data_key,
				record.wrapped_data_key_nonce,
			),
			DATA_KEY_CONTEXT,
		)
		if not hmac.compare_digest(data_key, self._vault.require_key()):
			raise InvalidPasswordError

		new_salt = secrets.token_bytes(SALT_BYTES)
		new_kek = derive_kek(new_password, new_salt, self._parameters)
		wrapped_key = wrap_data_key(new_kek, data_key, DATA_KEY_CONTEXT)
		await self._repository.save(
			replace(
				record,
				salt=new_salt,
				argon_time_cost=self._parameters.time_cost,
				argon_memory_cost=self._parameters.memory_cost,
				argon_parallelism=self._parameters.parallelism,
				verifier=derive_password_verifier(new_kek),
				wrapped_data_key=wrapped_key.ciphertext,
				wrapped_data_key_nonce=wrapped_key.nonce,
			)
		)

	async def replace_api_token(
		self,
		api_token: str,
		policy: EncryptionPolicy,
	) -> None:
		if not api_token:
			raise ValueError("Forum API token must not be empty")
		record = await self._repository.get()
		if record is None:
			raise NotInitializedError
		if policy.encrypts(DataCategory.SECRETS):
			stored = encrypt(
				self._vault.require_key(),
				api_token.encode(),
				API_TOKEN_CONTEXT,
			)
			updated = replace(
				record,
				api_token_plain=None,
				api_token_ciphertext=stored.ciphertext,
				api_token_nonce=stored.nonce,
			)
		else:
			updated = replace(
				record,
				api_token_plain=api_token,
				api_token_ciphertext=None,
				api_token_nonce=None,
			)
		await self._repository.save(updated)

	def _record_failure(self, now: datetime) -> None:
		self._throttle.failed_attempts += 1
		delay = min(
			2 ** (self._throttle.failed_attempts - 1),
			self._max_throttle_seconds,
		)
		self._throttle.next_attempt_at = now + timedelta(seconds=delay)

	def _reset_throttle(self) -> None:
		self._throttle.failed_attempts = 0
		self._throttle.next_attempt_at = None

	@staticmethod
	def _as_utc(value: datetime) -> datetime:
		if value.tzinfo is None or value.utcoffset() is None:
			raise ValueError("Clock must return a timezone-aware datetime")
		return value.astimezone(UTC)
