import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from lolzup.forum import (
	BumpJob,
	BumpOutcome,
	ForumApiClient,
	ForumApiError,
	ForumErrorKind,
	ThreadInfo,
)

BASE_URL = "https://prod-api.lolz.live"
NOW = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def run(coroutine: object) -> object:
	return asyncio.run(coroutine)  # type: ignore[arg-type]


def client_for(
	handler: httpx.AsyncBaseTransport,
	*,
	token: str = "secret-token",
	sleep: object = asyncio.sleep,
) -> tuple[httpx.AsyncClient, ForumApiClient]:
	http_client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
	return (
		http_client,
		ForumApiClient(
			http_client,
			token,
			clock=lambda: NOW,
			sleep=sleep,  # type: ignore[arg-type]
		),
	)


@pytest.mark.unit
def test_get_thread_uses_auth_and_parses_title() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		assert request.method == "GET"
		assert request.url == f"{BASE_URL}/threads/42"
		assert request.headers["Authorization"] == "Bearer secret-token"
		assert request.extensions["timeout"] == {
			"connect": 10.0,
			"read": 10.0,
			"write": 10.0,
			"pool": 10.0,
		}
		return httpx.Response(
			200,
			json={"thread": {"thread_id": 42, "title": "Topic title"}},
		)

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			assert await forum.get_thread(42) == ThreadInfo(42, "Topic title")

	run(scenario())


@pytest.mark.unit
def test_bump_batch_posts_jobs_and_maps_partial_results() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		assert request.method == "POST"
		assert request.url == f"{BASE_URL}/batch"
		assert json.loads(request.content) == [
			{
				"id": "topic-42",
				"method": "POST",
				"uri": "/threads/42/bump",
				"params": {},
			},
			{
				"id": "topic-43",
				"method": "POST",
				"uri": "/threads/43/bump",
				"params": {},
			},
		]
		return httpx.Response(
			200,
			json={
				"jobs": {
					"topic-43": {"status": 404},
					"topic-42": {"status": 200},
				}
			},
		)

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			results = await forum.bump_batch(
				[BumpJob("topic-42", 42), BumpJob("topic-43", 43)]
			)
		assert [result.outcome for result in results] == [
			BumpOutcome.SUCCESS,
			BumpOutcome.NOT_FOUND,
		]
		assert [result.thread_id for result in results] == [42, 43]

	run(scenario())


@pytest.mark.unit
def test_bump_batch_accepts_list_response_and_explicit_retry() -> None:
	def handler(_: httpx.Request) -> httpx.Response:
		return httpx.Response(
			200,
			json={
				"jobs": [
					{
						"id": "topic-42",
						"response": {"status_code": 429, "retry_after": 120},
					}
				]
			},
		)

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			results = await forum.bump_batch([BumpJob("topic-42", 42)])
		assert results[0].outcome is BumpOutcome.RETRY
		assert results[0].retry_at == datetime(2026, 7, 20, 10, 2, tzinfo=UTC)

	run(scenario())


@pytest.mark.unit
def test_bump_batch_rejects_more_than_ten_jobs_without_request() -> None:
	requested = False

	def handler(_: httpx.Request) -> httpx.Response:
		nonlocal requested
		requested = True
		return httpx.Response(200, json={"jobs": {}})

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			with pytest.raises(ValueError, match="at most 10"):
				await forum.bump_batch(
					[
						BumpJob(f"topic-{thread_id}", thread_id)
						for thread_id in range(1, 12)
					]
				)
		assert not requested

	run(scenario())


@pytest.mark.unit
@pytest.mark.parametrize(
	("status", "outcome"),
	[
		(401, BumpOutcome.UNAUTHORIZED),
		(403, BumpOutcome.FORBIDDEN),
		(404, BumpOutcome.NOT_FOUND),
		(429, BumpOutcome.RETRY),
		(503, BumpOutcome.RETRY),
	],
)
def test_bump_batch_normalizes_top_level_errors(
	status: int, outcome: BumpOutcome
) -> None:
	def handler(_: httpx.Request) -> httpx.Response:
		return httpx.Response(status, headers={"Retry-After": "60"})

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			result = (await forum.bump_batch([BumpJob("topic-42", 42)]))[0]
		assert result.outcome is outcome
		if outcome is BumpOutcome.RETRY:
			assert result.retry_at == datetime(2026, 7, 20, 10, 1, tzinfo=UTC)

	run(scenario())


@pytest.mark.unit
def test_network_error_is_retryable_and_redacted() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		raise httpx.ConnectError(
			"failed with secret-token",
			request=request,
		)

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			result = (await forum.bump_batch([BumpJob("topic-42", 42)]))[0]
		assert result.outcome is BumpOutcome.RETRY
		assert result.error == "Forum API request failed"
		assert "secret-token" not in repr(result)

	run(scenario())


@pytest.mark.unit
def test_get_thread_raises_typed_redacted_error() -> None:
	def handler(_: httpx.Request) -> httpx.Response:
		return httpx.Response(403, text="secret-token private response")

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			with pytest.raises(ForumApiError) as raised:
				await forum.get_thread(42)
		assert raised.value.kind is ForumErrorKind.FORBIDDEN
		assert raised.value.status_code == 403
		assert "secret-token" not in str(raised.value)
		assert "private response" not in str(raised.value)

	run(scenario())


@pytest.mark.unit
def test_malformed_json_is_normalized_without_body() -> None:
	def handler(_: httpx.Request) -> httpx.Response:
		return httpx.Response(200, text="secret-token invalid")

	async def scenario() -> None:
		http_client, forum = client_for(httpx.MockTransport(handler))
		async with http_client:
			result = (await forum.bump_batch([BumpJob("topic-42", 42)]))[0]
		assert result.outcome is BumpOutcome.ERROR
		assert result.error == "Forum API returned an invalid response"

	run(scenario())


@pytest.mark.unit
def test_rate_limit_delays_next_request() -> None:
	delays: list[float] = []
	request_count = 0

	async def sleep(delay: float) -> None:
		delays.append(delay)

	def handler(_: httpx.Request) -> httpx.Response:
		nonlocal request_count
		request_count += 1
		if request_count == 1:
			return httpx.Response(
				200,
				json={
					"thread": {"thread_id": 42, "title": "First"},
					"system_info": {
						"rate_limit": {
							"remaining": 0,
							"reset": NOW.timestamp() + 30,
						}
					},
				},
			)
		return httpx.Response(
			200,
			json={"thread": {"thread_id": 43, "title": "Second"}},
		)

	async def scenario() -> None:
		http_client, forum = client_for(
			httpx.MockTransport(handler),
			sleep=sleep,
		)
		async with http_client:
			await forum.get_thread(42)
			await forum.get_thread(43)
		assert delays == [30.0]

	run(scenario())
