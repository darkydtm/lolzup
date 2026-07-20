import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lolzup.db.models import (
	Administrator,
	AppSettings,
	AttemptOutcome,
	BumpAttempt,
	KnownUser,
	MenuReference,
	SecretEnvelope,
	Topic,
)
from lolzup.security.crypto import (
	CryptoEnvelope,
	blind_index,
	decrypt,
	derive_index_key,
	encrypt,
)
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault

Value = TypeVar("Value", str, int, bool, datetime, list[int])


class DuplicateTopicError(RuntimeError):
	pass


@dataclass(frozen=True, slots=True)
class StoredValue:
	plain: Any | None
	ciphertext: bytes | None
	nonce: bytes | None


@dataclass(frozen=True, slots=True)
class UserRecord:
	id: uuid.UUID
	telegram_id: int
	username: str | None


@dataclass(frozen=True, slots=True)
class TopicRecord:
	id: uuid.UUID
	thread_id: int
	title: str
	auto_bump_enabled: bool
	custom_interval_enabled: bool
	custom_interval_seconds: int | None
	last_success_at: datetime | None
	next_bump_at: datetime | None
	last_error: str | None


@dataclass(frozen=True, slots=True)
class SettingsRecord:
	global_bump_enabled: bool
	global_interval_seconds: int
	retry_schedule: list[int]
	notify_success: bool
	notify_errors: bool
	api_paused: bool


@dataclass(frozen=True, slots=True)
class SecretEnvelopeRecord:
	salt: bytes
	argon_time_cost: int
	argon_memory_cost: int
	argon_parallelism: int
	verifier: bytes
	wrapped_data_key: bytes
	wrapped_data_key_nonce: bytes
	api_token_plain: str | None
	api_token_ciphertext: bytes | None
	api_token_nonce: bytes | None


class EncryptedFieldCodec:
	def __init__(self, policy: EncryptionPolicy, vault: RuntimeVault) -> None:
		self._policy = policy
		self._vault = vault

	def encode(
		self,
		category: DataCategory,
		context: str,
		value: Value | None,
	) -> StoredValue:
		if value is None:
			return StoredValue(None, None, None)
		if not self._policy.encrypts(category):
			return StoredValue(value, None, None)

		payload = json.dumps(
			self._serialize(value), separators=(",", ":"), ensure_ascii=True
		).encode()
		envelope = encrypt(self._vault.require_key(), payload, context.encode())
		return StoredValue(None, envelope.ciphertext, envelope.nonce)

	def decode(
		self,
		category: DataCategory,
		context: str,
		plain: Value | None,
		ciphertext: bytes | None,
		nonce: bytes | None,
	) -> Value | None:
		if ciphertext is None:
			return plain
		if nonce is None:
			raise ValueError("Encrypted value is missing its nonce")

		payload = decrypt(
			self._vault.require_key(),
			CryptoEnvelope(ciphertext=ciphertext, nonce=nonce),
			context.encode(),
		)
		return cast(Value, self._deserialize(json.loads(payload)))

	def index(self, value: str) -> bytes:
		index_key = derive_index_key(self._vault.require_key())
		return blind_index(index_key, value)

	@staticmethod
	def _serialize(value: Value) -> Any:
		if isinstance(value, datetime):
			return {"type": "datetime", "value": value.astimezone(UTC).isoformat()}
		return {"type": "value", "value": value}

	@staticmethod
	def _deserialize(payload: dict[str, Any]) -> Any:
		if payload["type"] == "datetime":
			return datetime.fromisoformat(payload["value"])
		return payload["value"]


