import enum
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class TopicTimingState(enum.StrEnum):
	SCHEDULED = "scheduled"
	OVERDUE = "overdue"
	PENDING = "pending"
	DISABLED = "disabled"
	ERROR = "error"


@dataclass(frozen=True, slots=True)
class TopicTiming:
	state: TopicTimingState
	previous_at: datetime | None
	next_at: datetime | None
	remaining_seconds: int | None
	error: str | None


def effective_interval(
	global_seconds: int,
	custom_enabled: bool,
	custom_seconds: int | None,
) -> timedelta:
	if global_seconds <= 0:
		raise ValueError("Global interval must be positive")
	if custom_enabled:
		if custom_seconds is None or custom_seconds <= 0:
			raise ValueError("Enabled custom interval must be positive")
		return timedelta(seconds=custom_seconds)
	return timedelta(seconds=global_seconds)


def next_bump_at(
	last_success: datetime | None,
	created_at: datetime,
	interval: timedelta,
) -> datetime:
	if interval <= timedelta(0):
		raise ValueError("Bump interval must be positive")
	base = last_success if last_success is not None else created_at
	return _as_utc(base) + interval


def format_topic_timing(
	now: datetime,
	previous: datetime | None,
	next_at: datetime | None,
	enabled: bool,
	error: str | None,
) -> TopicTiming:
	current = _as_utc(now)
	previous_utc = None if previous is None else _as_utc(previous)
	next_utc = None if next_at is None else _as_utc(next_at)

	if not enabled:
		state = TopicTimingState.DISABLED
		remaining = None
	elif error:
		state = TopicTimingState.ERROR
		remaining = None
	elif next_utc is None:
		state = TopicTimingState.PENDING
		remaining = None
	elif next_utc <= current:
		state = TopicTimingState.OVERDUE
		remaining = None
	else:
		state = TopicTimingState.SCHEDULED
		remaining = math.ceil((next_utc - current).total_seconds())

	return TopicTiming(
		state=state,
		previous_at=previous_utc,
		next_at=next_utc,
		remaining_seconds=remaining,
		error=error,
	)


def _as_utc(value: datetime) -> datetime:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Datetime values must be timezone-aware")
	return value.astimezone(UTC)
