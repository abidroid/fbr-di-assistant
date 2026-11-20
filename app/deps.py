"""FastAPI dependencies shared by the routes.

Routes ask for what they need by type (``session: SessionDep``,
``user: CurrentUser``) and FastAPI supplies it. Tests replace these with fakes
via ``app.dependency_overrides``.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import SessionLocal, get_db
from app.exceptions import InvalidCredentialsError
from app.ingestion.embed import DenseEmbedder, SparseEmbedder
from app.ingestion.extract import Extractor
from app.models import User
from app.services import auth_service
from app.services.document_service import Pipeline

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


def get_extractor(request: Request) -> Extractor:
    """Return the extractor created once in the lifespan."""
    return request.app.state.extractor


def get_dense_embedder(request: Request) -> DenseEmbedder:
    """Return the dense embedder (BGE-M3) loaded once in the lifespan."""
    return request.app.state.dense_embedder


def get_sparse_embedder(request: Request) -> SparseEmbedder:
    """Return the sparse embedder (BM25) loaded once in the lifespan."""
    return request.app.state.sparse_embedder


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the factory background tasks use to open their own session.

    The request's session is closed once the response is sent, so a task
    that runs after it needs a fresh one. Tests point this at the test database.
    """
    return SessionLocal


def get_pipeline(
    extractor: Annotated[Extractor, Depends(get_extractor)],
    dense: Annotated[DenseEmbedder, Depends(get_dense_embedder)],
    sparse: Annotated[SparseEmbedder, Depends(get_sparse_embedder)],
    qdrant: QdrantDep,
) -> Pipeline:
    """Bundle everything ingestion needs into one object.

    Args:
        extractor: Reads files into blocks.
        dense: Dense embedder.
        sparse: Sparse embedder.
        qdrant: Vector database client.

    Returns:
        The ingestion pipeline.
    """
    return Pipeline(extractor=extractor, dense=dense, sparse=sparse, qdrant=qdrant)


PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


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