class SecretRepository:
	def __init__(self, session: AsyncSession) -> None:
		self._session = session

	async def get(self) -> SecretEnvelopeRecord | None:
		model = await self._session.get(SecretEnvelope, 1)
		if model is None:
			return None
		return SecretEnvelopeRecord(
			salt=model.salt,
			argon_time_cost=model.argon_time_cost,
			argon_memory_cost=model.argon_memory_cost,
			argon_parallelism=model.argon_parallelism,
			verifier=model.verifier,
			wrapped_data_key=model.wrapped_data_key,
			wrapped_data_key_nonce=model.wrapped_data_key_nonce,
			api_token_plain=model.api_token_plain,
			api_token_ciphertext=model.api_token_ciphertext,
			api_token_nonce=model.api_token_nonce,
		)

	async def create(self, record: SecretEnvelopeRecord) -> None:
		if await self.get() is not None:
			raise ValueError("Secret envelope is already initialized")
		self._session.add(
			SecretEnvelope(
				id=1,
				salt=record.salt,
				argon_time_cost=record.argon_time_cost,
				argon_memory_cost=record.argon_memory_cost,
				argon_parallelism=record.argon_parallelism,
				verifier=record.verifier,
				wrapped_data_key=record.wrapped_data_key,
				wrapped_data_key_nonce=record.wrapped_data_key_nonce,
				api_token_plain=record.api_token_plain,
				api_token_ciphertext=record.api_token_ciphertext,
				api_token_nonce=record.api_token_nonce,
			)
		)
		await self._session.flush()


class SettingsRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._codec = codec

	async def get_or_create(self) -> SettingsRecord:
		model = await self._session.get(AppSettings, 1)
		if model is None:
			model = AppSettings(id=1)
			self._write(
				model,
				SettingsRecord(True, 72 * 3600, [60, 300, 900], False, True, False),
			)
			self._session.add(model)
			await self._session.flush()
		return self._read(model)

	async def save(self, record: SettingsRecord) -> None:
		model = await self._session.get(AppSettings, 1)
		if model is None:
			model = AppSettings(id=1)
			self._session.add(model)
		self._write(model, record)
		await self._session.flush()

	def _write(self, model: AppSettings, record: SettingsRecord) -> None:
		self._assign(model, "global_bump_enabled", record.global_bump_enabled)
		self._assign(model, "global_interval", record.global_interval_seconds)
		self._assign(model, "retry_schedule", record.retry_schedule)
		self._assign(model, "notify_success", record.notify_success)
		self._assign(model, "notify_errors", record.notify_errors)
		model.api_paused = record.api_paused

	def _read(self, model: AppSettings) -> SettingsRecord:
		return SettingsRecord(
			global_bump_enabled=cast(bool, self._decode(model, "global_bump_enabled")),
			global_interval_seconds=cast(int, self._decode(model, "global_interval")),
			retry_schedule=cast(list[int], self._decode(model, "retry_schedule")),
			notify_success=cast(bool, self._decode(model, "notify_success")),
			notify_errors=cast(bool, self._decode(model, "notify_errors")),
			api_paused=model.api_paused,
		)

	def _assign(self, model: AppSettings, field: str, value: Value) -> None:
		stored = self._codec.encode(
			DataCategory.SCHEDULING, f"app_settings:1:{field}", value
		)
		setattr(model, f"{field}_plain", stored.plain)
		setattr(model, f"{field}_ciphertext", stored.ciphertext)
		setattr(model, f"{field}_nonce", stored.nonce)

	def _decode(self, model: AppSettings, field: str) -> Value | None:
		return self._codec.decode(
			DataCategory.SCHEDULING,
			f"app_settings:1:{field}",
			getattr(model, f"{field}_plain"),
			getattr(model, f"{field}_ciphertext"),
			getattr(model, f"{field}_nonce"),
		)


class UserRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._codec = codec

	async def upsert(self, telegram_id: int, username: str | None) -> UserRecord:
		index = self._codec.index(str(telegram_id))
		model = await self._session.scalar(
			select(KnownUser).where(KnownUser.telegram_id_index == index)
		)
		if model is None:
			model = KnownUser(id=uuid.uuid4(), telegram_id_index=index)
			self._session.add(model)
		self._write_identity(model, telegram_id, username)
		await self._session.flush()
		return self._read(model)

	async def get_by_telegram_id(self, telegram_id: int) -> UserRecord | None:
		model = await self._session.scalar(
			select(KnownUser).where(
				KnownUser.telegram_id_index == self._codec.index(str(telegram_id))
			)
		)
		return None if model is None else self._read(model)

	async def get_by_username(self, username: str) -> UserRecord | None:
		model = await self._session.scalar(
			select(KnownUser).where(
				KnownUser.username_index == self._codec.index(username.casefold())
			)
		)
		return None if model is None else self._read(model)

	def _write_identity(
		self, model: KnownUser, telegram_id: int, username: str | None
	) -> None:
		self._assign(model, "telegram_id", telegram_id)
		self._assign(model, "username", username)
		model.username_index = (
			None if username is None else self._codec.index(username.casefold())
		)

	def _assign(self, model: KnownUser, field: str, value: Value | None) -> None:
		stored = self._codec.encode(
			DataCategory.TELEGRAM_IDENTITIES,
			f"known_users:{model.id}:{field}",
			value,
		)
		setattr(model, f"{field}_plain", stored.plain)
		setattr(model, f"{field}_ciphertext", stored.ciphertext)
		setattr(model, f"{field}_nonce", stored.nonce)

	def _read(self, model: KnownUser) -> UserRecord:
		telegram_id = self._codec.decode(
			DataCategory.TELEGRAM_IDENTITIES,
			f"known_users:{model.id}:telegram_id",
			model.telegram_id_plain,
			model.telegram_id_ciphertext,
			model.telegram_id_nonce,
		)
		username = self._codec.decode(
			DataCategory.TELEGRAM_IDENTITIES,
			f"known_users:{model.id}:username",
			model.username_plain,
			model.username_ciphertext,
			model.username_nonce,
		)
		return UserRecord(model.id, cast(int, telegram_id), username)


