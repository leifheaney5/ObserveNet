from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from observenet.core.config import get_config

# argon2-cffi defaults are Argon2id with RFC 9106 low-memory parameters.
_hasher = PasswordHasher()

AGENT_TOKEN_PREFIX = "onet_"  # noqa: S105 - a format marker, not a credential


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_agent_token() -> str:
    return AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """Keyed digest of a high-entropy token for storage/lookup (tokens are never stored raw)."""
    key = get_config().secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
