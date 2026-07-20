import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lolzup.db.models import EncryptionMigration, MigrationStatus, Topic
from lolzup.db.repositories import EncryptedFieldCodec, TopicRecord, TopicRepository


def advisory_lock_key(topic_id: uuid.UUID) -> int:
	return int.from_bytes(topic_id.bytes[:8], byteorder="big", signed=True)


class SchedulerRepository:
	def __init__(self, session: AsyncSession, codec: EncryptedFieldCodec) -> None:
		self._session = session
		self._topics = TopicRepository(session, codec)

	async def migration_running(self) -> bool:
		return (
			await self._session.scalar(
				select(EncryptionMigration.id).where(
					EncryptionMigration.status == MigrationStatus.RUNNING
				)
			)
			is not None
		)

	async def claim_due(
		self,
		now: datetime,
		lease_until: datetime,
		limit: int,
	) -> list[TopicRecord]:
		if limit <= 0:
			raise ValueError("Claim limit must be positive")
		candidate_ids = await self._session.scalars(
			select(Topic.id)
			.where(
				Topic.schedule_due_at.is_not(None),
				Topic.schedule_due_at <= now,
				or_(Topic.lease_until.is_(None), Topic.lease_until <= now),
			)
			.order_by(Topic.schedule_due_at, Topic.id)
			.limit(limit)
		)
		claimed = []
		for topic_id in candidate_ids:
			locked = await self._session.scalar(
				select(func.pg_try_advisory_xact_lock(advisory_lock_key(topic_id)))
			)
			if not locked:
				continue
			model = await self._session.get(Topic, topic_id)
			if (
				model is None
				or model.schedule_due_at is None
				or model.schedule_due_at > now
				or (model.lease_until is not None and model.lease_until > now)
			):
				continue
			model.lease_until = lease_until
			topic = await self._topics.get(topic_id)
			if topic is not None and topic.auto_bump_enabled:
				claimed.append(topic)
			else:
				model.schedule_due_at = None
				model.lease_until = None
		await self._session.flush()
		return claimed

	async def clear_lease(self, topic_id: uuid.UUID) -> None:
		model = await self._session.get(Topic, topic_id)
		if model is not None:
			model.lease_until = None
			await self._session.flush()