class AdminRepository:
	def __init__(self, session: AsyncSession) -> None:
		self._session = session

	async def add(self, user_id: uuid.UUID) -> None:
		self._session.add(Administrator(user_id=user_id))
		await self._session.flush()

	async def remove(self, user_id: uuid.UUID) -> None:
		await self._session.execute(
			delete(Administrator).where(Administrator.user_id == user_id)
		)

	async def contains(self, user_id: uuid.UUID) -> bool:
		return (
			await self._session.scalar(
				select(Administrator.id).where(Administrator.user_id == user_id)
			)
			is not None
		)


class TopicRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._codec = codec

	async def add(
		self,
		thread_id: int,
		title: str,
		next_bump_at: datetime | None = None,
	) -> TopicRecord:
		model = Topic(
			id=uuid.uuid4(), thread_id_index=self._codec.index(str(thread_id))
		)
		self._assign(model, DataCategory.TOPICS, "thread_id", thread_id)
		self._assign(model, DataCategory.TOPICS, "title", title)
		self._assign(model, DataCategory.SCHEDULING, "auto_bump_enabled", True)
		self._assign(model, DataCategory.SCHEDULING, "custom_interval_enabled", False)
		self._assign(model, DataCategory.SCHEDULING, "custom_interval", None)
		self._assign(model, DataCategory.HISTORY, "last_success_at", None)
		self._assign(model, DataCategory.SCHEDULING, "next_bump_at", next_bump_at)
		self._assign(model, DataCategory.HISTORY, "last_error", None)
		try:
			async with self._session.begin_nested():
				self._session.add(model)
				await self._session.flush()
		except IntegrityError as error:
			raise DuplicateTopicError from error
		return self._read(model)

	async def get(self, topic_id: uuid.UUID) -> TopicRecord | None:
		model = await self._session.get(Topic, topic_id)
		return None if model is None else self._read(model)

	async def list(self) -> list[TopicRecord]:
		models = await self._session.scalars(select(Topic).order_by(Topic.created_at))
		return [self._read(model) for model in models]

	async def remove(self, topic_id: uuid.UUID) -> None:
		await self._session.execute(delete(Topic).where(Topic.id == topic_id))

	async def save(self, record: TopicRecord) -> None:
		model = await self._session.get(Topic, record.id)
		if model is None:
			raise KeyError(record.id)
		self._assign(
			model,
			DataCategory.SCHEDULING,
			"auto_bump_enabled",
			record.auto_bump_enabled,
		)
		self._assign(
			model,
			DataCategory.SCHEDULING,
			"custom_interval_enabled",
			record.custom_interval_enabled,
		)
		self._assign(
			model,
			DataCategory.SCHEDULING,
			"custom_interval",
			record.custom_interval_seconds,
		)
		self._assign(
			model,
			DataCategory.HISTORY,
			"last_success_at",
			record.last_success_at,
		)
		self._assign(
			model,
			DataCategory.SCHEDULING,
			"next_bump_at",
			record.next_bump_at,
		)
		self._assign(model, DataCategory.HISTORY, "last_error", record.last_error)
		await self._session.flush()

	def _assign(
		self,
		model: Topic,
		category: DataCategory,
		field: str,
		value: Value | None,
	) -> None:
		stored = self._codec.encode(category, f"topics:{model.id}:{field}", value)
		setattr(model, f"{field}_plain", stored.plain)
		setattr(model, f"{field}_ciphertext", stored.ciphertext)
		setattr(model, f"{field}_nonce", stored.nonce)

	def _decode(self, model: Topic, category: DataCategory, field: str) -> Value | None:
		return self._codec.decode(
			category,
			f"topics:{model.id}:{field}",
			getattr(model, f"{field}_plain"),
			getattr(model, f"{field}_ciphertext"),
			getattr(model, f"{field}_nonce"),
		)

	def _read(self, model: Topic) -> TopicRecord:
		return TopicRecord(
			id=model.id,
			thread_id=cast(int, self._decode(model, DataCategory.TOPICS, "thread_id")),
			title=cast(str, self._decode(model, DataCategory.TOPICS, "title")),
			auto_bump_enabled=cast(
				bool,
				self._decode(model, DataCategory.SCHEDULING, "auto_bump_enabled"),
			),
			custom_interval_enabled=cast(
				bool,
				self._decode(model, DataCategory.SCHEDULING, "custom_interval_enabled"),
			),
			custom_interval_seconds=cast(
				int | None,
				self._decode(model, DataCategory.SCHEDULING, "custom_interval"),
			),
			last_success_at=cast(
				datetime | None,
				self._decode(model, DataCategory.HISTORY, "last_success_at"),
			),
			next_bump_at=cast(
				datetime | None,
				self._decode(model, DataCategory.SCHEDULING, "next_bump_at"),
			),
			last_error=cast(
				str | None, self._decode(model, DataCategory.HISTORY, "last_error")
			),
		)


class AttemptRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._codec = codec

	async def add(
		self,
		topic_id: uuid.UUID,
		job_id: str,
		outcome: AttemptOutcome,
		is_manual: bool,
		retry_at: datetime | None = None,
		error: str | None = None,
	) -> uuid.UUID:
		model = BumpAttempt(
			id=uuid.uuid4(),
			topic_id=topic_id,
			job_id=job_id,
			outcome=outcome,
			is_manual=is_manual,
		)
		self._assign(model, "retry_at", retry_at)
		self._assign(model, "error", error)
		self._session.add(model)
		await self._session.flush()
		return model.id

	def _assign(self, model: BumpAttempt, field: str, value: Value | None) -> None:
		stored = self._codec.encode(
			DataCategory.HISTORY, f"bump_attempts:{model.id}:{field}", value
		)
		setattr(model, f"{field}_plain", stored.plain)
		setattr(model, f"{field}_ciphertext", stored.ciphertext)
		setattr(model, f"{field}_nonce", stored.nonce)


class MenuRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._codec = codec

	async def get(self, user_id: uuid.UUID, chat_id: int) -> int | None:
		model = await self._session.scalar(
			select(MenuReference).where(
				MenuReference.user_id == user_id,
				MenuReference.chat_id_index == self._codec.index(str(chat_id)),
			)
		)
		if model is None:
			return None
		value = self._codec.decode(
			DataCategory.TELEGRAM_IDENTITIES,
			f"menu_references:{model.id}:message_id",
			model.message_id_plain,
			model.message_id_ciphertext,
			model.message_id_nonce,
		)
		return cast(int, value)

	async def save(self, user_id: uuid.UUID, chat_id: int, message_id: int) -> None:
		index = self._codec.index(str(chat_id))
		model = await self._session.scalar(
			select(MenuReference).where(
				MenuReference.user_id == user_id,
				MenuReference.chat_id_index == index,
			)
		)
		if model is None:
			model = MenuReference(id=uuid.uuid4(), user_id=user_id, chat_id_index=index)
			self._session.add(model)
			self._assign(model, "chat_id", chat_id)
		self._assign(model, "message_id", message_id)
		await self._session.flush()

	def _assign(self, model: MenuReference, field: str, value: int) -> None:
		stored = self._codec.encode(
			DataCategory.TELEGRAM_IDENTITIES,
			f"menu_references:{model.id}:{field}",
			value,
		)
		setattr(model, f"{field}_plain", stored.plain)
		setattr(model, f"{field}_ciphertext", stored.ciphertext)
		setattr(model, f"{field}_nonce", stored.nonce)
