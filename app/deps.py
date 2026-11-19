"""FastAPI dependencies shared by the routes.

Routes ask for what they need by type (``session: SessionDep``,
``user: CurrentUser``) and FastAPI supplies it. Tests replace these with fakes
via ``app.dependency_overrides``.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.exceptions import InvalidCredentialsError
from app.models import User
from app.services import auth_service

# Reads "Authorization: Bearer <token>" and tells Swagger where to log in, which
# enables its Authorize button. auto_error=False: a missing token gives None
# instead of FastAPI's own 401 body, so we can raise our error in the API's shape.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_qdrant(request: Request) -> AsyncQdrantClient:
    """Return the single Qdrant client created in the app's lifespan.

    Args:
        request: The current request, used to reach ``app.state``.

    Returns:
        The shared `AsyncQdrantClient`.
    """
    return request.app.state.qdrant


SessionDep = Annotated[AsyncSession, Depends(get_db)]
QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


async def get_current_user(
    session: SessionDep, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> User:
    """Return the logged-in user, or refuse the request with 401.

    Args:
        session: Database session for this request.
        token: Bearer token from the ``Authorization`` header, or None if absent.

    Returns:
        The user the token was issued for.

    Raises:
        InvalidCredentialsError: If the token is missing, invalid, expired, or
            its user no longer exists (sent with ``WWW-Authenticate: Bearer``).
    """
    if token is None:
        raise InvalidCredentialsError("Not authenticated.")
    return await auth_service.get_user_from_token(session, token)


CurrentUser = Annotated[User, Depends(get_current_user)]
