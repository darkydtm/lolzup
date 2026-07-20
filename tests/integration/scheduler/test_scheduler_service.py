import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lolzup.db.models import (
	AppSettings,
	Base,
	BumpAttempt,
	EncryptionMigration,
	EncryptionMode,
	MigrationStatus,
	Topic,
)
from lolzup.db.repositories import (
	EncryptedFieldCodec,
	SettingsRecord,
	SettingsRepository,
	TopicRepository,
)
from lolzup.forum.types import BumpJob, BumpOutcome, BumpResult, ThreadInfo
from lolzup.scheduler import (
	CycleStatus,
	SchedulerService,
	advisory_lock_key,
)
from lolzup.security.crypto import generate_data_key
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


@dataclass
class FakeForum:
	outcomes: dict[int, BumpResult] = field(default_factory=dict)
	batches: list[list[BumpJob]] = field(default_factory=list)

	async def get_thread(self, thread_id: int) -> ThreadInfo:
		return ThreadInfo(thread_id, f"Topic {thread_id}")

	async def bump_batch(self, jobs: list[BumpJob]) -> list[BumpResult]:
		self.batches.append(jobs)
		return [
			BumpResult(
				job.job_id,
				job.thread_id,
				self.outcomes.get(
					job.thread_id,
					BumpResult(job.job_id, job.thread_id, BumpOutcome.SUCCESS),
				).outcome,
				self.outcomes.get(
					job.thread_id,
					BumpResult(job.job_id, job.thread_id, BumpOutcome.SUCCESS),
				).retry_at,
				self.outcomes.get(
					job.thread_id,
					BumpResult(job.job_id, job.thread_id, BumpOutcome.SUCCESS),
				).error,
			)
			for job in jobs
		]


@dataclass
class FakeNotifier:
	messages: list[str] = field(default_factory=list)

	async def __call__(self, message: str) -> None:
		self.messages.append(message)


@pytest.mark.integration
def test_scheduler_batches_due_topics_and_persists_partial_results() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(
			EncryptionPolicy(EncryptionMode.FULL),
			vault,
		)
		sessions = async_sessionmaker(engine, expire_on_commit=False)
		topic_ids: dict[int, uuid.UUID] = {}
		async with sessions.begin() as session:
			await SettingsRepository(session, codec).save(
				SettingsRecord(
					True,
					72 * 3600,
					[60, 300, 900],
					False,
					False,
					False,
				)
			)
			topics = TopicRepository(session, codec)
			for thread_id in range(1, 24):
				topic = await topics.add(
					thread_id,
					f"Topic {thread_id}",
					next_bump_at=NOW,
				)
				topic_ids[thread_id] = topic.id

		forum = FakeForum(
			{
				2: BumpResult(
					"unused",
					2,
					BumpOutcome.RETRY,
					NOW + timedelta(minutes=2),
					"rate limited",
				),
				3: BumpResult(
					"unused",
					3,
					BumpOutcome.NOT_FOUND,
					error="not found",
				),
				4: BumpResult(
					"unused",
					4,
					BumpOutcome.UNAUTHORIZED,
					error="unauthorized",
				),
			}
		)
		notifier = FakeNotifier()
		scheduler = SchedulerService(
			sessions,
			codec,
			forum,
			vault,
			notifier=notifier,
			job_id_factory=lambda: uuid.uuid4().hex,
		)

		report = await scheduler.run_cycle(NOW)

		assert report.status is CycleStatus.RAN
		assert report.claimed == 23
		assert report.batches == 3
		assert [len(batch) for batch in forum.batches] == [10, 10, 3]
		assert report.succeeded == 20
		assert report.retried == 1
		assert report.failed == 2

		async with sessions.begin() as session:
			assert await session.scalar(select(func.count(BumpAttempt.id))) == 23
			raw_settings = await session.get(AppSettings, 1)
			assert raw_settings is not None
			assert raw_settings.api_paused
			retry_topic = await TopicRepository(session, codec).get(topic_ids[2])
			assert retry_topic is not None
			assert retry_topic.next_bump_at == NOW + timedelta(minutes=2)
			missing_topic = await TopicRepository(session, codec).get(topic_ids[3])
			assert missing_topic is not None
			assert missing_topic.next_bump_at is None
			raw_topics = await session.scalars(select(Topic))
			assert all(topic.lease_until is None for topic in raw_topics)
		assert len(notifier.messages) == 1
		assert "Автоподнятие приостановлено" in notifier.messages[0]

		await engine.dispose()

	asyncio.run(scenario())


