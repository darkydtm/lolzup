from datetime import UTC, datetime, timedelta, timezone

import pytest

from lolzup.topics.schedule import (
	TopicTimingState,
	effective_interval,
	format_topic_timing,
	next_bump_at,
)

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


@pytest.mark.unit
def test_effective_interval_uses_global_default() -> None:
	assert effective_interval(72 * 3600, False, None) == timedelta(hours=72)


@pytest.mark.unit
def test_effective_interval_uses_enabled_custom_value() -> None:
	assert effective_interval(72 * 3600, True, 6 * 3600) == timedelta(hours=6)


@pytest.mark.unit
@pytest.mark.parametrize(("enabled", "seconds"), [(True, None), (True, 0)])
def test_effective_interval_rejects_invalid_custom_value(
	enabled: bool, seconds: int | None
) -> None:
	with pytest.raises(ValueError, match="custom interval"):
		effective_interval(72 * 3600, enabled, seconds)


@pytest.mark.unit
def test_next_bump_uses_last_success_or_creation_time() -> None:
	created = NOW - timedelta(days=4)
	previous = NOW - timedelta(hours=12)
	interval = timedelta(hours=72)

	assert next_bump_at(previous, created, interval) == previous + interval
	assert next_bump_at(None, created, interval) == created + interval


@pytest.mark.unit
def test_next_bump_normalizes_to_utc() -> None:
	created = datetime(2026, 7, 20, 18, 0, tzinfo=timezone(timedelta(hours=6)))

	assert next_bump_at(None, created, timedelta(hours=1)) == datetime(
		2026, 7, 20, 13, 0, tzinfo=UTC
	)


@pytest.mark.unit
def test_format_topic_timing_returns_scheduled_countdown() -> None:
	previous = NOW - timedelta(hours=2)
	next_at = NOW + timedelta(seconds=60, microseconds=1)

	timing = format_topic_timing(NOW, previous, next_at, True, None)

	assert timing.state is TopicTimingState.SCHEDULED
	assert timing.previous_at == previous
	assert timing.next_at == next_at
	assert timing.remaining_seconds == 61
	assert timing.error is None


@pytest.mark.unit
@pytest.mark.parametrize(
	("enabled", "next_at", "error", "expected"),
	[
		(False, NOW + timedelta(hours=1), None, TopicTimingState.DISABLED),
		(True, None, None, TopicTimingState.PENDING),
		(True, NOW, None, TopicTimingState.OVERDUE),
		(True, NOW - timedelta(seconds=1), None, TopicTimingState.OVERDUE),
		(True, NOW + timedelta(hours=1), "failed", TopicTimingState.ERROR),
	],
)
def test_format_topic_timing_handles_non_countdown_states(
	enabled: bool,
	next_at: datetime | None,
	error: str | None,
	expected: TopicTimingState,
) -> None:
	timing = format_topic_timing(NOW, None, next_at, enabled, error)

	assert timing.state is expected
	assert timing.remaining_seconds is None


@pytest.mark.unit
def test_schedule_functions_reject_naive_datetimes() -> None:
	naive = datetime(2026, 7, 20, 12, 0)

	with pytest.raises(ValueError, match="timezone-aware"):
		next_bump_at(None, naive, timedelta(hours=1))
	with pytest.raises(ValueError, match="timezone-aware"):
		format_topic_timing(naive, None, None, True, None)
