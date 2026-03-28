from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from django.conf import settings

TOKEN_PREFIX = "enc:v1:"
_NONCE_SIZE = 16
_TAG_SIZE = 32
_BLOCK_SIZE = 32


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX)


def seal_secret(value: str | None) -> str:
    if not value:
        return ""
    if is_encrypted_secret(value):
        return value

    plaintext = value.encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    key = _candidate_keys()[0]
    ciphertext = _xor_keystream(plaintext, key=key, nonce=nonce)
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")
    return f"{TOKEN_PREFIX}{payload}"


def open_secret(value: str | None) -> str:
    if not value:
        return ""
    if not is_encrypted_secret(value):
        return value

    raw = base64.urlsafe_b64decode(value.removeprefix(TOKEN_PREFIX).encode("ascii"))
    nonce = raw[:_NONCE_SIZE]
    tag = raw[_NONCE_SIZE : _NONCE_SIZE + _TAG_SIZE]
    ciphertext = raw[_NONCE_SIZE + _TAG_SIZE :]

    for key in _candidate_keys():
        expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if hmac.compare_digest(expected_tag, tag):
            plaintext = _xor_keystream(ciphertext, key=key, nonce=nonce)
            return plaintext.decode("utf-8")

    raise ValueError("No se pudo descifrar el secreto almacenado con las claves configuradas.")


def _candidate_keys() -> list[bytes]:
    configured = [
        settings.AUTODOCKER_TOKEN_ENCRYPTION_KEY,
        *getattr(settings, "AUTODOCKER_TOKEN_ENCRYPTION_FALLBACK_KEYS", []),
    ]
    unique = []
    seen = set()
    for item in configured:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        unique.append(hashlib.sha256(item.encode("utf-8")).digest())
    if not unique:
        raise ValueError("No hay claves configuradas para proteger tokens externos.")
    return unique


def _xor_keystream(payload: bytes, *, key: bytes, nonce: bytes) -> bytes:
    chunks = bytearray()
    counter = 0
    for offset in range(0, len(payload), _BLOCK_SIZE):
        block = payload[offset : offset + _BLOCK_SIZE]
        stream = hmac.new(
            key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        chunks.extend(value ^ mask for value, mask in zip(block, stream))
        counter += 1
    return bytes(chunks)
