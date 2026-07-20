import logging
import re
import traceback
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"

BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
BOT_TOKEN_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
SENSITIVE_FIELD_PATTERN = re.compile(
	r"(?i)\b(password|passphrase|api[_ -]?token|bot[_ -]?token|"
	r"authorization|ciphertext|wrapped[_ -]?data[_ -]?key|data[_ -]?key)"
	r"(\s*[=:]\s*)([^\s,;}\]]+)"
)
SENSITIVE_KEYS = frozenset(
	{
		"password",
		"passphrase",
		"api_token",
		"bot_token",
		"authorization",
		"ciphertext",
		"wrapped_data_key",
		"data_key",
	}
)


class RedactionFilter(logging.Filter):
	def __init__(self, secrets: Iterable[str] = ()) -> None:
		super().__init__()
		self._secrets = {secret for secret in secrets if secret}

	def add_secret(self, secret: str) -> None:
		if secret:
			self._secrets.add(secret)

	def filter(self, record: logging.LogRecord) -> bool:
		record.msg = self.redact(record.msg)
		record.args = self.redact(record.args)
		if record.exc_info is not None:
			record.exc_text = self.redact(
				"".join(traceback.format_exception(*record.exc_info))
			)
		return True

	def redact(self, value: Any) -> Any:
		if isinstance(value, str):
			return self._redact_text(value)
		if isinstance(value, bytes):
			return REDACTED
		if isinstance(value, Mapping):
			return {
				key: REDACTED
				if str(key).casefold().replace("-", "_") in SENSITIVE_KEYS
				else self.redact(item)
				for key, item in value.items()
			}
		if isinstance(value, tuple):
			return tuple(self.redact(item) for item in value)
		if isinstance(value, list):
			return [self.redact(item) for item in value]
		return value

	def _redact_text(self, value: str) -> str:
		result = BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
		result = BOT_TOKEN_PATTERN.sub(REDACTED, result)
		result = SENSITIVE_FIELD_PATTERN.sub(
			lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
			result,
		)
		for secret in sorted(self._secrets, key=len, reverse=True):
			result = result.replace(secret, REDACTED)
		return result


def configure_logging(
	level: str,
	*,
	secrets: Iterable[str] = (),
) -> RedactionFilter:
	redaction = RedactionFilter(secrets)
	handler = logging.StreamHandler()
	handler.addFilter(redaction)
	handler.setFormatter(
		logging.Formatter(
			"%(asctime)s %(levelname)s %(name)s %(message)s",
		)
	)
	root = logging.getLogger()
	root.handlers.clear()
	root.addHandler(handler)
	root.setLevel(level.upper())
	return redaction
