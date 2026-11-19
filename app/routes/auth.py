"""Auth endpoints: POST /auth/signup, POST /auth/login, GET /auth/me."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.deps import CurrentUser, SessionDep
from app.models import User
from app.schemas import Token, UserCreate, UserRead, error_responses
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    summary="Create an account",
    description=(
        "Registers a new user. The email is stored in lower case and must be unique; "
        "the password (8-128 characters) is stored only as an argon2 hash. "
        "Returns the new user without any password data."
    ),
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 422),
)
async def signup(data: UserCreate, session: SessionDep) -> User:
    """Create a user account.

    Args:
        data: Email, full name and password.
        session: Database session for this request.

    Returns:
        The created user (serialised as `UserRead`).
    """
    return await auth_service.signup(session, data)


@router.post(
    "/login",
    summary="Log in and get an access token",
    description=(
        "OAuth2 password flow: send form fields `username` (your email) and `password` "
        "as `application/x-www-form-urlencoded`. Returns a JWT to send as "
        "`Authorization: Bearer <token>`. This is the endpoint Swagger's Authorize button calls."
    ),
    response_model=Token,
    status_code=status.HTTP_200_OK,
    responses=error_responses(401, 422),
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep
) -> Token:
    """Exchange email and password for an access token.

    Args:
        form: OAuth2 form; ``username`` holds the email.
        session: Database session for this request.

    Returns:
        The bearer token.
    """
    return await auth_service.authenticate(session, form.username, form.password)


@router.get(
    "/me",
    summary="Get the current user",
    description="Returns the account that owns the bearer token. Requires a valid token.",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    responses=error_responses(401),
)
async def me(user: CurrentUser) -> User:
    """Return the logged-in user.

    Args:
        user: Resolved from the bearer token by the `CurrentUser` dependency.

    Returns:
        The current user (serialised as `UserRead`).
    """
    return user
