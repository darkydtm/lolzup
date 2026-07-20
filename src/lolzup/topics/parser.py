import re
from urllib.parse import unquote, urlsplit

ALLOWED_HOSTS = frozenset({"lolz.live", "zelenka.guru"})
THREAD_ID_PATTERN = re.compile(r"[1-9]\d*")
SLUG_PATTERN = re.compile(r"[^/?#]+")
SLUG_WITH_ID_PATTERN = re.compile(r"[^/?#]+\.(?P<thread_id>[1-9]\d*)")


class InvalidTopicReferenceError(ValueError):
	pass


def parse_topic_reference(value: str) -> int:
	reference = value.strip()
	if THREAD_ID_PATTERN.fullmatch(reference):
		return int(reference)
	if not reference:
		raise InvalidTopicReferenceError("Topic reference must not be empty")

	try:
		parsed = urlsplit(reference)
		port = parsed.port
	except ValueError as error:
		raise InvalidTopicReferenceError("Topic URL is invalid") from error

	if parsed.scheme != "https":
		raise InvalidTopicReferenceError("Topic URL must use HTTPS")
	if parsed.hostname is None or parsed.hostname.casefold() not in ALLOWED_HOSTS:
		raise InvalidTopicReferenceError("Topic URL host is not supported")
	if parsed.username is not None or parsed.password is not None or port is not None:
		raise InvalidTopicReferenceError(
			"Topic URL must not contain credentials or a port"
		)

	segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
	if len(segments) not in {2, 3} or segments[0] != "threads":
		raise InvalidTopicReferenceError("Topic URL path is invalid")

	thread_segment = segments[1]
	match = THREAD_ID_PATTERN.fullmatch(thread_segment)
	if match is not None:
		if len(segments) == 3 and SLUG_PATTERN.fullmatch(segments[2]) is None:
			raise InvalidTopicReferenceError("Topic URL slug is invalid")
		return int(thread_segment)

	if len(segments) != 2:
		raise InvalidTopicReferenceError("Topic URL path is invalid")
	slug_match = SLUG_WITH_ID_PATTERN.fullmatch(thread_segment)
	if slug_match is None:
		raise InvalidTopicReferenceError("Topic URL does not contain a valid thread ID")
	return int(slug_match.group("thread_id"))
