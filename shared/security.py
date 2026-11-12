"""Password hashing, signed tokens and at-rest encryption.

Each product passes its OWN secret, so tokens and ciphertexts never cross products.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from cryptography.fernet import Fernet, InvalidToken

PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(payload: dict, secret: str, ttl_seconds: int = 8 * 3600) -> str:
    body = dict(payload, exp=int(time.time()) + ttl_seconds)
    raw = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def read_token(token: str, secret: str) -> dict | None:
    try:
        raw, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        body = json.loads(_unb64(raw))
        if body.get("exp", 0) < time.time():
            return None
        return body
    except Exception:
        return None


class Cipher:
    """Fernet (AES-128-CBC + HMAC) with a key derived from a passphrase."""

    def __init__(self, passphrase: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest())
        self._f = Fernet(key)

    def encrypt(self, data: bytes | str) -> bytes:
        if isinstance(data, str):
            data = data.encode()
        return self._f.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self._f.decrypt(token)
        except InvalidToken as exc:  # pragma: no cover
            raise ValueError("Could not decrypt record with this product's key") from exc


def mask_account(acct: str) -> str:
    """Restricted account identifier for display: keep last 4 characters."""
    acct = str(acct)
    return ("•" * max(0, len(acct) - 4)) + acct[-4:]
