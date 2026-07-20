import enum
from dataclasses import dataclass
from datetime import datetime


class BumpOutcome(enum.StrEnum):
	SUCCESS = "success"
	RETRY = "retry"
	UNAUTHORIZED = "unauthorized"
	FORBIDDEN = "forbidden"
	NOT_FOUND = "not_found"
	ERROR = "error"


class ForumErrorKind(enum.StrEnum):
	UNAUTHORIZED = "unauthorized"
	FORBIDDEN = "forbidden"
	NOT_FOUND = "not_found"
	RATE_LIMITED = "rate_limited"
	SERVER_ERROR = "server_error"
	NETWORK = "network"
	INVALID_RESPONSE = "invalid_response"
	OTHER = "other"


@dataclass(frozen=True, slots=True)
class ThreadInfo:
	thread_id: int
	title: str


@dataclass(frozen=True, slots=True)
class BumpJob:
	job_id: str
	thread_id: int


@dataclass(frozen=True, slots=True)
class BumpResult:
	job_id: str
	thread_id: int
	outcome: BumpOutcome
	retry_at: datetime | None = None
	error: str | None = None
