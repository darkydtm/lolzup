import logging
from collections.abc import Mapping

import pytest

from lolzup.logging import REDACTED, RedactionFilter


def record(
	message: object,
	args: tuple[object, ...] | Mapping[str, object] | None = (),
) -> logging.LogRecord:
	return logging.LogRecord(
		"test",
		logging.ERROR,
		__file__,
		1,
		message,
		args,
		None,
	)


@pytest.mark.unit
def test_redacts_authorization_tokens_and_sensitive_fields() -> None:
	redaction = RedactionFilter()
	log_record = record(
		"Authorization: Bearer api-secret password=hunter2 "
		"ciphertext=deadbeef bot_token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
	)

	assert redaction.filter(log_record)
	message = str(log_record.msg)
	assert "api-secret" not in message
	assert "hunter2" not in message
	assert "deadbeef" not in message
	assert "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in message
	assert REDACTED in message


@pytest.mark.unit
def test_redacts_registered_secrets_and_structured_arguments() -> None:
	redaction = RedactionFilter(["runtime-encryption-secret"])
	log_record = record(
		"request failed: %s",
		(
			{
				"authorization": "Bearer token",
				"detail": "runtime-encryption-secret",
				"nested": ["safe", b"ciphertext"],
			},
		),
	)

	redaction.filter(log_record)

	assert "runtime-encryption-secret" not in str(log_record.args)
	assert "Bearer token" not in str(log_record.args)
	assert b"ciphertext" not in str(log_record.args).encode()


@pytest.mark.unit
def test_redacts_exception_traceback() -> None:
	redaction = RedactionFilter(["exception-secret"])
	try:
		raise RuntimeError("failed with exception-secret")
	except RuntimeError:
		log_record = logging.LogRecord(
			"test",
			logging.ERROR,
			__file__,
			1,
			"failure",
			(),
			__import__("sys").exc_info(),
		)

	redaction.filter(log_record)

	assert log_record.exc_text is not None
	assert "exception-secret" not in log_record.exc_text
	assert REDACTED in log_record.exc_text
