from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


class SecurityValidationError(ValueError):
    """Raised when a security-sensitive value fails validation."""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise SecurityValidationError("A valid email address is required")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH or len(password) > PASSWORD_MAX_LENGTH:
        raise SecurityValidationError(
            "Password must contain between "
            f"{PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters"
        )
    if not any(character.islower() for character in password):
        raise SecurityValidationError("Password must contain a lowercase character")
    if not any(character.isupper() for character in password):
        raise SecurityValidationError("Password must contain an uppercase character")
    if not any(character.isdigit() for character in password):
        raise SecurityValidationError("Password must contain a digit")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16_384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(derived, bytes.fromhex(digest_hex))
    except (TypeError, ValueError, OverflowError):
        return False


def issue_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def session_expiry(ttl_seconds: int) -> datetime:
    return utc_now() + timedelta(seconds=ttl_seconds)


def new_id() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    email: str
    role: str
    session_id: str
