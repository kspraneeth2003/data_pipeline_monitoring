import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

PREFIX = "enc:v1:"


def _key() -> bytes:
    if not settings.connector_secret_key:
        raise RuntimeError(
            "CONNECTOR_SECRET_KEY is not set - generate one with "
            "`python3 -c \"import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())\"`"
        )
    key = base64.b64decode(settings.connector_secret_key)
    if len(key) != 32:
        raise RuntimeError("CONNECTOR_SECRET_KEY must decode to exactly 32 bytes (base64-encoded)")
    return key


def encrypt_secret(plaintext: str) -> str:
    aesgcm = AESGCM(_key())
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_secret(payload: str) -> str:
    if not payload.startswith(PREFIX):
        raise ValueError("Value is not an encrypted secret produced by encrypt_secret()")
    raw = base64.b64decode(payload[len(PREFIX):])
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(_key())
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


def is_encrypted_secret(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)
