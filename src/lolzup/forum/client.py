import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from lolzup.forum.types import (
	BumpJob,
	BumpOutcome,
	BumpResult,
	ForumErrorKind,
	ThreadInfo,
)

MAX_BATCH_JOBS = 10
DEFAULT_TIMEOUT_SECONDS = 10.0

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]
TokenProvider = Callable[[], Awaitable[str]]


class ForumApiError(RuntimeError):
	def __init__(
		self,
		message: str,
		kind: ForumErrorKind,
		*,
		status_code: int | None = None,
		retry_at: datetime | None = None,
	) -> None:
		super().__init__(message)
		self.kind = kind
		self.status_code = status_code
		self.retry_at = retry_at

	@property
	def retryable(self) -> bool:
		return self.kind in {
			ForumErrorKind.RATE_LIMITED,
			ForumErrorKind.SERVER_ERROR,
			ForumErrorKind.NETWORK,
		}


class ForumApiClient:
	def __init__(
		self,
		client: httpx.AsyncClient,
		token: str | TokenProvider,
		*,
		timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
		clock: Clock | None = None,
		sleep: Sleep = asyncio.sleep,
	) -> None:
		if isinstance(token, str) and not token:
			raise ValueError("Forum API token must not be empty")
		if timeout_seconds <= 0:
			raise ValueError("Forum API timeout must be positive")

		self._client = client
		self._token_provider = (
			self._static_token_provider(token) if isinstance(token, str) else token
		)
		self._timeout = httpx.Timeout(timeout_seconds)
		self._clock = clock or (lambda: datetime.now(UTC))
		self._sleep = sleep
		self._request_lock = asyncio.Lock()
		self._blocked_until: datetime | None = None

	async def get_thread(self, thread_id: int) -> ThreadInfo:
		if thread_id <= 0:
			raise ValueError("Thread ID must be positive")

		response = await self._request("GET", f"/threads/{thread_id}")
		if not response.is_success:
			raise self._response_error(response)

		payload = self._read_json(response)
		thread = payload.get("thread", payload)
		if not isinstance(thread, Mapping):
			raise self._invalid_response()

		title = thread.get("title")
		response_thread_id = thread.get("thread_id", thread_id)
		if (
			not isinstance(title, str)
			or not title.strip()
			or not isinstance(response_thread_id, int)
		):
			raise self._invalid_response()
		return ThreadInfo(thread_id=response_thread_id, title=title)

	async def bump_batch(self, jobs: Sequence[BumpJob]) -> list[BumpResult]:
		if not jobs:
			return []
		if len(jobs) > MAX_BATCH_JOBS:
			raise ValueError("A Forum API batch accepts at most 10 jobs")
		if len({job.job_id for job in jobs}) != len(jobs):
			raise ValueError("Batch job IDs must be unique")
		if any(not job.job_id or job.thread_id <= 0 for job in jobs):
			raise ValueError("Batch jobs require an ID and a positive thread ID")

		payload = [
			{
				"id": job.job_id,
				"method": "POST",
				"uri": f"/threads/{job.thread_id}/bump",
				"params": {},
			}
			for job in jobs
		]

		try:
			response = await self._request("POST", "/batch", json=payload)
		except ForumApiError as error:
			return [self._result_from_error(job, error) for job in jobs]

		if not response.is_success:
			response_error = self._response_error(response)
			return [self._result_from_error(job, response_error) for job in jobs]

		try:
			body = self._read_json(response)
			responses = self._index_batch_responses(body)
		except ForumApiError as error:
			return [self._result_from_error(job, error) for job in jobs]

		results = []
		for job in jobs:
			job_response = responses.get(job.job_id)
			if job_response is None:
				results.append(
					BumpResult(
						job.job_id,
						job.thread_id,
						BumpOutcome.RETRY,
						error="Forum API omitted the batch job result",
					)
				)
				continue
			results.append(self._parse_job_result(job, job_response, response))
		return results

	async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
		async with self._request_lock:
			await self._wait_for_rate_limit()
			token = await self._token_provider()
			if not token:
				raise ForumApiError(
					"Forum API token is unavailable",
					ForumErrorKind.UNAUTHORIZED,
				)
			try:
				response = await self._client.request(
					method,
					url,
					headers={"Authorization": f"Bearer {token}"},
					timeout=self._timeout,
					**kwargs,
				)
			except httpx.RequestError as error:
				raise ForumApiError(
					"Forum API request failed",
					ForumErrorKind.NETWORK,
				) from error
			self._update_rate_limit(response)
			return response

	@staticmethod
	def _static_token_provider(token: str) -> TokenProvider:
		async def provide() -> str:
			return token

		return provide

	async def _wait_for_rate_limit(self) -> None:
		if self._blocked_until is None:
			return
		delay = (self._blocked_until - self._clock()).total_seconds()
		if delay > 0:
			await self._sleep(delay)
		self._blocked_until = None

	def _update_rate_limit(self, response: httpx.Response) -> None:
		retry_at = self._retry_at(response.headers)
		remaining = self._integer_header(response.headers, "X-RateLimit-Remaining")
		reset_at = self._timestamp_header(response.headers, "X-RateLimit-Reset")

		try:
			payload = response.json()
		except ValueError:
			payload = None
		if isinstance(payload, Mapping):
			system_info = payload.get("system_info")
			if isinstance(system_info, Mapping):
				rate_limit = system_info.get("rate_limit")
				if isinstance(rate_limit, Mapping):
					remaining = self._as_int(rate_limit.get("remaining"), remaining)
					reset_at = self._as_datetime(
						rate_limit.get("reset")
						or rate_limit.get("reset_at")
						or rate_limit.get("reset_time"),
						reset_at,
					)

		if response.status_code == 429:
			self._blocked_until = retry_at or reset_at or self._clock()
		elif remaining is not None and remaining <= 0 and reset_at is not None:
			self._blocked_until = reset_at

	def _parse_job_result(
		self,
		job: BumpJob,
		payload: Mapping[str, Any],
		response: httpx.Response,
	) -> BumpResult:
		nested_response = payload.get("response")
		details = nested_response if isinstance(nested_response, Mapping) else payload
		status = self._as_int(
			details.get("status")
			or details.get("status_code")
			or payload.get("status")
			or payload.get("status_code"),
			None,
		)
		if status is None:
			return BumpResult(
				job.job_id,
				job.thread_id,
				BumpOutcome.ERROR,
				error="Forum API returned a batch job without status",
			)

		retry_at = self._retry_at_from_payload(details) or self._retry_at(
			response.headers
		)
		outcome = self._outcome_for_status(status)
		return BumpResult(
			job.job_id,
			job.thread_id,
			outcome,
			retry_at=retry_at if outcome is BumpOutcome.RETRY else None,
			error=None
			if outcome is BumpOutcome.SUCCESS
			else f"Forum API returned status {status}",
		)

	@staticmethod
	def _index_batch_responses(
		payload: Mapping[str, Any],
	) -> dict[str, Mapping[str, Any]]:
		raw_jobs = payload.get("jobs", payload.get("responses", payload.get("results")))
		if isinstance(raw_jobs, Mapping):
			return {
				str(job_id): job_response
				for job_id, job_response in raw_jobs.items()
				if isinstance(job_response, Mapping)
			}
		if isinstance(raw_jobs, list):
			return {
				str(job_response["id"]): job_response
				for job_response in raw_jobs
				if isinstance(job_response, Mapping) and "id" in job_response
			}
		raise ForumApiClient._invalid_response()

	def _response_error(self, response: httpx.Response) -> ForumApiError:
		status = response.status_code
		kind = self._error_kind_for_status(status)
		return ForumApiError(
			f"Forum API returned status {status}",
			kind,
			status_code=status,
			retry_at=self._retry_at(response.headers),
		)

	@staticmethod
	def _result_from_error(job: BumpJob, error: ForumApiError) -> BumpResult:
		outcome = {
			ForumErrorKind.UNAUTHORIZED: BumpOutcome.UNAUTHORIZED,
			ForumErrorKind.FORBIDDEN: BumpOutcome.FORBIDDEN,
			ForumErrorKind.NOT_FOUND: BumpOutcome.NOT_FOUND,
			ForumErrorKind.RATE_LIMITED: BumpOutcome.RETRY,
			ForumErrorKind.SERVER_ERROR: BumpOutcome.RETRY,
			ForumErrorKind.NETWORK: BumpOutcome.RETRY,
		}.get(error.kind, BumpOutcome.ERROR)
		return BumpResult(
			job.job_id,
			job.thread_id,
			outcome,
			retry_at=error.retry_at if outcome is BumpOutcome.RETRY else None,
			error=str(error),
		)

	@staticmethod
	def _outcome_for_status(status: int) -> BumpOutcome:
		if 200 <= status < 300:
			return BumpOutcome.SUCCESS
		if status == 401:
			return BumpOutcome.UNAUTHORIZED
		if status == 403:
			return BumpOutcome.FORBIDDEN
		if status == 404:
			return BumpOutcome.NOT_FOUND
		if status == 429 or status >= 500:
			return BumpOutcome.RETRY
		return BumpOutcome.ERROR

	@staticmethod
	def _error_kind_for_status(status: int) -> ForumErrorKind:
		if status == 401:
			return ForumErrorKind.UNAUTHORIZED
		if status == 403:
			return ForumErrorKind.FORBIDDEN
		if status == 404:
			return ForumErrorKind.NOT_FOUND
		if status == 429:
			return ForumErrorKind.RATE_LIMITED
		if status >= 500:
			return ForumErrorKind.SERVER_ERROR
		return ForumErrorKind.OTHER

	def _retry_at_from_payload(self, payload: Mapping[str, Any]) -> datetime | None:
		retry_at = self._as_datetime(payload.get("retry_at"), None)
		if retry_at is not None:
			return retry_at
		retry_after = self._as_int(payload.get("retry_after"), None)
		if retry_after is not None:
			return self._clock() + timedelta(seconds=max(retry_after, 0))

		body = payload.get("body")
		if isinstance(body, Mapping):
			return self._retry_at_from_payload(body)
		return None

	def _retry_at(self, headers: httpx.Headers) -> datetime | None:
		value = headers.get("Retry-After")
		if value is None:
			return None
		seconds = self._as_int(value, None)
		if seconds is not None:
			return self._clock() + timedelta(seconds=max(seconds, 0))
		try:
			parsed = parsedate_to_datetime(value)
		except (TypeError, ValueError):
			return None
		return self._ensure_utc(parsed)

	@staticmethod
	def _integer_header(headers: httpx.Headers, name: str) -> int | None:
		return ForumApiClient._as_int(headers.get(name), None)

	@staticmethod
	def _timestamp_header(headers: httpx.Headers, name: str) -> datetime | None:
		return ForumApiClient._as_datetime(headers.get(name), None)

	@staticmethod
	def _as_int(value: object, default: int | None) -> int | None:
		if isinstance(value, bool):
			return default
		if not isinstance(value, (str, int, float)):
			return default
		try:
			return int(value) if value is not None else default
		except (TypeError, ValueError):
			return default

	@staticmethod
	def _as_datetime(value: object, default: datetime | None) -> datetime | None:
		if isinstance(value, datetime):
			return ForumApiClient._ensure_utc(value)
		if isinstance(value, (int, float)):
			return datetime.fromtimestamp(value, UTC)
		if isinstance(value, str):
			try:
				return datetime.fromtimestamp(float(value), UTC)
			except ValueError:
				try:
					return ForumApiClient._ensure_utc(
						datetime.fromisoformat(value.replace("Z", "+00:00"))
					)
				except ValueError:
					return default
		return default

	@staticmethod
	def _ensure_utc(value: datetime) -> datetime:
		if value.tzinfo is None:
			return value.replace(tzinfo=UTC)
		return value.astimezone(UTC)

	@staticmethod
	def _read_json(response: httpx.Response) -> Mapping[str, Any]:
		try:
			payload = response.json()
		except ValueError as error:
			raise ForumApiClient._invalid_response() from error
		if not isinstance(payload, Mapping):
			raise ForumApiClient._invalid_response()
		return payload

	@staticmethod
	def _invalid_response() -> ForumApiError:
		return ForumApiError(
			"Forum API returned an invalid response",
			ForumErrorKind.INVALID_RESPONSE,
		)
