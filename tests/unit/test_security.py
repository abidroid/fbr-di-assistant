"""Unit tests for `app.security`: password hashing and JWT helpers."""

from datetime import timedelta

import jwt
import pytest

from app.config import settings
from app.exceptions import InvalidCredentialsError
from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_is_not_the_password_and_is_salted() -> None:
    first = hash_password("correct horse battery")
    second = hash_password("correct horse battery")

    assert first != "correct horse battery"
    assert first.startswith("$argon2")
    assert first != second  # a random salt makes every hash different


def test_verify_password_accepts_right_and_rejects_wrong() -> None:
    hashed = hash_password("correct horse battery")

    assert verify_password("correct horse battery", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_token_round_trip_returns_subject() -> None:
    token = create_access_token("user-123")

    assert decode_access_token(token) == "user-123"


def test_expired_token_is_rejected() -> None:
    token = create_access_token("user-123", expires_delta=timedelta(seconds=-1))

    with pytest.raises(InvalidCredentialsError):
        decode_access_token(token)


def test_tampered_token_is_rejected() -> None:
    header, payload, signature = create_access_token("user-123").split(".")
    # Flip the last signature character so the signature no longer matches.
    tampered = f"{header}.{payload}.{signature[:-1]}{'A' if signature[-1] != 'A' else 'B'}"

    with pytest.raises(InvalidCredentialsError):
        decode_access_token(tampered)


def test_token_signed_with_another_key_is_rejected() -> None:
    attacker_key = "attacker-key-that-is-long-enough-for-hs256"
    forged = jwt.encode({"sub": "user-123", "exp": 9999999999}, attacker_key, algorithm="HS256")

    with pytest.raises(InvalidCredentialsError):
        decode_access_token(forged)


def test_token_without_subject_is_rejected() -> None:
    no_sub = jwt.encode(
        {"exp": 9999999999}, settings.jwt_secret_key.get_secret_value(), algorithm="HS256"
    )

    with pytest.raises(InvalidCredentialsError):
        decode_access_token(no_sub)


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(InvalidCredentialsError):
        decode_access_token("not-a-jwt")
