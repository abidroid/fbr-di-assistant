"""Password hashing (argon2) and JWT access tokens (HS256).

Pure helpers with no database access: the auth service calls them at signup,
login, and on every request that carries a bearer token.
"""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import settings
from app.exceptions import InvalidCredentialsError

# recommended() = argon2id with pwdlib's current safe defaults. argon2 is slow
# and memory-hungry on purpose, so brute-forcing a stolen hash is expensive.
password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plain password for storage.

    Args:
        password: The password the user typed.

    Returns:
        An argon2 hash string that embeds its own random salt and parameters.
    """
    return password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a typed password against a stored hash.

    Args:
        password: The password the user typed at login.
        hashed_password: The hash stored in ``users.hashed_password``.

    Returns:
        True if they match, otherwise False.
    """
    return password_hasher.verify(password, hashed_password)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT that identifies a user.

    Args:
        subject: The user's id, stored in the ``sub`` claim.
        expires_delta: Lifetime of the token; defaults to
            ``settings.access_token_expire_minutes``. Tests pass a negative
            value to create an already-expired token.

    Returns:
        The encoded token ``header.payload.signature``.
    """
    now = datetime.now(UTC)
    lifetime = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "iat": now, "exp": now + lifetime}
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> str:
    """Verify a token's signature and expiry and return its subject.

    Args:
        token: The raw token from the ``Authorization: Bearer`` header.

    Returns:
        The ``sub`` claim (a user id as a string).

    Raises:
        InvalidCredentialsError: If the token is malformed, tampered with,
            signed with another key, expired, or missing ``sub``/``exp``.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            # Pinning the algorithm list blocks "alg: none" and algorithm-swap attacks.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        # One generic error for every failure: the client doesn't need to know
        # which check failed, and telling them would help an attacker.
        raise InvalidCredentialsError() from None
    return payload["sub"]
