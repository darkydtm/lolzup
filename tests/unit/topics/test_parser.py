import pytest

from lolzup.topics.parser import InvalidTopicReferenceError, parse_topic_reference


@pytest.mark.unit
@pytest.mark.parametrize(
	"value",
	[
		"5523020",
		" 5523020 ",
		"https://lolz.live/threads/5523020",
		"https://lolz.live/threads/5523020/",
		"https://zelenka.guru/threads/5523020",
		"https://LOLZ.LIVE/threads/5523020?from=bot",
		"https://lolz.live/threads/5523020#latest",
		"https://lolz.live/threads/5523020/topic-slug",
		"https://lolz.live/threads/topic-slug.5523020/",
	],
)
def test_parse_topic_reference_accepts_supported_values(value: str) -> None:
	assert parse_topic_reference(value) == 5523020


@pytest.mark.unit
@pytest.mark.parametrize(
	"value",
	[
		"",
		"0",
		"-1",
		"1.5",
		"topic 5523020",
		"http://lolz.live/threads/5523020",
		"https://example.com/threads/5523020",
		"https://lolz.live.example.com/threads/5523020",
		"https://zelenka.guru.evil.test/threads/5523020",
		"https://user@lolz.live/threads/5523020",
		"https://user:pass@lolz.live/threads/5523020",
		"https://lolz.live:443/threads/5523020",
		"https://lolz.live/topics/5523020",
		"https://lolz.live/x/threads/5523020",
		"https://lolz.live/threads/topic5523020",
		"https://lolz.live/threads/5523020/slug/extra",
		"https://lolz.live/threads/-5523020",
		"https://lolz.live/threads/0",
	],
)
def test_parse_topic_reference_rejects_unsafe_values(value: str) -> None:
	with pytest.raises(InvalidTopicReferenceError):
		parse_topic_reference(value)