@pytest.mark.integration
def test_scheduler_sends_success_notification_when_enabled() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(
			EncryptionPolicy(EncryptionMode.FULL),
			vault,
		)
		sessions = async_sessionmaker(engine, expire_on_commit=False)
		async with sessions.begin() as session:
			await SettingsRepository(session, codec).save(
				SettingsRecord(
					True,
					72 * 3600,
					[60, 300, 900],
					True,
					False,
					False,
				)
			)
			await TopicRepository(session, codec).add(
				42,
				"Topic",
				next_bump_at=NOW,
			)

		notifier = FakeNotifier()
		report = await SchedulerService(
			sessions,
			codec,
			FakeForum(),
			vault,
			notifier=notifier,
		).run_cycle(NOW)

		assert report.succeeded == 1
		assert notifier.messages == ["Тема «Topic» успешно поднята автоматически."]
		await engine.dispose()

	asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
	("locked", "global_enabled", "api_paused", "migrating", "expected"),
	[
		(True, True, False, False, CycleStatus.LOCKED),
		(False, False, False, False, CycleStatus.DISABLED),
		(False, True, True, False, CycleStatus.API_PAUSED),
		(False, True, False, True, CycleStatus.MIGRATING),
	],
)
def test_scheduler_respects_global_guards(
	locked: bool,
	global_enabled: bool,
	api_paused: bool,
	migrating: bool,
	expected: CycleStatus,
) -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(
			EncryptionPolicy(EncryptionMode.FULL),
			vault,
		)
		sessions = async_sessionmaker(engine, expire_on_commit=False)
		async with sessions.begin() as session:
			settings = SettingsRepository(session, codec)
			await settings.save(
				SettingsRecord(
					global_enabled,
					72 * 3600,
					[60, 300, 900],
					False,
					True,
					api_paused,
				)
			)
			if migrating:
				session.add(
					EncryptionMigration(
						id=1,
						status=MigrationStatus.RUNNING,
					)
				)
		if locked:
			await vault.lock()

		forum = FakeForum()
		report = await SchedulerService(
			sessions,
			codec,
			forum,
			vault,
		).run_cycle(NOW)

		assert report.status is expected
		assert forum.batches == []
		await engine.dispose()

	asyncio.run(scenario())


@pytest.mark.integration
def test_advisory_lock_and_lease_prevent_duplicate_claims() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(
			EncryptionPolicy(EncryptionMode.FULL),
			vault,
		)
		sessions = async_sessionmaker(engine, expire_on_commit=False)
		async with sessions.begin() as session:
			topic = await TopicRepository(session, codec).add(
				42,
				"Topic",
				next_bump_at=NOW,
			)

		forum = FakeForum()
		scheduler = SchedulerService(sessions, codec, forum, vault)
		async with sessions() as blocker:
			async with blocker.begin():
				await blocker.scalar(
					select(func.pg_advisory_xact_lock(advisory_lock_key(topic.id)))
				)
				blocked = await scheduler.run_cycle(NOW)
				assert blocked.claimed == 0

		processed = await scheduler.run_cycle(NOW)
		assert processed.claimed == 1
		assert len(forum.batches) == 1

		async with sessions.begin() as session:
			raw = await session.get(Topic, topic.id)
			assert raw is not None
			raw.schedule_due_at = NOW
			raw.lease_until = NOW + timedelta(minutes=1)
		leased = await scheduler.run_cycle(NOW)
		assert leased.claimed == 0

		recovered = await scheduler.run_cycle(NOW + timedelta(minutes=1))
		assert recovered.claimed == 1
		await engine.dispose()

	asyncio.run(scenario())


@pytest.mark.integration
def test_scheduler_advances_through_default_retry_schedule() -> None:
	async def scenario() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

		vault = RuntimeVault()
		await vault.unlock(generate_data_key())
		codec = EncryptedFieldCodec(
			EncryptionPolicy(EncryptionMode.FULL),
			vault,
		)
		sessions = async_sessionmaker(engine, expire_on_commit=False)
		async with sessions.begin() as session:
			topic = await TopicRepository(session, codec).add(
				42,
				"Topic",
				next_bump_at=NOW,
			)

		forum = FakeForum(
			{
				42: BumpResult(
					"unused",
					42,
					BumpOutcome.RETRY,
					error="temporary failure",
				)
			}
		)
		scheduler = SchedulerService(sessions, codec, forum, vault)
		cycle_times = [
			NOW,
			NOW + timedelta(minutes=1),
			NOW + timedelta(minutes=6),
		]
		expected_retry_times = [
			NOW + timedelta(minutes=1),
			NOW + timedelta(minutes=6),
			NOW + timedelta(minutes=21),
		]

		for cycle_time, expected_retry_at in zip(
			cycle_times,
			expected_retry_times,
			strict=True,
		):
			report = await scheduler.run_cycle(cycle_time)
			assert report.retried == 1
			async with sessions.begin() as session:
				stored_topic = await TopicRepository(session, codec).get(topic.id)
				assert stored_topic is not None
				assert stored_topic.next_bump_at == expected_retry_at

		async with sessions.begin() as session:
			attempts = list(
				await session.scalars(
					select(BumpAttempt).order_by(BumpAttempt.created_at)
				)
			)
			assert len(attempts) == 3
			for attempt, expected_retry_at in zip(
				attempts,
				expected_retry_times,
				strict=True,
			):
				assert attempt.retry_at_plain is None
				assert attempt.retry_at_ciphertext is not None
				decoded = codec.decode(
					DataCategory.HISTORY,
					f"bump_attempts:{attempt.id}:retry_at",
					attempt.retry_at_plain,
					attempt.retry_at_ciphertext,
					attempt.retry_at_nonce,
				)
				assert decoded == expected_retry_at

		await engine.dispose()

	asyncio.run(scenario())
