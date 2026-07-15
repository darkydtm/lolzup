import secrets

import pytest
from cryptography.exceptions import InvalidTag

from lolzup.security.crypto import (
	blind_index,
	decrypt,
	derive_kek,
	encrypt,
	generate_data_key,
	unwrap_data_key,
	wrap_data_key,
)


@pytest.mark.unit
def test_encrypt_round_trip() -> None:
	key = generate_data_key()
	envelope = encrypt(key, b"secret", b"topics:1:title")

	assert decrypt(key, envelope, b"topics:1:title") == b"secret"


@pytest.mark.unit
def test_encrypt_uses_unique_nonces() -> None:
	key = generate_data_key()

	first = encrypt(key, b"secret", b"context")
	second = encrypt(key, b"secret", b"context")

	assert first.nonce != second.nonce
	assert first.ciphertext != second.ciphertext


@pytest.mark.unit
def test_context_prevents_ciphertext_relocation() -> None:
	key = generate_data_key()
	envelope = encrypt(key, b"secret", b"topics:1:title")

	with pytest.raises(InvalidTag):
		decrypt(key, envelope, b"topics:2:title")


@pytest.mark.unit
def test_wrong_key_cannot_decrypt() -> None:
	envelope = encrypt(generate_data_key(), b"secret", b"context")

	with pytest.raises(InvalidTag):
		decrypt(generate_data_key(), envelope, b"context")


@pytest.mark.unit
def test_blind_index_is_stable_and_keyed() -> None:
	key = secrets.token_bytes(32)

	assert blind_index(key, "123") == blind_index(key, "123")
	assert blind_index(key, "123") != blind_index(key, "456")
	assert blind_index(key, "123") != blind_index(secrets.token_bytes(32), "123")


@pytest.mark.unit
def test_data_key_can_be_rewrapped() -> None:
	data_key = generate_data_key()
	old_kek = derive_kek("old password", secrets.token_bytes(16))
	new_kek = derive_kek("new password", secrets.token_bytes(16))
	context = b"secret-envelope:data-key"

	old_envelope = wrap_data_key(old_kek, data_key, context)
	unwrapped = unwrap_data_key(old_kek, old_envelope, context)
	new_envelope = wrap_data_key(new_kek, unwrapped, context)

	assert unwrap_data_key(new_kek, new_envelope, context) == data_key
	with pytest.raises(InvalidTag):
		unwrap_data_key(old_kek, new_envelope, context)


@pytest.mark.unit
def test_empty_password_is_rejected() -> None:
	with pytest.raises(ValueError, match="must not be empty"):
		derive_kek("", secrets.token_bytes(16))
