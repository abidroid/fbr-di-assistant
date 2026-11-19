"""Account logic: sign up, log in, and resolve a bearer token to a user.

Routes call these functions; they raise domain exceptions from
`app.exceptions`, which the handler in `app.main` maps to 401 / 409.
"""

import uuid

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import EmailTakenError, InvalidCredentialsError
from app.models import User
from app.schemas import Token, UserCreate
from app.security import create_access_token, decode_access_token, hash_password, verify_password

# Login with an unknown email still runs one argon2 verify against this hash, so
# the response takes as long as a wrong password. Otherwise a fast reply would
# reveal "this email has no account". Computed once, at import.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Find a user by email.

    Args:
        session: Database session for this request.
        email: Email to look up; compared in lower case.

    Returns:
        The user, or None if no account uses this email.
    """
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def signup(session: AsyncSession, data: UserCreate) -> User:
    """Create a new account with a hashed password.

    Args:
        session: Database session for this request.
        data: Validated signup body (email already lower-cased by the schema).

    Returns:
        The saved user, including its generated id and created_at.

    Raises:
        EmailTakenError: If an account with this email already exists.
    """
    if await get_user_by_email(session, data.email) is not None:
        raise EmailTakenError()

    # argon2 is CPU-heavy (tens of ms by design); run it in a worker thread so
    # the event loop keeps serving other requests meanwhile.
    hashed = await to_thread.run_sync(hash_password, data.password)
    user = User(email=data.email, full_name=data.full_name, hashed_password=hashed)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Two signups with the same email can both pass the check above; the
        # database's unique index stops the second, and we report it the same way.
        await session.rollback()
        raise EmailTakenError() from None
    # Load server-side defaults (created_at) so the response can include them.
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> Token:
    """Check login details and issue an access token.

    Args:
        session: Database session for this request.
        email: Email typed at login (the OAuth2 form calls it ``username``).
        password: Password typed at login.

    Returns:
        A bearer token for the user.

    Raises:
        InvalidCredentialsError: If the email is unknown or the password is wrong.
            Both cases give the same message so nobody can probe which emails exist.
    """
    user = await get_user_by_email(session, email)
    stored_hash = user.hashed_password if user else _DUMMY_HASH
    password_ok = await to_thread.run_sync(verify_password, password, stored_hash)
    if user is None or not password_ok:
        raise InvalidCredentialsError("Incorrect email or password.")
    return Token(access_token=create_access_token(str(user.id)))


async def get_user_from_token(session: AsyncSession, token: str) -> User:
    """Resolve a bearer token to the user it was issued for.

    Args:
        session: Database session for this request.
        token: Raw JWT from the ``Authorization`` header.

    Returns:
        The user named in the token's ``sub`` claim.

    Raises:
        InvalidCredentialsError: If the token is invalid or expired, or its user
            no longer exists (for example, the account was deleted).
    """
    subject = decode_access_token(token)
    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        raise InvalidCredentialsError() from None
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentialsError()
    return user
