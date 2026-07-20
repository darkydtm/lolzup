import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from lolzup.db.models import AttemptOutcome
from lolzup.db.repositories import SettingsRecord, TopicRecord
from lolzup.forum.types import BumpJob, BumpOutcome, BumpResult, ThreadInfo
from lolzup.topics.parser import parse_topic_reference
from lolzup.topics.schedule import effective_interval, next_bump_at

Clock = Callable[[], datetime]
JobIdFactory = Callable[[], str]


class TopicNotFoundError(LookupError):
	pass


class TopicStore(Protocol):
	async def add(
		self,
		thread_id: int,
		title: str,
		next_bump_at: datetime | None = None,
	) -> TopicRecord: ...

	async def get(self, topic_id: uuid.UUID) -> TopicRecord | None: ...

	async def list(self) -> list[TopicRecord]: ...

	async def remove(self, topic_id: uuid.UUID) -> None: ...

	async def save(self, record: TopicRecord) -> None: ...


class SettingsStore(Protocol):
	async def get_or_create(self) -> SettingsRecord: ...

	async def save(self, record: SettingsRecord) -> None: ...


class AttemptStore(Protocol):
	async def add(
		self,
		topic_id: uuid.UUID,
		job_id: str,
		outcome: AttemptOutcome,
		is_manual: bool,
		retry_at: datetime | None = None,
		error: str | None = None,
	) -> uuid.UUID: ...


class ForumClient(Protocol):
	async def get_thread(self, thread_id: int) -> ThreadInfo: ...

	async def bump_batch(self, jobs: list[BumpJob]) -> list[BumpResult]: ...


class TopicService:
	def __init__(
		self,
		topics: TopicStore,
		settings: SettingsStore,
		attempts: AttemptStore,
		forum: ForumClient,
		*,
		clock: Clock | None = None,
		job_id_factory: JobIdFactory | None = None,
	) -> None:
		self._topics = topics
		self._settings = settings
		self._attempts = attempts
		self._forum = forum
		self._clock = clock or (lambda: datetime.now(UTC))
		self._job_id_factory = job_id_factory or (lambda: uuid.uuid4().hex)

	async def add(self, reference: str) -> TopicRecord:
		thread_id = parse_topic_reference(reference)
		thread = await self._forum.get_thread(thread_id)
		settings = await self._settings.get_or_create()
		interval = effective_interval(
			settings.global_interval_seconds,
			custom_enabled=False,
			custom_seconds=None,
		)
		return await self._topics.add(
			thread.thread_id,
			thread.title,
			next_bump_at=self._now() + interval,
		)

	async def get(self, topic_id: uuid.UUID) -> TopicRecord:
		return await self._require_topic(topic_id)

	async def list(self) -> list[TopicRecord]:
		return await self._topics.list()

	async def settings(self) -> SettingsRecord:
		return await self._settings.get_or_create()

	async def remove(self, topic_id: uuid.UUID) -> None:
		await self._require_topic(topic_id)
		await self._topics.remove(topic_id)

	async def set_enabled(self, topic_id: uuid.UUID, enabled: bool) -> TopicRecord:
		topic = await self._require_topic(topic_id)
		next_at = topic.next_bump_at
		if enabled:
			settings = await self._settings.get_or_create()
			interval = effective_interval(
				settings.global_interval_seconds,
				topic.custom_interval_enabled,
				topic.custom_interval_seconds,
			)
			next_at = self._now() + interval
		updated = replace(
			topic,
			auto_bump_enabled=enabled,
			next_bump_at=next_at,
		)
		await self._topics.save(updated)
		return updated

	async def set_custom_interval(
		self,
		topic_id: uuid.UUID,
		enabled: bool,
		seconds: int | None = None,
	) -> TopicRecord:
		topic = await self._require_topic(topic_id)
		custom_seconds = (
			seconds if seconds is not None else topic.custom_interval_seconds
		)
		settings = await self._settings.get_or_create()
		interval = effective_interval(
			settings.global_interval_seconds,
			enabled,
			custom_seconds,
		)
		updated = replace(
			topic,
			custom_interval_enabled=enabled,
			custom_interval_seconds=custom_seconds,
			next_bump_at=next_bump_at(
				topic.last_success_at,
				self._now(),
				interval,
			),
		)
		await self._topics.save(updated)
		return updated

	async def set_global_interval(self, seconds: int) -> SettingsRecord:
		interval = effective_interval(seconds, False, None)
		settings = await self._settings.get_or_create()
		updated_settings = replace(settings, global_interval_seconds=seconds)
		await self._settings.save(updated_settings)

		now = self._now()
		for topic in await self._topics.list():
			if topic.custom_interval_enabled:
				continue
			updated_topic = replace(
				topic,
				next_bump_at=next_bump_at(topic.last_success_at, now, interval),
			)
			await self._topics.save(updated_topic)
		return updated_settings

	async def set_global_enabled(self, enabled: bool) -> SettingsRecord:
		settings = await self._settings.get_or_create()
		updated = replace(settings, global_bump_enabled=enabled)
		await self._settings.save(updated)
		return updated

	async def manual_bump(self, topic_id: uuid.UUID) -> BumpResult:
		topic = await self._require_topic(topic_id)
		job_id = f"manual-{topic.id.hex[:16]}-{self._job_id_factory()[:16]}"
		results = await self._forum.bump_batch([BumpJob(job_id, topic.thread_id)])
		if len(results) != 1:
			raise RuntimeError("Forum API returned an invalid manual bump result count")
		result = results[0]
		await self._attempts.add(
			topic.id,
			result.job_id,
			self._attempt_outcome(result.outcome),
			True,
			retry_at=result.retry_at,
			error=result.error,
		)

		if result.outcome is BumpOutcome.SUCCESS:
			now = self._now()
			settings = await self._settings.get_or_create()
			interval = effective_interval(
				settings.global_interval_seconds,
				topic.custom_interval_enabled,
				topic.custom_interval_seconds,
			)
			updated = replace(
				topic,
				last_success_at=now,
				next_bump_at=now + interval,
				last_error=None,
			)
		else:
			updated = replace(topic, last_error=result.error)
		await self._topics.save(updated)
		return result

	async def _require_topic(self, topic_id: uuid.UUID) -> TopicRecord:
		topic = await self._topics.get(topic_id)
		if topic is None:
			raise TopicNotFoundError(topic_id)
		return topic

	def _now(self) -> datetime:
		value = self._clock()
		if value.tzinfo is None or value.utcoffset() is None:
			raise ValueError("Clock must return a timezone-aware datetime")
		return value.astimezone(UTC)

	@staticmethod
	def _attempt_outcome(outcome: BumpOutcome) -> AttemptOutcome:
		if outcome is BumpOutcome.SUCCESS:
			return AttemptOutcome.SUCCESS
		if outcome is BumpOutcome.RETRY:
			return AttemptOutcome.RETRY
		return AttemptOutcome.ERROR
