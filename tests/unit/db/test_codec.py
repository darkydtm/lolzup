import asyncio

import pytest
from cryptography.exceptions import InvalidTag

from lolzup.db.models import EncryptionMode
from lolzup.db.repositories import EncryptedFieldCodec
from lolzup.security.crypto import generate_data_key
from lolzup.security.policy import DataCategory, EncryptionPolicy
from lolzup.security.runtime import RuntimeVault


def unlocked_vault() -> RuntimeVault:
	vault = RuntimeVault()
	asyncio.run(vault.unlock(generate_data_key()))
	return vault


@pytest.mark.unit
def test_codec_encrypts_selected_values() -> None:
	codec = EncryptedFieldCodec(EncryptionPolicy(EncryptionMode.FULL), unlocked_vault())

	stored = codec.encode(DataCategory.TOPICS, "topics:1:title", "Title")

	assert stored.plain is None
	assert stored.ciphertext is not None
	assert stored.nonce is not None
	assert (
		codec.decode(
			DataCategory.TOPICS,
			"topics:1:title",
			stored.plain,
			stored.ciphertext,
			stored.nonce,
		)
		== "Title"
	)


@pytest.mark.unit
def test_codec_uses_plain_value_when_encryption_is_disabled() -> None:
	codec = EncryptedFieldCodec(
		EncryptionPolicy(EncryptionMode.DISABLED), unlocked_vault()
	)

	stored = codec.encode(DataCategory.TOPICS, "topics:1:title", "Title")

	assert stored.plain == "Title"
	assert stored.ciphertext is None
	assert stored.nonce is None


@pytest.mark.unit
def test_codec_rejects_relocated_ciphertext() -> None:
	codec = EncryptedFieldCodec(EncryptionPolicy(EncryptionMode.FULL), unlocked_vault())
	stored = codec.encode(DataCategory.TOPICS, "topics:1:title", "Title")

	with pytest.raises(InvalidTag):
		codec.decode(
			DataCategory.TOPICS,
			"topics:2:title",
			stored.plain,
			stored.ciphertext,
			stored.nonce,
		)
