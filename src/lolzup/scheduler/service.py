import enum
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lolzup.db.models import AttemptOutcome
from lolzup.db.repositories import (
	AttemptRepository,
	EncryptedFieldCodec,
	SettingsRecord,
	SettingsRepository,
	TopicRecord,
	TopicRepository,
)
from lolzup.forum.types import BumpJob, BumpOutcome, BumpResult
from lolzup.scheduler.repository import SchedulerRepository
from lolzup.security.runtime import RuntimeVault
from lolzup.topics.schedule import effective_interval
from lolzup.topics.service import ForumClient

MAX_BATCH_SIZE = 10
DEFAULT_LEASE_SECONDS = 300
DEFAULT_CLAIM_LIMIT = 100

JobIdFactory = Callable[[], str]
NotificationSink = Callable[[str], Awaitable[None]]


class CycleStatus(enum.StrEnum):
	RAN = "ran"
	LOCKED = "locked"
	DISABLED = "disabled"
	API_PAUSED = "api_paused"
	MIGRATING = "migrating"


@dataclass(frozen=True, slots=True)
class CycleReport:
	status: CycleStatus
	claimed: int = 0
	batches: int = 0
	succeeded: int = 0
	retried: int = 0
	failed: int = 0


class SchedulerService:
	def __init__(
		self,
		sessions: async_sessionmaker[AsyncSession],
		codec: EncryptedFieldCodec,
		forum: ForumClient,
		vault: RuntimeVault,
		*,
		lease_seconds: int = DEFAULT_LEASE_SECONDS,
		claim_limit: int = DEFAULT_CLAIM_LIMIT,
		job_id_factory: JobIdFactory | None = None,
		notifier: NotificationSink | None = None,
	) -> None:
		if lease_seconds <= 0:
			raise ValueError("Lease duration must be positive")
		if claim_limit <= 0:
			raise ValueError("Claim limit must be positive")
		self._sessions = sessions
		self._codec = codec
		self._forum = forum
		self._vault = vault
		self._lease_seconds = lease_seconds
		self._claim_limit = claim_limit
		self._job_id_factory = job_id_factory or (lambda: uuid.uuid4().hex)
		self._notifier = notifier

	async def run_cycle(self, now: datetime) -> CycleReport:
		current = self._as_utc(now)
		if not self._vault.is_unlocked:
			return CycleReport(CycleStatus.LOCKED)

		preflight, topics = await self._claim(current)
		if preflight is not CycleStatus.RAN:
			return CycleReport(preflight)
		if not topics:
			return CycleReport(CycleStatus.RAN)

		batches = 0
		succeeded = 0
		retried = 0
		failed = 0
		topic_batches = self._chunks(topics, MAX_BATCH_SIZE)
		for batch_index, batch_topics in enumerate(topic_batches):
			batches += 1
			jobs = [
				BumpJob(self._job_id(topic), topic.thread_id) for topic in batch_topics
			]
			results = await self._forum.bump_batch(jobs)
			results_by_id = {result.job_id: result for result in results}
			account_paused = False
			for topic, job in zip(batch_topics, jobs, strict=True):
				result = results_by_id.get(
					job.job_id,
					BumpResult(
						job.job_id,
						job.thread_id,
						BumpOutcome.RETRY,
						error="Forum API omitted the batch job result",
					),
				)
				persisted = await self._persist_result(topic.id, result, current)
				if persisted is not None:
					updated, settings = persisted
					await self._notify(updated, result, settings)
				if result.outcome is BumpOutcome.SUCCESS:
					succeeded += 1
				elif result.outcome is BumpOutcome.RETRY:
					retried += 1
				else:
					failed += 1
				if result.outcome in {
					BumpOutcome.UNAUTHORIZED,
					BumpOutcome.FORBIDDEN,
				}:
					account_paused = True
			if account_paused:
				remaining = [
					topic.id
					for pending_batch in topic_batches[batch_index + 1 :]
					for topic in pending_batch
				]
				await self._release_leases(remaining)
				break
		return CycleReport(
			CycleStatus.RAN,
			claimed=len(topics),
			batches=batches,
			succeeded=succeeded,
			retried=retried,
			failed=failed,
		)

	async def _claim(
		self,
		now: datetime,
	) -> tuple[CycleStatus, list[TopicRecord]]:
		async with self._sessions.begin() as session:
			settings = await SettingsRepository(session, self._codec).get_or_create()
			scheduler = SchedulerRepository(session, self._codec)
			if not settings.global_bump_enabled:
				return CycleStatus.DISABLED, []
			if settings.api_paused:
				return CycleStatus.API_PAUSED, []
			if await scheduler.migration_running():
				return CycleStatus.MIGRATING, []
			topics = await scheduler.claim_due(
				now,
				now + timedelta(seconds=self._lease_seconds),
				self._claim_limit,
			)
		return CycleStatus.RAN, topics

	async def _release_leases(self, topic_ids: Sequence[uuid.UUID]) -> None:
		if not topic_ids:
			return
		async with self._sessions.begin() as session:
			scheduler = SchedulerRepository(session, self._codec)
			for topic_id in topic_ids:
				await scheduler.clear_lease(topic_id)

	async def _persist_result(
		self,
		topic_id: uuid.UUID,
		result: BumpResult,
		now: datetime,
	) -> tuple[TopicRecord, SettingsRecord] | None:
		async with self._sessions.begin() as session:
			topics = TopicRepository(session, self._codec)
			attempts = AttemptRepository(session, self._codec)
			settings_repository = SettingsRepository(session, self._codec)
			scheduler = SchedulerRepository(session, self._codec)
			topic = await topics.get(topic_id)
			if topic is None:
				return None
			settings = await settings_repository.get_or_create()
			retry_count = await attempts.retry_count(topic.id, topic.last_success_at)
			updated = self._updated_topic(
				topic,
				result,
				settings.global_interval_seconds,
				settings.retry_schedule,
				retry_count,
				now,
			)
			await attempts.add(
				topic.id,
				result.job_id,
				self._attempt_outcome(result.outcome),
				False,
				retry_at=(
					updated.next_bump_at
					if result.outcome is BumpOutcome.RETRY
					else result.retry_at
				),
				error=result.error,
			)
			await topics.save(updated)
			await scheduler.clear_lease(topic.id)
			if result.outcome in {
				BumpOutcome.UNAUTHORIZED,
				BumpOutcome.FORBIDDEN,
			}:
				await settings_repository.save(replace(settings, api_paused=True))
		return updated, settings

	async def _notify(
		self,
		topic: TopicRecord,
		result: BumpResult,
		settings: SettingsRecord,
	) -> None:
		if self._notifier is None:
			return
		message = self._notification_text(topic, result, settings)
		if message is None:
			return
		try:
			await self._notifier(message)
		except Exception:
			logging.getLogger(__name__).exception("Scheduler notification failed")

	@staticmethod
	def _notification_text(
		topic: TopicRecord,
		result: BumpResult,
		settings: SettingsRecord,
	) -> str | None:
		if result.outcome is BumpOutcome.SUCCESS:
			if not settings.notify_success:
				return None
			return f"Тема «{topic.title}» успешно поднята автоматически."
		if result.outcome in {
			BumpOutcome.UNAUTHORIZED,
			BumpOutcome.FORBIDDEN,
		}:
			return (
				"Автоподнятие приостановлено: Forum API отклонил "
				"авторизацию или доступ аккаунта."
			)
		if not settings.notify_errors:
			return None
		if result.outcome is BumpOutcome.RETRY:
			return f"Тема «{topic.title}» временно не поднята. Запланирован повтор."
		if result.outcome is BumpOutcome.NOT_FOUND:
			return f"Тема «{topic.title}» удалена или недоступна."
		return f"Не удалось автоматически поднять тему «{topic.title}»."

	@staticmethod
	def _updated_topic(
		topic: TopicRecord,
		result: BumpResult,
		global_interval_seconds: int,
		retry_schedule: list[int],
		retry_count: int,
		now: datetime,
	) -> TopicRecord:
		if result.outcome is BumpOutcome.SUCCESS:
			interval = effective_interval(
				global_interval_seconds,
				topic.custom_interval_enabled,
				topic.custom_interval_seconds,
			)
			return replace(
				topic,
				last_success_at=now,
				next_bump_at=now + interval,
				last_error=None,
			)
		if result.outcome is BumpOutcome.RETRY:
			if not retry_schedule:
				raise ValueError("Retry schedule must not be empty")
			delay = retry_schedule[min(retry_count, len(retry_schedule) - 1)]
			return replace(
				topic,
				next_bump_at=result.retry_at or now + timedelta(seconds=delay),
				last_error=result.error,
			)
		return replace(topic, next_bump_at=None, last_error=result.error)

	def _job_id(self, topic: TopicRecord) -> str:
		return f"auto-{topic.id.hex[:16]}-{self._job_id_factory()[:16]}"

	@staticmethod
	def _attempt_outcome(outcome: BumpOutcome) -> AttemptOutcome:
		if outcome is BumpOutcome.SUCCESS:
			return AttemptOutcome.SUCCESS
		if outcome is BumpOutcome.RETRY:
			return AttemptOutcome.RETRY
		return AttemptOutcome.ERROR

	@staticmethod
	def _chunks(
		values: Sequence[TopicRecord],
		size: int,
	) -> list[Sequence[TopicRecord]]:
		return [values[index : index + size] for index in range(0, len(values), size)]

	@staticmethod
	def _as_utc(value: datetime) -> datetime:
		if value.tzinfo is None or value.utcoffset() is None:
			raise ValueError("Cycle time must be timezone-aware")
		return value.astimezone(UTC)
