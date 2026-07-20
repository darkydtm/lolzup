import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from lolzup.db.models import AttemptOutcome
from lolzup.db.repositories import SettingsRecord, TopicRecord
from lolzup.forum.types import BumpJob, BumpOutcome, BumpResult, ThreadInfo
from lolzup.topics.service import TopicNotFoundError, TopicService

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


@dataclass
class FakeTopics:
	records: dict[uuid.UUID, TopicRecord] = field(default_factory=dict)
	added: list[tuple[int, str, datetime | None]] = field(default_factory=list)

	async def add(
		self,
		thread_id: int,
		title: str,
		next_bump_at: datetime | None = None,
	) -> TopicRecord:
		record = topic_record(
			thread_id=thread_id,
			title=title,
			next_bump_at=next_bump_at,
		)
		self.records[record.id] = record
		self.added.append((thread_id, title, next_bump_at))
		return record

	async def get(self, topic_id: uuid.UUID) -> TopicRecord | None:
		return self.records.get(topic_id)

	async def list(self) -> list[TopicRecord]:
		return list(self.records.values())

	async def remove(self, topic_id: uuid.UUID) -> None:
		self.records.pop(topic_id, None)

	async def save(self, record: TopicRecord) -> None:
		self.records[record.id] = record


@dataclass
class FakeSettings:
	record: SettingsRecord = field(
		default_factory=lambda: SettingsRecord(
			True,
			72 * 3600,
			[60, 300, 900],
			False,
			True,
			False,
		)
	)

	async def get_or_create(self) -> SettingsRecord:
		return self.record

	async def save(self, record: SettingsRecord) -> None:
		self.record = record


@dataclass
class AttemptCall:
	topic_id: uuid.UUID
	job_id: str
	outcome: AttemptOutcome
	is_manual: bool
	retry_at: datetime | None
	error: str | None


@dataclass
class FakeAttempts:
	calls: list[AttemptCall] = field(default_factory=list)

	async def add(
		self,
		topic_id: uuid.UUID,
		job_id: str,
		outcome: AttemptOutcome,
		is_manual: bool,
		retry_at: datetime | None = None,
		error: str | None = None,
	) -> uuid.UUID:
		self.calls.append(
			AttemptCall(topic_id, job_id, outcome, is_manual, retry_at, error)
		)
		return uuid.uuid4()


@dataclass
class FakeForum:
	thread: ThreadInfo = field(default_factory=lambda: ThreadInfo(5523020, "Topic"))
	bump_result: BumpResult = field(
		default_factory=lambda: BumpResult(
			"unused",
			5523020,
			BumpOutcome.SUCCESS,
		)
	)
	thread_requests: list[int] = field(default_factory=list)
	batches: list[list[BumpJob]] = field(default_factory=list)

	async def get_thread(self, thread_id: int) -> ThreadInfo:
		self.thread_requests.append(thread_id)
		return self.thread

	async def bump_batch(self, jobs: list[BumpJob]) -> list[BumpResult]:
		self.batches.append(jobs)
		job = jobs[0]
		return [
			BumpResult(
				job.job_id,
				job.thread_id,
				self.bump_result.outcome,
				self.bump_result.retry_at,
				self.bump_result.error,
			)
		]


def topic_record(
	*,
	thread_id: int = 5523020,
	title: str = "Topic",
	auto_bump_enabled: bool = True,
	custom_interval_enabled: bool = False,
	custom_interval_seconds: int | None = None,
	last_success_at: datetime | None = None,
	next_bump_at: datetime | None = None,
	last_error: str | None = None,
) -> TopicRecord:
	return TopicRecord(
		id=uuid.uuid4(),
		thread_id=thread_id,
		title=title,
		auto_bump_enabled=auto_bump_enabled,
		custom_interval_enabled=custom_interval_enabled,
		custom_interval_seconds=custom_interval_seconds,
		last_success_at=last_success_at,
		next_bump_at=next_bump_at,
		last_error=last_error,
	)


def build_service(
	*,
	topics: FakeTopics | None = None,
	settings: FakeSettings | None = None,
	attempts: FakeAttempts | None = None,
	forum: FakeForum | None = None,
) -> tuple[TopicService, FakeTopics, FakeSettings, FakeAttempts, FakeForum]:
	active_topics = topics or FakeTopics()
	active_settings = settings or FakeSettings()
	active_attempts = attempts or FakeAttempts()
	active_forum = forum or FakeForum()
	service = TopicService(
		active_topics,
		active_settings,
		active_attempts,
		active_forum,
		clock=lambda: NOW,
		job_id_factory=lambda: "fixed-job-id",
	)
	return service, active_topics, active_settings, active_attempts, active_forum


