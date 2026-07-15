import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AES_KEY_BYTES = 32
AES_NONCE_BYTES = 12


@dataclass(frozen=True, slots=True)
class Argon2Parameters:
	time_cost: int = 3
	memory_cost: int = 65536
	parallelism: int = 4
	hash_len: int = AES_KEY_BYTES


@dataclass(frozen=True, slots=True)
class CryptoEnvelope:
	ciphertext: bytes
	nonce: bytes


def generate_data_key() -> bytes:
	return AESGCM.generate_key(bit_length=256)


def derive_kek(
	password: str,
	salt: bytes,
	parameters: Argon2Parameters | None = None,
) -> bytes:
	if not password:
		raise ValueError("Password must not be empty")
	if len(salt) < 16:
		raise ValueError("Salt must contain at least 16 bytes")

	active_parameters = parameters or Argon2Parameters()
	return hash_secret_raw(
		secret=password.encode(),
		salt=salt,
		time_cost=active_parameters.time_cost,
		memory_cost=active_parameters.memory_cost,
		parallelism=active_parameters.parallelism,
		hash_len=active_parameters.hash_len,
		type=Type.ID,
	)


def encrypt(data_key: bytes, plaintext: bytes, context: bytes) -> CryptoEnvelope:
	_validate_aes_key(data_key)
	nonce = secrets.token_bytes(AES_NONCE_BYTES)
	ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, context)
	return CryptoEnvelope(ciphertext=ciphertext, nonce=nonce)


def decrypt(data_key: bytes, envelope: CryptoEnvelope, context: bytes) -> bytes:
	_validate_aes_key(data_key)
	if len(envelope.nonce) != AES_NONCE_BYTES:
		raise ValueError("Nonce must contain 12 bytes")
	return AESGCM(data_key).decrypt(envelope.nonce, envelope.ciphertext, context)


def blind_index(index_key: bytes, value: str) -> bytes:
	if not index_key:
		raise ValueError("Index key must not be empty")
	return hmac.new(index_key, value.encode(), hashlib.sha256).digest()


def wrap_data_key(kek: bytes, data_key: bytes, context: bytes) -> CryptoEnvelope:
	_validate_aes_key(data_key)
	return encrypt(kek, data_key, context)


def unwrap_data_key(kek: bytes, envelope: CryptoEnvelope, context: bytes) -> bytes:
	data_key = decrypt(kek, envelope, context)
	_validate_aes_key(data_key)
	return data_key


def _validate_aes_key(key: bytes) -> None:
	if len(key) != AES_KEY_BYTES:
		raise ValueError("AES-256 key must contain 32 bytes")