@pytest.mark.unit
def test_add_resolves_title_before_storing_topic() -> None:
	async def scenario() -> None:
		service, topics, _, _, forum = build_service()

		record = await service.add("https://lolz.live/threads/5523020")

		assert forum.thread_requests == [5523020]
		assert topics.added == [(5523020, "Topic", NOW + timedelta(hours=72))]
		assert record.next_bump_at == NOW + timedelta(hours=72)

	asyncio.run(scenario())


@pytest.mark.unit
def test_read_methods_return_domain_records() -> None:
	async def scenario() -> None:
		topic = topic_record()
		topics = FakeTopics({topic.id: topic})
		service, _, settings, _, _ = build_service(topics=topics)

		assert await service.get(topic.id) == topic
		assert await service.list() == [topic]
		assert await service.settings() == settings.record

	asyncio.run(scenario())


@pytest.mark.unit
def test_topic_toggle_and_custom_interval_recalculate_schedule() -> None:
	async def scenario() -> None:
		topic = topic_record(next_bump_at=NOW - timedelta(hours=1))
		topics = FakeTopics({topic.id: topic})
		service, _, _, _, _ = build_service(topics=topics)

		disabled = await service.set_enabled(topic.id, False)
		assert not disabled.auto_bump_enabled

		enabled = await service.set_enabled(topic.id, True)
		assert enabled.next_bump_at == NOW + timedelta(hours=72)

		custom = await service.set_custom_interval(topic.id, True, 6 * 3600)
		assert custom.custom_interval_enabled
		assert custom.custom_interval_seconds == 6 * 3600
		assert custom.next_bump_at == NOW + timedelta(hours=6)

	asyncio.run(scenario())


@pytest.mark.unit
def test_global_interval_updates_only_global_topics() -> None:
	async def scenario() -> None:
		global_topic = topic_record(last_success_at=NOW - timedelta(hours=1))
		custom_topic = topic_record(
			custom_interval_enabled=True,
			custom_interval_seconds=3600,
			next_bump_at=NOW,
		)
		topics = FakeTopics(
			{
				global_topic.id: global_topic,
				custom_topic.id: custom_topic,
			}
		)
		service, _, settings, _, _ = build_service(topics=topics)

		await service.set_global_interval(24 * 3600)

		assert settings.record.global_interval_seconds == 24 * 3600
		assert topics.records[global_topic.id].next_bump_at == NOW + timedelta(hours=23)
		assert topics.records[custom_topic.id].next_bump_at == NOW

	asyncio.run(scenario())


@pytest.mark.unit
def test_retry_and_notification_settings_are_updated() -> None:
	async def scenario() -> None:
		service, _, settings, _, _ = build_service()

		await service.set_retry_schedule([120, 600])
		await service.set_notifications(success=True, errors=False)

		assert settings.record.retry_schedule == [120, 600]
		assert settings.record.notify_success
		assert not settings.record.notify_errors

	asyncio.run(scenario())


@pytest.mark.unit
def test_successful_manual_bump_uses_single_batch_job_and_reschedules() -> None:
	async def scenario() -> None:
		topic = topic_record(custom_interval_enabled=True, custom_interval_seconds=3600)
		topics = FakeTopics({topic.id: topic})
		service, _, _, attempts, forum = build_service(topics=topics)

		result = await service.manual_bump(topic.id)

		assert result.outcome is BumpOutcome.SUCCESS
		assert len(forum.batches) == 1
		assert len(forum.batches[0]) == 1
		assert forum.batches[0][0].thread_id == topic.thread_id
		assert attempts.calls[0].outcome is AttemptOutcome.SUCCESS
		updated = topics.records[topic.id]
		assert updated.last_success_at == NOW
		assert updated.next_bump_at == NOW + timedelta(hours=1)
		assert updated.last_error is None

	asyncio.run(scenario())


@pytest.mark.unit
def test_failed_manual_bump_records_error_without_rescheduling() -> None:
	async def scenario() -> None:
		next_at = NOW + timedelta(hours=2)
		topic = topic_record(next_bump_at=next_at)
		topics = FakeTopics({topic.id: topic})
		forum = FakeForum(
			bump_result=BumpResult(
				"unused",
				topic.thread_id,
				BumpOutcome.RETRY,
				NOW + timedelta(minutes=1),
				"Forum API returned status 429",
			)
		)
		service, _, _, attempts, _ = build_service(topics=topics, forum=forum)

		await service.manual_bump(topic.id)

		assert attempts.calls[0].outcome is AttemptOutcome.RETRY
		assert topics.records[topic.id].next_bump_at == next_at
		assert topics.records[topic.id].last_error == "Forum API returned status 429"

	asyncio.run(scenario())


@pytest.mark.unit
def test_missing_topic_is_rejected() -> None:
	async def scenario() -> None:
		service, _, _, _, _ = build_service()

		with pytest.raises(TopicNotFoundError):
			await service.remove(uuid.uuid4())

	asyncio.run(scenario())
